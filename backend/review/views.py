from __future__ import annotations
from review.integrations.errors import safe_error

from datetime import timedelta
from django.db.models import Q, Avg, Count, F
from django.db.models.functions import TruncDate, Coalesce
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from review.integrations.gitlab import GitLabClient, test_gitlab_connection
from review.integrations.llm import LLMClient
from review.integrations.wecom import WeComClient
from review.engines.registry import engine_catalog, effective_engine, get_platform_engine_config, is_engine_selectable, verify_engine
from review.models import AuditLog, GitLabConfig, LineComment, ModelConfig, NotificationConfig, Project, ProjectPolicy, ProjectRepository, Review, ReviewEngine, ReviewJob, distinct_issue_count
from review.services.mr_issues import resolve_manually, record_publication
from review.serializers import (
    AuditLogSerializer,
    GitLabConfigSerializer,
    LineCommentSerializer,
    ModelConfigSerializer,
    NotificationConfigSerializer,
    ProjectPolicySerializer,
    ProjectSerializer,
    ProjectConnectionCheckSerializer,
    ReviewListSerializer,
    ReviewSerializer,
)
from review.services.gates import evaluate_gate
from review.services.gate_control import evaluate_live, proof_matches, store_assessment
from review.policy_writes import ProjectPolicyWriteSerializer
from review.services.audit import record_audit
from review.permissions import is_platform_admin, visible_projects, requested_project
from review.services.execution_locks import ResourceBusy, resource_lock
from review.services.review_runner import _model_config, _notification_config, create_review_job, requeue_failed_job, trigger_review
from review.services.auto_merge import send_merge_result
from review.audit_views import filter_audit_category
from review.services.mr_coverage import active_mr_jobs, collect_mr_coverage, enabled_repository_paths, enrich_merge_requests, prefetch_review_state
from review.services.project_connection import check_project_connections


def _model_config_for_test(config: ModelConfig | None, data) -> ModelConfig | None:
    """Build an unsaved config from form values so a catalog can be fetched before saving."""
    if not data:
        return config
    source = config or ModelConfig(scope=ModelConfig.Scope.GLOBAL)
    values = {
        'gateway_type': data.get('gateway_type', source.gateway_type),
        'base_url': data.get('base_url', source.base_url),
        'api_key': data.get('api_key') or source.api_key,
        'model': data.get('model', source.model),
        'request_timeout_s': data.get('request_timeout_s', source.request_timeout_s),
        'enabled': data.get('enabled', source.enabled),
    }
    return ModelConfig(
        scope=source.scope,
        project=source.project,
        **values,
    )


def _validate_project_gateway_target(config, data):
    if not isinstance(data, dict):
        raise ValidationError({'detail': '模型配置必须为 JSON 对象'})
    old_url = (config.base_url if config else '') or settings.CCSWITCH_BASE_URL
    raw_url = data.get('base_url', config.base_url if config else '')
    if not isinstance(raw_url, str):
        raise ValidationError({'base_url': '网关地址必须为字符串'})
    new_url = raw_url or settings.CCSWITCH_BASE_URL
    existing_key = (config.api_key if config else '') or settings.CCSWITCH_API_KEY
    if existing_key and new_url.rstrip('/') != old_url.rstrip('/') and not data.get('api_key'):
        raise ValidationError({'detail': '更换项目网关地址时必须提供新密钥，不能向新地址转发继承或已保存的密钥'})


def _notification_config_for_test(config: NotificationConfig | None, data) -> NotificationConfig | None:
    """Build an unsaved notification config for a test send from the form."""
    if not data:
        return config
    source = config or NotificationConfig(scope=NotificationConfig.Scope.GLOBAL)
    return NotificationConfig(
        scope=source.scope,
        project=source.project,
        provider=data.get('provider', source.provider),
        name=data.get('name', source.name),
        webhook_url=data.get('webhook_url') or source.webhook_url,
        language=data.get('language', source.language),
        enabled=data.get('enabled', source.enabled),
        notify_suggestions=data.get('notify_suggestions', source.notify_suggestions),
        notify_blockers=data.get('notify_blockers', source.notify_blockers),
        notify_merge_result=data.get('notify_merge_result', source.notify_merge_result),
    )


def health(request):
    return JsonResponse({'status': 'ok', 'service': 'cw-cr-api'})


@api_view(['GET', 'POST'])
def projects(request):
    if request.method == 'GET':
        queryset = visible_projects(request.user).prefetch_related('reviews')
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(gitlab_project_path__icontains=search))
        return Response({'count': queryset.count(), 'results': ProjectSerializer(queryset, many=True, context={'request': request}).data})
    serializer = ProjectSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    project = serializer.save()
    ProjectPolicy.objects.get_or_create(project=project)
    record_audit(user=request.user, project=project, action='创建项目', target=project.name)
    return Response(ProjectSerializer(project, context={'request': request}).data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH'])
def project_detail(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == 'GET':
        return Response(ProjectSerializer(project, context={'request': request}).data)
    serializer = ProjectSerializer(project, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    repository_fields = ('gitlab_project_id', 'gitlab_project_path', 'gitlab_url', 'repositories')
    if not is_platform_admin(request.user) and any(
        field in serializer.validated_data
        and serializer.validated_data[field] != getattr(project, field)
        for field in repository_fields
    ):
        return Response({'detail': '仓库绑定只能由平台管理员修改'}, status=status.HTTP_403_FORBIDDEN)
    project = serializer.save()
    record_audit(user=request.user, project=project, action='更新项目', target=project.name, metadata={'fields': list(request.data.keys())})
    return Response(ProjectSerializer(project, context={'request': request}).data)


@api_view(['GET', 'PATCH'])
def project_policy(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    policy, _ = ProjectPolicy.objects.get_or_create(project=project)
    if request.method == 'GET':
        return Response(ProjectPolicySerializer(policy).data)
    try:
        with resource_lock(f'gate:{project.pk}'):
            policy.refresh_from_db()
            serializer = ProjectPolicyWriteSerializer(policy, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            policy = serializer.save()
            record_audit(user=request.user, project=project, action='更新项目策略', target=project.name, metadata={'fields': list(request.data.keys())})
            return Response(ProjectPolicySerializer(policy).data)
    except ResourceBusy:
        return Response({'detail': '此项目的门禁操作正在执行，请稍后保存策略'}, status=409)


@api_view(['GET', 'PATCH'])
def review_engine_config(request):
    if request.method == 'GET':
        config = get_platform_engine_config()
        return Response({
            'default_engine': config.default_engine,
            'effective_engine': config.default_engine,
            'engines': engine_catalog(),
        })
    data = request.data
    if not isinstance(data, dict) or set(data) != {'default_engine'}:
        return Response({'detail': '仅允许提交 default_engine'}, status=400)
    engine = data.get('default_engine')
    if engine not in ReviewEngine.values:
        return Response({'default_engine': '未知的评审引擎'}, status=400)
    if not is_engine_selectable(engine):
        return Response({'default_engine': '此引擎尚未安装或未通过全局模型配置验证'}, status=409)
    config = get_platform_engine_config()
    config.default_engine = engine
    config.save(update_fields=('default_engine', 'updated_at'))
    record_audit(user=request.user, action='更新平台默认评审引擎', target=engine)
    return Response({
        'default_engine': engine,
        'effective_engine': engine,
        'engines': engine_catalog(),
    })


@api_view(['POST'])
def review_engine_config_test(request):
    if not isinstance(request.data, dict) or set(request.data) != {'engine'}:
        return Response({'detail': '请指定唯一的 engine'}, status=400)
    engine = str(request.data.get('engine') or '')
    result = verify_engine(engine)
    record_audit(user=request.user, action='验证平台评审引擎', target=engine, metadata=result)
    return Response(result, status=200 if result.get('ok') else 503)


@api_view(['GET', 'PATCH'])
def project_engine_config(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    policy, _ = ProjectPolicy.objects.get_or_create(project=project)
    if request.method == 'GET':
        return Response({
            'inherited': project.engine_inherit,
            'configured_engine': policy.engine,
            'effective_engine': effective_engine(project),
            'engines': engine_catalog(project),
        })
    if request.data == {'inherited': True}:
        project.engine_inherit = True
        project.save(update_fields=('engine_inherit', 'updated_at'))
        record_audit(user=request.user, project=project, action='恢复项目评审引擎全局继承', target=project.name)
        return Response({
            'inherited': True,
            'configured_engine': policy.engine,
            'effective_engine': effective_engine(project),
            'engines': engine_catalog(project),
        })
    if not isinstance(request.data, dict) or set(request.data) != {'engine'}:
        return Response({'detail': '仅允许提交 engine，或单独提交 inherited: true 恢复全局继承'}, status=400)
    engine = request.data.get('engine')
    if engine not in ReviewEngine.values:
        return Response({'engine': '未知的评审引擎'}, status=400)
    if not is_engine_selectable(engine):
        return Response({'engine': '此引擎尚未通过平台验证'}, status=409)
    policy.engine = engine
    policy.save(update_fields=('engine', 'updated_at'))
    project.engine_inherit = False
    project.save(update_fields=('engine_inherit', 'updated_at'))
    record_audit(user=request.user, project=project, action='更新项目评审引擎', target=engine)
    return Response({
        'inherited': False,
        'configured_engine': engine,
        'effective_engine': engine,
        'engines': engine_catalog(project),
    })


@api_view(['GET', 'PATCH'])
def project_model_config(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == 'PATCH' and 'inherited' in request.data:
        if request.data != {'inherited': True}:
            return Response({'detail': '恢复继承只能提交 inherited: true'}, status=400)
        project.model_inherit = True
        project.save(update_fields=('model_inherit', 'updated_at'))
        record_audit(user=request.user, project=project, action='恢复项目模型全局继承', target=project.name)
        source = _model_config(project) or ModelConfig(scope=ModelConfig.Scope.GLOBAL)
        return Response({**ModelConfigSerializer(source).data, 'inherited': True})
    source = _model_config(project)
    if request.method == 'GET':
        source = source or ModelConfig(scope=ModelConfig.Scope.GLOBAL)
        return Response({**ModelConfigSerializer(source).data, 'inherited': project.model_inherit})
    _validate_project_gateway_target(source, request.data)
    config = ModelConfig.objects.filter(project=project).first() or ModelConfig(project=project, scope=ModelConfig.Scope.PROJECT)
    if project.model_inherit and source is not None:
        for field in ["gateway_type","base_url","api_key","model","enabled","request_timeout_s"]:
            setattr(config, field, getattr(source, field))
    if request.method == 'PATCH':
        serializer = ModelConfigSerializer(config, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        config = serializer.save(scope=ModelConfig.Scope.PROJECT, project=project)
        if project.model_inherit:
            project.model_inherit = False
            project.save(update_fields=('model_inherit', 'updated_at'))
        record_audit(user=request.user, project=project, action='更新项目模型配置', target=project.name, metadata={'fields': list(request.data.keys())})
    return Response({**ModelConfigSerializer(config).data, 'inherited': False})


@api_view(['POST'])
def project_model_config_test(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    config = _model_config(project)
    _validate_project_gateway_target(config, request.data)
    config = _model_config_for_test(config, request.data)
    result = LLMClient(config).test_inference() if request.data.get('probe') is True else LLMClient(config).test_connection()
    record_audit(user=request.user, project=project, action='测试模型网关', target=project.name, metadata=result)
    return Response(result, status=status.HTTP_200_OK if result.get('ok') else status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(['GET', 'PATCH'])
def project_notification_config(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == 'PATCH' and 'inherited' in request.data:
        if request.data != {'inherited': True}:
            return Response({'detail': '恢复继承只能提交 inherited: true'}, status=400)
        project.notification_inherit = True
        project.save(update_fields=('notification_inherit', 'updated_at'))
        record_audit(user=request.user, project=project, action='恢复项目通知全局继承', target=project.name)
        source = _notification_config(project) or NotificationConfig(scope=NotificationConfig.Scope.GLOBAL)
        return Response({**NotificationConfigSerializer(source).data, 'inherited': True})
    source = _notification_config(project)
    if request.method == 'GET':
        source = source or NotificationConfig(scope=NotificationConfig.Scope.GLOBAL)
        return Response({**NotificationConfigSerializer(source).data, 'inherited': project.notification_inherit})
    config = NotificationConfig.objects.filter(project=project).first() or NotificationConfig(project=project, scope=NotificationConfig.Scope.PROJECT)
    if project.notification_inherit and source is not None:
        for field in ["provider","name","webhook_url","language","enabled","notify_suggestions","notify_blockers","notify_merge_result"]:
            setattr(config, field, getattr(source, field))
    if request.method == 'PATCH':
        serializer = NotificationConfigSerializer(config, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        config = serializer.save(scope=NotificationConfig.Scope.PROJECT, project=project)
        if project.notification_inherit:
            project.notification_inherit = False
            project.save(update_fields=('notification_inherit', 'updated_at'))
        record_audit(user=request.user, project=project, action='更新项目企业微信配置', target=project.name, metadata={'fields': list(request.data.keys())})
    return Response({**NotificationConfigSerializer(config).data, 'inherited': False})


@api_view(['POST'])
def project_notification_config_test(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    config = _notification_config(project)
    config = _notification_config_for_test(config, request.data)
    try:
        result = WeComClient(config).send_test()
    except Exception as exc:
        result = {'ok': False, 'error': safe_error(exc)}
    record_audit(user=request.user, project=project, action='测试企业微信通知', target=project.name, metadata=result)
    return Response(result, status=status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY)


@api_view(['GET'])
def reviews(request):
    queryset = prefetch_review_state(Review.objects.filter(project__in=visible_projects(request.user)))
    project_id = request.query_params.get('project')
    source = request.query_params.get('source')
    review_status = request.query_params.get('status')
    if project_id:
        queryset = queryset.filter(project_id=project_id)
    if source:
        queryset = queryset.filter(source=source)
    if review_status:
        queryset = queryset.filter(status=review_status)
    return Response({'count': queryset.count(), 'results': ReviewListSerializer(queryset, many=True).data})


@api_view(['GET'])
def project_merge_requests(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    try:
        page = int(request.query_params.get('page', '1'))
        if page < 1:
            raise ValueError
    except ValueError:
        return Response({'detail': '页码必须为正整数'}, status=400)
    state = request.query_params.get('state', 'opened')
    if state not in ('opened', 'merged'):
        return Response({'detail': 'MR 状态只能是 opened 或 merged'}, status=400)
    active_repositories = enabled_repository_paths(project)
    requested_repository = request.query_params.get('repository')
    if not requested_repository and len(active_repositories) > 1:
        return Response({'detail': '多仓库项目必须选择 GitLab 仓库'}, status=400)
    repository_path = requested_repository or active_repositories[0] if active_repositories else ''
    if repository_path not in active_repositories:
        return Response({'detail': '仓库不属于当前项目或已停用'}, status=400)
    try:
        items, has_next = GitLabClient(project, repository_path=repository_path).list_merge_requests(page, state)
        items = enrich_merge_requests(project, repository_path, items)
    except Exception:
        return Response({'detail': '无法读取 GitLab MR，请检查连接、权限和项目路径'}, status=502)
    return Response({'results': items, 'page': page, 'has_next': has_next, 'repository_path': repository_path, 'state': state})


@api_view(['POST'])
def project_connection_check(request):
    serializer = ProjectConnectionCheckSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    public_base = getattr(settings, 'PUBLIC_BASE_URL', '').strip().rstrip('/')
    webhook_path = reverse('gitlab-webhook')
    webhook_url = public_base + webhook_path if public_base else request.build_absolute_uri(webhook_path)
    return Response(check_project_connections(serializer.validated_data['repositories'], webhook_url))


@api_view(['GET'])
def overview_coverage(request):
    return Response(collect_mr_coverage(visible_projects(request.user)))


@api_view(['GET', 'POST'])
def project_reviews(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == 'POST':
        return _trigger_project_review(request, project_id)
    queryset = prefetch_review_state(Review.objects.filter(project=project))
    return Response({'count': queryset.count(), 'results': ReviewListSerializer(queryset, many=True).data})


@api_view(['GET'])
def review_detail(request, review_id):
    review = get_object_or_404(prefetch_review_state(Review.objects.all()), pk=review_id)
    return Response(ReviewSerializer(review).data)


@api_view(['PATCH'])
def review_summary(request, review_id):
    review = get_object_or_404(Review.objects.select_related('project'), pk=review_id)
    summary = request.data.get('summary')
    if not isinstance(summary, str) or not summary.strip():
        return Response({'summary': ['总评不能为空']}, status=status.HTTP_400_BAD_REQUEST)
    review.summary = summary.strip()
    review.save(update_fields=('summary', 'updated_at'))
    try:
        GitLabClient(review.project, repository_path=review.repository_path).create_summary_note(review, update_existing=True)
        review.summary_sync_status = 'synced'
    except Exception as exc:
        review.summary_sync_status = 'failed'
        record_audit(user=request.user, project=review.project,
            action='GitLab 总评同步失败',
            target=f'{review.project.name}!{review.mr_iid}',
            metadata={'review_id': str(review.id), 'error': safe_error(exc), 'manual': True},
        )
    record_audit(user=request.user, project=review.project,
        action='编辑中文总评',
        target=f'{review.project.name}!{review.mr_iid}',
        metadata={'review_id': str(review.id)},
    )
    review.save(update_fields=('summary_sync_status', 'updated_at'))
    return Response(ReviewSerializer(review).data)


@api_view(['POST'])
def notify_review(request, review_id):
    try:
        with resource_lock(f'notification:{review_id}'):
            return _notify_review(review_id, user=request.user)
    except ResourceBusy as exc:
        return Response({'ok': False, 'error': safe_error(exc)}, status=409)


def _notify_review(review_id, user=None):
    review = get_object_or_404(Review.objects.select_related('project'), pk=review_id)
    if review.status not in (Review.Status.COMPLETED, Review.Status.BLOCKED) or not review.summary.strip():
        return Response({'ok': False, 'error': '评审尚未成功完成，不能发送评审结论'}, status=409)
    if review.jobs.filter(status__in=(ReviewJob.Status.QUEUED, ReviewJob.Status.RUNNING)).exists():
        return Response({'ok': False, 'error': '后台任务尚未结束，请等待自动通知完成后再重发'}, status=409)
    config = _notification_config(review.project)
    try:
        review.jobs.update(notification_state='sending')
        result = WeComClient(config).send_review(review)
    except Exception as exc:
        review.jobs.update(notification_state='unknown')
        record_audit(user=user, project=review.project,
            action='企业微信通知失败',
            target=f'{review.project.name}!{review.mr_iid}',
            metadata={'review_id': str(review.id), 'error': safe_error(exc), 'manual': True},
        )
        return Response({'ok': False, 'error': safe_error(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    review.jobs.update(notification_state='done' if result.get('ok') or result.get('skipped') else 'failed')
    record_audit(user=user, project=review.project,
        action='发送企业微信评审通知',
        target=f'{review.project.name}!{review.mr_iid}',
        metadata={'review_id': str(review.id), **result},
    )
    return Response(result)


@api_view(['POST'])
def notify_merge_result(request, review_id):
    review = get_object_or_404(Review.objects.select_related('project'), pk=review_id)
    job = review.jobs.order_by('-created_at').first()
    if job is None or job.merge_status in ('not_requested', 'skipped'):
        return Response({'ok': False, 'error': '当前评审没有可重发的自动合并结果'}, status=409)
    try:
        result = send_merge_result(job, review, force=True)
    except ResourceBusy:
        return Response({'ok': False, 'error': '合并结果通知正在投递，请稍后刷新'}, status=409)
    record_audit(
        user=request.user,
        project=review.project,
        action='重发自动合并结果通知',
        target=f'{review.project.name}!{review.mr_iid}',
        metadata={'review_id': str(review.id), **result},
    )
    return Response(result, status=status.HTTP_200_OK if result.get('status') in ('done', 'skipped') else status.HTTP_502_BAD_GATEWAY)


def _trigger_project_review(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if not project.enabled:
        return Response({'detail': '项目已暂停'}, status=409)
    mode = request.data.get('mode', 'review')
    if mode not in ('review', 'improve'):
        return Response({'detail': 'mode 必须是 review 或 improve'}, status=status.HTTP_400_BAD_REQUEST)
    policy, _ = ProjectPolicy.objects.get_or_create(project=project)
    if mode == 'review' and not policy.command_review:
        return Response({'detail': '项目策略已关闭 /review'}, status=status.HTTP_409_CONFLICT)
    if mode == 'improve' and not policy.command_improve:
        return Response({'detail': '项目策略已关闭 /improve'}, status=status.HTTP_409_CONFLICT)
    payload = dict(request.data)
    try:
        mr_iid = int(payload.get('mr_iid'))
    except (TypeError, ValueError):
        mr_iid = 0
    if mr_iid <= 0:
        return Response({'detail': 'mr_iid 必须是正整数，不能创建无关联 MR 的评审'}, status=status.HTTP_400_BAD_REQUEST)
    payload['mr_iid'] = mr_iid
    active_repositories = list(ProjectRepository.objects.filter(project=project, enabled=True).values_list('path', flat=True))
    if project.gitlab_project_path not in active_repositories and not ProjectRepository.objects.filter(project=project, path=project.gitlab_project_path).exists():
        active_repositories.insert(0, project.gitlab_project_path)
    if not payload.get('repository_path') and len(active_repositories) > 1:
        return Response({'detail': '多仓库项目必须指定 repository_path'}, status=400)
    repository_path = payload.get('repository_path') or (active_repositories[0] if active_repositories else '')
    if not isinstance(repository_path, str):
        return Response({'detail': 'repository_path 必须是仓库路径'}, status=400)
    if repository_path not in active_repositories:
        return Response({'detail': '仓库不属于当前项目或已停用'}, status=400)
    payload['repository_path'] = repository_path
    payload['source'] = Review.Source.REVIEW if mode == 'review' else Review.Source.IMPROVE
    try:
        job, review = trigger_review(project, payload, kind=mode)
    except ResourceBusy:
        return Response({'detail': '此 MR 正在创建评审任务，请刷新状态'}, status=409)
    body = {'job_id': str(job.id), 'job_status': job.status, 'review': ReviewSerializer(review).data if review else None}
    return Response(body, status=status.HTTP_201_CREATED if job.status == ReviewJob.Status.SUCCEEDED else status.HTTP_202_ACCEPTED)


@api_view(['POST'])
def trigger_project_review(request, project_id):
    return _trigger_project_review(request, project_id)


@api_view(['POST'])
def retry_review(request, review_id):
    review = get_object_or_404(Review.objects.select_related('project'), pk=review_id)
    if not review.project.enabled:
        return Response({'detail': '项目已暂停，不能重试评审'}, status=409)
    if active_mr_jobs(review.project, review.repository_path or review.project.gitlab_project_path, {review.mr_iid}):
        return Response({'detail': '该评审已有排队或执行中的任务'}, status=409)
    if review.status == Review.Status.FAILED:
        job = ReviewJob.objects.filter(review=review, status=ReviewJob.Status.FAILED).select_related('project', 'review').order_by('-created_at').first()
        if job is None:
            return Response({'detail': '找不到可恢复的失败任务，请从项目 MR 列表重新发起评审'}, status=409)
        try:
            changed = requeue_failed_job(job)
        except ResourceBusy:
            return Response({'detail': '此 MR 正在创建评审任务，请刷新状态'}, status=409)
        if not changed:
            return Response({'detail': '该 MR 已有排队或执行中的任务，请刷新状态'}, status=409)
        record_audit(user=request.user, project=review.project, action='重试失败评审任务', target=str(job.id), metadata={'review_id': str(review.id)})
        return Response({'job_id': str(job.id), 'review': ReviewSerializer(review).data}, status=202)
    if review.status not in (Review.Status.COMPLETED, Review.Status.BLOCKED):
        return Response({'detail': '评审尚未结束，不能重新执行'}, status=409)
    payload = {
        'event_uuid': f'retry-{review.id}-{timezone.now().timestamp()}',
        'mr_iid': review.mr_iid,
        'repository_path': review.repository_path or review.project.gitlab_project_path,
        'title': review.title,
        'branch': review.branch,
        'gitlab_url': review.gitlab_url,
        'base_sha': review.base_sha,
        'start_sha': review.start_sha,
        'head_sha': review.head_sha,
        'source': review.source,
    }
    mode = 'improve' if review.source == Review.Source.IMPROVE else 'review'
    try:
        job, rerun = trigger_review(review.project, payload, kind=mode)
    except ResourceBusy:
        return Response({'detail': '此 MR 正在创建评审任务，请刷新状态'}, status=409)
    return Response({'job_id': str(job.id), 'review': ReviewSerializer(rerun).data if rerun else None}, status=status.HTTP_202_ACCEPTED)


@api_view(['POST'])
def merge_review(request, review_id):
    review = get_object_or_404(Review, pk=review_id)
    try:
        with resource_lock(f'gate:{review.project_id}'):
            return _merge_review(request, review_id)
    except ResourceBusy:
        return Response({'merged': False, 'reason': '此项目的门禁操作正在执行，请稍后重试'}, status=409)


def _merge_review(request, review_id):
    """Evaluate the merge gate and call GitLab only when the project policy allows it."""
    review = get_object_or_404(Review.objects.select_related('project'), pk=review_id)
    if not review.project.enabled or review.status not in (Review.Status.COMPLETED, Review.Status.BLOCKED):
        return Response({'merged': False, 'reason': '项目已暂停或评审尚未成功完成'}, status=409)
    policy, _ = ProjectPolicy.objects.get_or_create(project=review.project)
    gate = evaluate_gate(review, policy)
    if not gate.passed:
        review.gate_status = gate.status
        review.gate_reason = gate.reason
        review.status = Review.Status.BLOCKED
        review.save(update_fields=('gate_status', 'gate_reason', 'status', 'updated_at'))
        return Response(
            {'status': 'blocked', 'merged': False, 'reason': gate.reason},
            status=status.HTTP_409_CONFLICT,
        )
    if not policy.auto_merge_enabled:
        return Response(
            {'status': 'disabled', 'merged': False, 'reason': '项目策略未启用自动合并门禁'},
            status=status.HTTP_409_CONFLICT,
        )
    if not proof_matches(policy, review):
        return Response(
            {'status': 'dry_run_required', 'merged': False, 'reason': '请先对当前 MR 和提交完成有效的 dry-run'},
            status=status.HTTP_409_CONFLICT,
        )

    if Review.objects.filter(project=review.project, mr_iid=review.mr_iid, created_at__gt=review.created_at).exists():
        return Response({'merged': False, 'reason': '存在更新的评审，请使用最新结果'}, status=409)
    assessment = evaluate_live(review, policy)
    store_assessment(policy, review, assessment)
    if not policy.auto_merge_enabled:
        return Response({'status': 'disabled', 'merged': False, 'reason': '自动合并已关闭'}, status=409)
    if not assessment['passed']:
        return Response(
            {'status': 'blocked', 'merged': False, 'reason': assessment['reason']},
            status=502 if assessment['remote_error'] else 409,
        )
    try:
        result = GitLabClient(review.project, repository_path=review.repository_path).merge(review)
    except Exception as exc:
        record_audit(user=request.user, project=review.project,
            action='自动合并失败',
            target=f'{review.project.name}!{review.mr_iid}',
            metadata={'review_id': str(review.id), 'error': safe_error(exc)},
        )
        return Response(
            {'status': 'failed', 'merged': False, 'reason': safe_error(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    review.gate_status = Review.GateStatus.PASSED
    review.gate_reason = gate.reason
    if result.get('merged'):
        review.status = Review.Status.COMPLETED
    review.save(update_fields=('gate_status', 'gate_reason', 'status', 'updated_at'))
    record_audit(user=request.user, project=review.project,
        action='执行自动合并',
        target=f'{review.project.name}!{review.mr_iid}',
        metadata={'review_id': str(review.id), 'merged': bool(result.get('merged'))},
    )
    return Response({
        'status': 'merged' if result.get('merged') else 'pending',
        'merged': bool(result.get('merged')),
        'message': result.get('message', ''),
    })


@api_view(['POST'])
def publish_comment(request, review_id, comment_id):
    review = get_object_or_404(Review, pk=review_id)
    if not review.project.enabled:
        return Response({'detail': '项目已暂停，不能发布评论'}, status=409)
    comment = get_object_or_404(LineComment, pk=comment_id, review=review)
    if comment.status != LineComment.Status.RESOLVED:
        try:
            discussion = GitLabClient(review.project, repository_path=review.repository_path).create_discussion(review, comment)
        except ResourceBusy:
            return Response({'detail': '评论正在发布，请稍后刷新状态'}, status=409)
        except Exception as exc:
            comment.status = LineComment.Status.FAILED
            comment.error_message = safe_error(exc)
            comment.save(update_fields=('status', 'error_message', 'updated_at'))
            record_audit(user=request.user, project=review.project,
                action='发布行级评论失败',
                target=f'{review.project.name}!{review.mr_iid}',
                metadata={'comment_id': str(comment.id), 'error': safe_error(exc)},
            )
            return Response(LineCommentSerializer(comment).data, status=status.HTTP_502_BAD_GATEWAY)
        comment.discussion_id = str(discussion.get('id', ''))
        comment.status = LineComment.Status.RESOLVED if discussion.get('resolved') is True else LineComment.Status.PUBLISHED
        if comment.status == LineComment.Status.RESOLVED:
            comment.resolved_at = timezone.now()
        comment.published_at = timezone.now()
        comment.error_message = ''
        comment.save(update_fields=('discussion_id', 'status', 'published_at', 'resolved_at', 'error_message', 'updated_at'))
        record_audit(user=request.user, project=review.project, action='发布行级评论', target=f'{review.project.name}!{review.mr_iid}', metadata={'comment_id': str(comment.id)})
    record_publication(comment, comment.discussion_id)
    return Response(LineCommentSerializer(comment).data)


@api_view(['POST'])
def resolve_comment(request, review_id, comment_id):
    review = get_object_or_404(Review, pk=review_id)
    comment = get_object_or_404(LineComment, pk=comment_id, review=review)
    try:
        resolve_manually(review, comment, GitLabClient(review.project, repository_path=review.repository_path))
    except Exception as exc:
        record_audit(user=request.user, project=review.project,
            action='解决行级评论失败',
            target=f'{review.project.name}!{review.mr_iid}',
            metadata={'comment_id': str(comment.id), 'error': safe_error(exc)},
        )
        return Response({'ok': False, 'error': safe_error(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    record_audit(user=request.user, project=review.project, action='解决行级评论', target=f'{review.project.name}!{review.mr_iid}', metadata={'comment_id': str(comment.id)})
    return Response(LineCommentSerializer(comment).data)


@api_view(['GET', 'PATCH'])
def model_config(request):
    config, _ = ModelConfig.objects.get_or_create(scope=ModelConfig.Scope.GLOBAL, project=None)
    if request.method == 'GET':
        return Response(ModelConfigSerializer(config).data)
    serializer = ModelConfigSerializer(config, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    config = serializer.save()
    record_audit(user=request.user, project=None, action='更新模型配置', target='全局配置', metadata={'fields': list(request.data.keys())})
    return Response(ModelConfigSerializer(config).data)


@api_view(['POST'])
def model_config_test(request):
    config, _ = ModelConfig.objects.get_or_create(scope=ModelConfig.Scope.GLOBAL, project=None)
    config = _model_config_for_test(config, request.data)
    result = LLMClient(config).test_inference() if request.data.get('probe') is True else LLMClient(config).test_connection()
    record_audit(user=request.user, project=None, action='测试模型网关', target='全局配置', metadata=result)
    return Response(result, status=status.HTTP_200_OK if result.get('ok') else status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(['GET', 'PATCH'])
def gitlab_config(request):
    config, _ = GitLabConfig.objects.get_or_create(pk=1)
    if request.method == 'GET':
        return Response(GitLabConfigSerializer(config).data)
    serializer = GitLabConfigSerializer(config, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    config = serializer.save()
    record_audit(user=request.user, project=None,
        action='更新 GitLab 连接配置',
        target='全局配置',
        metadata={'fields': list(request.data.keys())},
    )
    return Response(GitLabConfigSerializer(config).data)


@api_view(['POST'])
def gitlab_config_test(request):
    result = test_gitlab_connection(overrides=request.data or None)
    record_audit(user=request.user, project=None, action='测试 GitLab 连接', target='全局配置', metadata=result)
    return Response(result, status=status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY)


@api_view(['GET', 'PATCH'])
def notification_config(request):
    config, _ = NotificationConfig.objects.get_or_create(scope=NotificationConfig.Scope.GLOBAL, project=None)
    if request.method == 'GET':
        return Response(NotificationConfigSerializer(config).data)
    serializer = NotificationConfigSerializer(config, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    config = serializer.save()
    record_audit(user=request.user, project=None, action='更新企业微信配置', target='全局配置', metadata={'fields': list(request.data.keys())})
    return Response(NotificationConfigSerializer(config).data)


@api_view(['POST'])
def notification_config_test(request):
    config, _ = NotificationConfig.objects.get_or_create(scope=NotificationConfig.Scope.GLOBAL, project=None)
    config = _notification_config_for_test(config, request.data)
    try:
        result = WeComClient(config).send_test()
    except Exception as exc:
        result = {'ok': False, 'error': safe_error(exc)}
    record_audit(user=request.user, project=None, action='测试企业微信通知', target='全局配置', metadata=result)
    return Response(result, status=status.HTTP_200_OK if result.get('ok') else status.HTTP_502_BAD_GATEWAY)


@api_view(['GET'])
def project_notification_preview(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    reviews = Review.objects.filter(project=project, status__in=(Review.Status.COMPLETED, Review.Status.BLOCKED))
    review_id = request.query_params.get('review')
    review = get_object_or_404(reviews, pk=review_id) if review_id else reviews.first()
    if review is None:
        return Response({'review_id': None, 'content': '', 'will_send': False})
    config = _notification_config(project)
    blockers = review.blocking_comment_count
    will_send = bool(
        (config is None or config.enabled)
        and (config is None or (config.notify_blockers if blockers else config.notify_suggestions))
    )
    return Response({
        'review_id': str(review.pk), 'repository_path': review.repository_path or project.gitlab_project_path,
        'content': WeComClient.review_content(review), 'will_send': will_send,
    })


@api_view(['GET'])
def overview(request):
    projects = visible_projects(request.user)
    queryset = Review.objects.filter(project__in=projects)
    finished = queryset.filter(status__in=[Review.Status.COMPLETED, Review.Status.BLOCKED])
    dates = [timezone.localdate() - timedelta(days=offset) for offset in range(13, -1, -1)]
    severe_days = dates[-7:]
    all_blockers = LineComment.objects.filter(review__project__in=projects).filter(
        Q(level__in=[LineComment.Level.P0, LineComment.Level.UNRATED])
        | (Q(level=LineComment.Level.P1) & (
            Q(review__project__policy__isnull=True) | Q(review__project__policy__block_on_p1=True)
            | Q(review__project__policy__block_on_p2=True)
        ))
        | Q(level=LineComment.Level.P2, review__project__policy__block_on_p2=True)
    ).exclude(status=LineComment.Status.RESOLVED)
    severe_comments = all_blockers.filter(Q(issue__isnull=True) | Q(id=F('issue__first_comment_id'))).annotate(
        issue_created_at=Coalesce('issue__created_at', 'created_at'),
    ).filter(issue_created_at__date__gte=severe_days[0])
    severe_daily_rows = severe_comments.annotate(day=TruncDate('issue_created_at')).values('day').annotate(total=Count(Coalesce('issue_id', 'id'), distinct=True))
    severe_daily = {row['day']: row['total'] for row in severe_daily_rows}
    recent_severe = severe_comments.select_related('review', 'review__project').order_by('-created_at')[:8]
    current_finished = finished.annotate(live_blockers=Count(Coalesce('comments__issue_id', 'comments__id'), distinct=True, filter=(
        Q(comments__level__in=[LineComment.Level.P0, LineComment.Level.UNRATED])
        | (Q(comments__level=LineComment.Level.P1) & (
            Q(project__policy__isnull=True) | Q(project__policy__block_on_p1=True) | Q(project__policy__block_on_p2=True)
        ))
        | Q(comments__level=LineComment.Level.P2, project__policy__block_on_p2=True)
    ) & ~Q(comments__status=LineComment.Status.RESOLVED)))
    states = list(current_finished.values_list('completed_at', 'live_blockers'))
    daily_completed, daily_blocked = {}, {}
    for completed_at, blockers in states:
        if completed_at is not None:
            day = timezone.localdate(completed_at)
            target = daily_blocked if blockers else daily_completed
            target[day] = target.get(day, 0) + 1
    return Response({
        'projects': projects.filter(enabled=True).count(),
        'reviews': queryset.count(),
        'blockers': distinct_issue_count(all_blockers),
        'completed': sum(count == 0 for _date, count in states),
        'blocked_reviews': sum(count > 0 for _date, count in states),
        'average_duration_ms': finished.aggregate(value=Avg('duration_ms'))['value'],
        'daily_reviews': [daily_completed.get(day, 0) + daily_blocked.get(day, 0) for day in dates],
        'daily_completed': [daily_completed.get(day, 0) for day in dates],
        'daily_blocked': [daily_blocked.get(day, 0) for day in dates],
        'daily_labels': [day.strftime('%m-%d') for day in dates],
        'recent_severe_count': distinct_issue_count(severe_comments),
        'daily_severe_issues': [severe_daily.get(day, 0) for day in severe_days],
        'recent_severe_issues': [
            {
                'id': str(comment.pk), 'review_id': str(comment.review_id),
                'level': comment.level, 'title': comment.title,
                'project': comment.review.project.name, 'mr_iid': comment.review.mr_iid,
                'created_at': comment.created_at.isoformat(),
            }
            for comment in recent_severe
        ],
        'recent_reviews': ReviewListSerializer(prefetch_review_state(queryset)[:10], many=True).data,
    })


@api_view(['GET'])
def system_status(request):
    today = timezone.localdate()
    jobs = ReviewJob.objects.all()
    reviews = Review.objects.all()
    return Response({
        'queue': {
            'queued': jobs.filter(status=ReviewJob.Status.QUEUED).count(),
            'running': jobs.filter(status=ReviewJob.Status.RUNNING).count(),
            'failed': jobs.filter(status=ReviewJob.Status.FAILED).count(),
            'completed_today': jobs.filter(status=ReviewJob.Status.SUCCEEDED, completed_at__date=today).count(),
        },
        'reviews': {
            'total': reviews.count(),
            'failed': reviews.filter(status=Review.Status.FAILED).count(),
            'completed_today': reviews.filter(status=Review.Status.COMPLETED, completed_at__date=today).count(),
        },
        'runtime': {
            'gitlab_timeout_s': settings.GITLAB_REQUEST_TIMEOUT,
            'model_timeout_s': settings.LLM_REQUEST_TIMEOUT,
            'wecom_timeout_s': settings.WECOM_REQUEST_TIMEOUT,
            'max_concurrency': settings.REVIEW_MAX_CONCURRENCY,
            'max_retries': settings.REVIEW_MAX_RETRIES,
        },
        'webhooks_received': AuditLog.objects.filter(action='Webhook 触发评审').count(),
        'audit_logs': AuditLogSerializer(AuditLog.objects.all()[:20], many=True).data,
    })


@api_view(['POST'])
def retry_failed_jobs(request):
    failed_jobs = list(ReviewJob.objects.filter(status=ReviewJob.Status.FAILED, project__enabled=True).select_related('project', 'review'))
    job_ids = []
    for job in failed_jobs:
        try:
            changed = requeue_failed_job(job)
        except ResourceBusy:
            continue
        if changed:
            job_ids.append(str(job.id))
    if job_ids:
        record_audit(user=request.user, project=None, action='重试失败评审任务', target=f'{len(job_ids)} 个任务', metadata={'job_ids': job_ids})
    return Response({'count': len(job_ids), 'job_ids': job_ids, 'status': 'queued'})


@api_view(['GET'])
def audit_logs(request):
    queryset = AuditLog.objects.all()
    if request.query_params.get('project'):
        queryset = queryset.filter(project=requested_project(request))
    return Response(AuditLogSerializer(filter_audit_category(queryset, request).order_by('-created_at', '-id')[:100], many=True).data)
