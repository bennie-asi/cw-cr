"""Project memberships never create accounts or grant platform authority."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from review.auth_views import user_payload
from review.models import Project, ProjectMembership
from review.permissions import is_platform_admin
from review.services.audit import record_audit
from review.services.execution_locks import ResourceBusy, resource_lock


def member_payload(member):
    user = member.user
    identity = user_payload(user)
    return {
        'id': member.pk,
        'user_id': user.pk,
        'username': identity['username'],
        'display_name': identity['display_name'],
        'role': member.role,
        'is_active': user.is_active,
        'platform_role': identity['platform_role'],
    }


def validate_payload(data, allowed, required=()):
    if not isinstance(data, dict):
        raise ValidationError('请求体必须为对象')
    if set(data) - set(allowed):
        raise ValidationError('包含不允许修改的字段')
    if set(required) - set(data):
        raise ValidationError('缺少必填字段')


def validate_role(role):
    if role not in ('admin', 'member'):
        raise ValidationError({'role': '请选择项目管理员或项目成员'})
    return role


def require_manager(user_id, project):
    # Recheck inside the identity lock: request.user may predate role revocation.
    actor = get_object_or_404(get_user_model(), pk=user_id)
    if not actor.is_active:
        raise PermissionDenied('账号已停用')
    if not is_platform_admin(actor) and not ProjectMembership.objects.filter(
        project=project, user=actor, role=ProjectMembership.Role.ADMIN,
    ).exists():
        raise PermissionDenied('需要本项目管理员权限')
    return actor


@api_view(['GET', 'POST'])
def project_members(request, project_id):
    if request.method == 'GET':
        members = ProjectMembership.objects.filter(project_id=project_id).select_related('user').order_by('id')
        return Response([member_payload(member) for member in members])
    validate_payload(request.data, ('username', 'role'), ('username',))
    username = request.data['username']
    if not isinstance(username, str) or not username.strip() or len(username) > 150:
        raise ValidationError({'username': '请输入有效用户名（最多150字符）'})
    role = validate_role(request.data.get('role', 'member'))
    try:
        with resource_lock('identity-management'), transaction.atomic():
            project = get_object_or_404(Project, pk=project_id)
            actor = require_manager(request.user.pk, project)
            user = get_user_model().objects.filter(username=username, is_active=True).first()
            if user is None:
                raise ValidationError({'username': '未找到已启用的内置账号'})
            if ProjectMembership.objects.filter(project=project, user=user).exists():
                raise ValidationError({'username': '该账号已是项目成员'})
            member = ProjectMembership.objects.create(project=project, user=user, role=role)
            record_audit(action='添加项目成员', target=user.username, project=project, user=actor,
                         metadata={'user_id': user.pk, 'role': role})
            return Response(member_payload(member), status=201)
    except ResourceBusy as error:
        return Response({'detail': str(error)}, status=409)


@api_view(['PATCH', 'DELETE'])
def project_member_detail(request, project_id, member_id):
    deleting = request.method == 'DELETE'
    validate_payload(request.data, () if deleting else ('role',), () if deleting else ('role',))
    role = None if deleting else validate_role(request.data['role'])
    try:
        with resource_lock('identity-management'), transaction.atomic():
            project = get_object_or_404(Project, pk=project_id)
            actor = require_manager(request.user.pk, project)
            member = get_object_or_404(ProjectMembership.objects.select_related('user'), pk=member_id, project=project)
            removing_admin = member.role == 'admin' and member.user.is_active and (deleting or role != 'admin')
            if removing_admin and not ProjectMembership.objects.filter(
                project=project, role='admin', user__is_active=True,
            ).exclude(pk=member.pk).exists():
                return Response({'detail': '不能移除或降权最后一名已启用的项目管理员，请先指定替代管理员'}, status=409)
            previous_role = member.role
            if deleting:
                member.delete()
            else:
                member.role = role
                member.save(update_fields=['role'])
            record_audit(action='移除项目成员' if deleting else '修改项目成员角色', target=member.user.username,
                         project=project, user=actor,
                         metadata={'user_id': member.user_id, 'previous_role': previous_role, 'role': role})
            return Response(status=204) if deleting else Response(member_payload(member))
    except ResourceBusy as error:
        return Response({'detail': str(error)}, status=409)
