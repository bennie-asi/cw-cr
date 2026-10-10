"""Persistent project auto-merge switch and optional per-MR diagnostics."""

from collections.abc import Mapping
from uuid import UUID

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from review.models import ProjectPolicy, Review
from review.permissions import requested_project
from review.policy_writes import MergeGateWriteSerializer
from review.services.audit import record_audit
from review.services.execution_locks import ResourceBusy, resource_lock
from review.services.gate_control import evaluate_live, store_assessment


def _gate_data(policy):
    return {
        'project_id': str(policy.project_id), 'enabled': policy.auto_merge_enabled,
        'scope': 'project', 'dry_run_required': False, 'automatic_validation_required': True,
        'dry_run_passed': policy.dry_run_passed,
        'dry_run_completed_at': policy.dry_run_completed_at,
        'dry_run_reason': policy.dry_run_reason, 'assessment': policy.dry_run_assessment,
        'engine': policy.engine, 'block_on_p1': policy.block_on_p1,
        'block_on_p2': policy.block_on_p2, 'blocking_levels': policy.blocking_levels,
    }


@api_view(['GET', 'PATCH'])
def merge_gate(request):
    project = requested_project(request)
    policy, _ = ProjectPolicy.objects.get_or_create(project=project)
    if request.method == 'GET':
        return Response(_gate_data(policy))
    serializer = MergeGateWriteSerializer(policy, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    try:
        with resource_lock(f'gate:{project.pk}'):
            policy.refresh_from_db()
            enabled = serializer.validated_data.get('auto_merge_enabled')
            if enabled is not None:
                policy.auto_merge_enabled = enabled
                policy.save(update_fields=('auto_merge_enabled', 'updated_at'))
                record_audit(user=request.user, project=project, action='更新自动合并门禁', target=project.name,
                             metadata={'enabled': enabled, 'scope': 'project', 'validation': 'automatic_per_review'})
            return Response(_gate_data(policy))
    except ResourceBusy:
        return Response({'detail': '此项目的门禁操作正在执行，请稍后重试'}, status=409)


@api_view(['POST'])
def merge_gate_dry_run(request):
    project = requested_project(request)
    if not isinstance(request.data, Mapping) or set(request.data) != {'review_id'}:
        raise ValidationError({'review_id': '必须且只能提交待验证的 review_id'})
    try:
        review_id = UUID(str(request.data['review_id']))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({'review_id': '请选择有效的评审记录'})
    review = get_object_or_404(Review, project=project, pk=review_id)
    try:
        with resource_lock(f'gate:{project.pk}'):
            policy, _ = ProjectPolicy.objects.get_or_create(project=project)
            result = evaluate_live(review, policy)
            assessment = store_assessment(policy, review, result)
            record_audit(user=request.user, project=project, action='执行门禁 dry-run', target=project.name,
                         metadata={'review_id': str(review.pk), 'mr_iid': review.mr_iid,
                                   'passed': result['passed'], 'reason': result['reason']})
            return Response(assessment, status=502 if result['remote_error'] else 200)
    except ResourceBusy:
        return Response({'detail': '此项目的门禁操作正在执行，请稍后重试'}, status=409)
