"""Platform-only built-in account administration; never expose credentials."""

from functools import wraps

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from review.auth_views import user_payload
from review.member_views import validate_payload
from review.models import ProjectMembership
from review.permissions import is_platform_admin
from review.services.audit import record_audit
from review.services.execution_locks import ResourceBusy, resource_lock


def account_payload(user):
    return {**user_payload(user), 'is_active': user.is_active, 'last_login': user.last_login}


def validate_text(value, name, maximum=150):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValidationError({name: f'请输入有效内容（最多{maximum}字符）'})
    return value


def validate_platform_role(role):
    if role not in ('admin', 'member'):
        raise ValidationError({'platform_role': '请选择平台管理员或普通用户'})
    return role


def set_validated_password(user, password):
    validate_text(password, 'password', maximum=4096)
    try:
        validate_password(password, user)
    except DjangoValidationError as error:
        raise ValidationError({'password': error.messages}) from error
    user.set_password(password)


def identity_write(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        try:
            with resource_lock('identity-management'), transaction.atomic():
                actor = get_object_or_404(get_user_model(), pk=request.user.pk)
                if not is_platform_admin(actor):
                    raise PermissionDenied('需要平台管理员权限')
                return function(request, actor, *args, **kwargs)
        except ResourceBusy as error:
            return Response({'detail': str(error)}, status=409)
    return wrapped


@identity_write
def create_account(request, actor):
    validate_payload(request.data, ('username', 'display_name', 'password', 'platform_role'),
                     ('username', 'display_name', 'password'))
    username = validate_text(request.data['username'], 'username')
    display_name = validate_text(request.data['display_name'], 'display_name')
    role = validate_platform_role(request.data.get('platform_role', 'member'))
    User = get_user_model()
    try:
        User._meta.get_field('username').run_validators(username)
    except DjangoValidationError as error:
        raise ValidationError({'username': error.messages}) from error
    if User.objects.filter(username=username).exists():
        raise ValidationError({'username': '用户名已存在'})
    user = User(username=username, first_name=display_name, is_staff=role == 'admin', is_active=True)
    set_validated_password(user, request.data['password'])
    user.save()
    record_audit(action='创建内置账号', target=user.username, user=actor,
                 metadata={'user_id': user.pk, 'platform_role': role})
    return Response(account_payload(user), status=201)


@api_view(['GET', 'POST'])
def users(request):
    if request.method == 'GET':
        return Response([account_payload(user) for user in get_user_model().objects.order_by('id')])
    return create_account(request)


@api_view(['PATCH'])
@identity_write
def user_detail(request, actor, user_id):
    validate_payload(request.data, ('display_name', 'platform_role', 'is_active'))
    if not request.data:
        raise ValidationError('请提供需要修改的字段')
    user = get_object_or_404(get_user_model(), pk=user_id)
    enabled = request.data.get('is_active', user.is_active)
    if not isinstance(enabled, bool):
        raise ValidationError({'is_active': '启用状态必须是布尔值'})
    role = validate_platform_role(request.data.get('platform_role', 'admin' if user.is_staff or user.is_superuser else 'member'))
    name = validate_text(request.data['display_name'], 'display_name') if 'display_name' in request.data else None
    active_admins = get_user_model().objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True))
    if is_platform_admin(user) and (not enabled or role != 'admin') and not active_admins.exclude(pk=user.pk).exists():
        return Response({'detail': '不能停用或降权最后一名已启用的平台管理员'}, status=409)
    if user.is_active and not enabled:
        for project_id in ProjectMembership.objects.filter(user=user, role='admin').values_list('project_id', flat=True):
            if not ProjectMembership.objects.filter(project_id=project_id, role='admin', user__is_active=True).exclude(user=user).exists():
                return Response({'detail': '该账号是某项目最后一名已启用的管理员，请先为项目指定替代管理员'}, status=409)
    user.is_active = enabled
    if 'platform_role' in request.data:
        user.is_staff = role == 'admin'
        if role == 'member':
            user.is_superuser = False
    if 'display_name' in request.data:
        user.first_name = name
        user.last_name = ''
    user.save(update_fields=['is_active', 'is_staff', 'is_superuser', 'first_name', 'last_name'])
    record_audit(action='修改内置账号', target=user.username, user=actor,
                 metadata={'user_id': user.pk, 'platform_role': role, 'is_active': enabled})
    return Response(account_payload(user))


@api_view(['POST'])
@identity_write
def user_reset_password(request, actor, user_id):
    validate_payload(request.data, ('new_password',), ('new_password',))
    user = get_object_or_404(get_user_model(), pk=user_id)
    if user.pk == actor.pk:
        raise ValidationError('修改本人密码请使用账号安全入口并验证当前密码')
    set_validated_password(user, request.data['new_password'])
    user.save(update_fields=['password'])
    record_audit(action='重置内置账号密码', target=user.username, user=actor, metadata={'user_id': user.pk})
    return Response({'detail': '密码已重置，原有会话将在后续请求时失效'})
