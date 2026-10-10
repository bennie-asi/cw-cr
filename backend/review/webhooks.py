from __future__ import annotations

import hmac
import hashlib
import json
import re

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from review.integrations.gitlab import gitlab_webhook_secret
from review.models import AuditLog, Project, ProjectPolicy, ProjectRepository, Review, ReviewJob
from review.services.review_runner import get_or_create_review_job, run_review_job
from review.services.execution_locks import ResourceBusy


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def gitlab_webhook(request):
    payload = request.data
    if not isinstance(payload, dict) or any(not isinstance(payload.get(key, {}), dict) for key in ('project', 'object_attributes', 'merge_request')):
        return Response({'detail': 'Webhook 字段必须为 JSON 对象'}, status=400)
    project_path = (payload.get('project') or {}).get('path_with_namespace') or payload.get('project_path')
    repository = ProjectRepository.objects.select_related('project').filter(path=project_path).first()
    project = repository.project if repository else Project.objects.filter(gitlab_project_path=project_path).first()
    if project is None:
        return Response({'detail': '项目未接入评审平台'}, status=status.HTTP_404_NOT_FOUND)
    if repository and not repository.enabled:
        return Response({'accepted': False, 'reason': '仓库已停用'}, status=409)
    expected = gitlab_webhook_secret(project)
    received = request.META.get('HTTP_X_GITLAB_TOKEN', '')
    if expected and not hmac.compare_digest(expected, received):
        return Response({'detail': 'Webhook token 无效'}, status=status.HTTP_403_FORBIDDEN)
    if not expected:
        return Response({'detail': '请先配置 Webhook Secret'}, status=503)
    if not project.enabled:
        return Response({'accepted': False, 'reason': '项目已暂停'}, status=409)
    attributes = payload.get('object_attributes') or {}
    object_kind = str(payload.get('object_kind') or '').lower()
    if object_kind not in ('merge_request', 'note'):
        return Response({'accepted': False, 'reason': '非 MR 或 MR 评论事件'})
    policy, _ = ProjectPolicy.objects.get_or_create(project=project)
    job_kind = ReviewJob.Kind.REVIEW
    source = Review.Source.WEBHOOK
    mr = payload.get('merge_request') or {}

    if object_kind == 'note' or payload.get('event_type') == 'note':
        if str(attributes.get('noteable_type') or 'MergeRequest').lower() not in ('mergerequest', 'merge_request'):
            return Response({'accepted': False, 'reason': 'note 不是 Merge Request 评论'})
        command_match = re.match(r'^\s*/(review|improve)(?:\s|$)', str(attributes.get('note') or ''), flags=re.IGNORECASE)
        if command_match is None:
            return Response({'accepted': False, 'reason': '未发现 /review 或 /improve 命令'})
        command = command_match.group(1).lower()
        if command == 'review':
            if not policy.command_review:
                return Response({'accepted': False, 'reason': '项目策略已关闭 /review'}, status=status.HTTP_409_CONFLICT)
            job_kind = ReviewJob.Kind.REVIEW
            source = Review.Source.REVIEW
        else:
            if not policy.command_improve:
                return Response({'accepted': False, 'reason': '项目策略已关闭 /improve'}, status=status.HTTP_409_CONFLICT)
            job_kind = ReviewJob.Kind.IMPROVE
            source = Review.Source.IMPROVE
        mr_iid = mr.get('iid') or attributes.get('noteable_iid') or attributes.get('iid')
        if not mr_iid:
            return Response({'accepted': False, 'reason': '评论事件缺少 MR IID'}, status=status.HTTP_400_BAD_REQUEST)
        enriched_payload = {
            **payload,
            'mr_iid': mr_iid,
            'title': mr.get('title', ''),
            'branch': mr.get('source_branch', ''),
            'gitlab_url': mr.get('url', ''),
        }
    else:
        if not policy.webhook_trigger:
            return Response(
                {'accepted': False, 'reason': '项目策略已关闭 MR 自动触发'},
                status=status.HTTP_409_CONFLICT,
            )
        action = attributes.get('action', 'open')
        if action in ('close', 'merge'):
            return Response({'accepted': False, 'reason': f'action={action}'})
        if not attributes.get('iid'):
            return Response({'accepted': False, 'reason': 'Merge Request 事件缺少 MR IID'}, status=status.HTTP_400_BAD_REQUEST)
        enriched_payload = {**payload, 'mr_iid': attributes.get('iid')}

    try:
        raw_iid = enriched_payload.get('mr_iid')
        if isinstance(raw_iid, bool) or not str(raw_iid).isdigit() or int(raw_iid) <= 0:
            raise ValueError
        enriched_payload['mr_iid'] = int(raw_iid)
    except (TypeError, ValueError):
        return Response({'detail': 'MR IID 必须是正整数'}, status=400)
    event_uuid = request.META.get('HTTP_X_GITLAB_EVENT_UUID') or payload.get('event_uuid')
    if not event_uuid:
        last_commit = (attributes.get('last_commit') or {}).get('id') if isinstance(attributes.get('last_commit'), dict) else None
        event_marker = last_commit or attributes.get('updated_at') or attributes.get('note_id')
        if not event_marker:
            event_marker = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:24]
        event_uuid = f'{project_path}:{enriched_payload.get("mr_iid")}:{event_marker}'
    enriched_payload = {**enriched_payload, 'event_uuid': event_uuid, 'source': source, 'repository_path': project_path}
    try:
        job, is_new_event = get_or_create_review_job(project, enriched_payload, job_kind)
    except ResourceBusy:
        return Response({'accepted': False, 'reason': '此 MR 正在创建评审任务，请稍后重试'}, status=409)
    if is_new_event:
        AuditLog.objects.create(
            project=project,
            action='Webhook 触发评审',
            target=f'{project.name}!{enriched_payload.get("mr_iid")}',
            metadata={'event_uuid': str(event_uuid), 'kind': job_kind, 'source': source},
        )
    existing = job.review
    if job.status == ReviewJob.Status.SUCCEEDED and existing:
        return Response({'accepted': True, 'job_id': str(job.id), 'review_id': str(existing.id), 'status': job.status})
    review = run_review_job(job.id) if settings.REVIEW_EXECUTE_INLINE and is_new_event else existing
    if settings.REVIEW_EXECUTE_INLINE:
        job.refresh_from_db()
    return Response(
        {'accepted': True, 'job_id': str(job.id), 'review_id': str(review.id) if review else None, 'status': job.status},
        status=status.HTTP_201_CREATED if job.status == ReviewJob.Status.SUCCEEDED else status.HTTP_202_ACCEPTED,
    )
