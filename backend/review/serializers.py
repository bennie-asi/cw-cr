from rest_framework import serializers
from django.conf import settings
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from urllib.parse import urlsplit
from review.integrations.llm import LLMClient
from review.permissions import is_platform_admin
from review.services.mr_coverage import active_mr_jobs
from review.services.finding_locations import comment_location
from review.services.gates import evaluate_gate

from .models import (
    AuditLog,
    GitLabConfig,
    LineComment,
    ModelConfig,
    NotificationConfig,
    Project,
    ProjectRepository,
    ProjectPolicy,
    GlobalPromptConfig,
    Review,
    ReviewJob,
    distinct_issue_count,
)


class ProjectPolicySerializer(serializers.ModelSerializer):
    effective_prompts = serializers.SerializerMethodField()
    default_prompts = serializers.SerializerMethodField()

    def get_effective_prompts(self, obj):
        defaults = self.get_default_prompts(obj)
        return {field: getattr(obj, field).strip() or defaults[field] for field in defaults}

    def get_default_prompts(self, obj):
        if not hasattr(self, '_global_prompts'):
            self._global_prompts, _ = GlobalPromptConfig.objects.get_or_create(pk=1)
        return {field: getattr(self._global_prompts, field)
                for field in ('security_prompt', 'reliability_prompt', 'style_prompt')}

    class Meta:
        model = ProjectPolicy
        exclude = ('project', 'created_at', 'updated_at')
        read_only_fields = ('dry_run_passed', 'dry_run_completed_at', 'dry_run_reason', 'dry_run_assessment')


class PreserveBlankCredentialsMixin:
    credential_fields = ()

    def update(self, instance, validated_data):
        validated_data = {name: value for name, value in validated_data.items()
                          if name not in self.credential_fields or value != ''}
        return super().update(instance, validated_data)


class ModelConfigSerializer(PreserveBlankCredentialsMixin, serializers.ModelSerializer):
    credential_fields = ('api_key',)
    api_key_masked = serializers.CharField(read_only=True)

    class Meta:
        model = ModelConfig
        fields = (
            'id', 'scope', 'project', 'gateway_type', 'base_url', 'model',
            'api_key', 'api_key_masked', 'enabled', 'request_timeout_s', 'created_at', 'updated_at',
        )
        extra_kwargs = {'api_key': {'write_only': True}}
        read_only_fields = ('scope', 'project')


class GitLabConfigSerializer(PreserveBlankCredentialsMixin, serializers.ModelSerializer):
    credential_fields = ('api_token', 'webhook_secret')
    api_token_masked = serializers.SerializerMethodField()
    api_token_configured = serializers.SerializerMethodField()
    webhook_secret_configured = serializers.SerializerMethodField()

    class Meta:
        model = GitLabConfig
        fields = (
            'id', 'base_url', 'api_token', 'api_token_masked', 'api_token_configured',
            'webhook_secret', 'webhook_secret_configured', 'enabled', 'created_at', 'updated_at',
        )
        extra_kwargs = {
            'api_token': {'write_only': True, 'required': False, 'allow_blank': True},
            'webhook_secret': {'write_only': True, 'required': False, 'allow_blank': True},
        }

    def get_api_token_configured(self, obj: GitLabConfig) -> bool:
        return bool(obj.api_token or settings.GITLAB_TOKEN)

    def get_webhook_secret_configured(self, obj: GitLabConfig) -> bool:
        return bool(obj.webhook_secret or settings.GITLAB_WEBHOOK_SECRET)

    def get_api_token_masked(self, obj: GitLabConfig) -> str:
        return obj.api_token_masked if obj.api_token else ('环境变量已配置' if settings.GITLAB_TOKEN else '')


class NotificationConfigSerializer(PreserveBlankCredentialsMixin, serializers.ModelSerializer):
    credential_fields = ('webhook_url',)
    webhook_url = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=200,
        validators=[URLValidator(schemes=['http', 'https'], message='请输入有效的 HTTP 或 HTTPS Webhook 地址')],
    )
    webhook_configured = serializers.SerializerMethodField()

    class Meta:
        model = NotificationConfig
        read_only_fields = ('scope', 'project')
        fields = (
            'id', 'scope', 'project', 'provider', 'name', 'webhook_url',
            'webhook_configured', 'language', 'enabled', 'notify_suggestions',
            'notify_blockers', 'notify_merge_result', 'created_at', 'updated_at',
        )

    def get_webhook_configured(self, obj: NotificationConfig) -> bool:
        return bool(obj.enabled and (obj.webhook_url or (settings.WECOM_WEBHOOK_URL if obj.scope == NotificationConfig.Scope.GLOBAL else '')))


class LineCommentSerializer(serializers.ModelSerializer):
    level_label = serializers.CharField(source='get_level_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    is_blocking = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()

    class Meta:
        model = LineComment
        fields = (
            'id', 'issue_id', 'external_id', 'file_path', 'old_line', 'new_line', 'level',
            'level_label', 'title', 'body', 'anchor_code', 'location', 'suggestion', 'status', 'status_label', 'is_blocking',
            'discussion_id', 'error_message', 'published_at', 'resolved_at',
        )
        read_only_fields = ('issue_id', 'anchor_code', 'location', 'discussion_id', 'error_message', 'published_at', 'resolved_at')

    def get_location(self, obj: LineComment) -> dict:
        return comment_location(obj)

    def get_is_blocking(self, obj: LineComment) -> bool:
        return obj.level in obj.review.blocking_levels and obj.status != LineComment.Status.RESOLVED


class ReviewSerializer(serializers.ModelSerializer):
    execution_state = serializers.SerializerMethodField()
    generation_state = serializers.SerializerMethodField()
    publication_state = serializers.SerializerMethodField()
    notification_state = serializers.SerializerMethodField()
    merge_status = serializers.SerializerMethodField()
    merge_notification_state = serializers.SerializerMethodField()
    merge_attempts = serializers.SerializerMethodField()
    merge_error = serializers.SerializerMethodField()

    def get_notification_state(self, obj):
        job = self._latest_job(obj)
        return job.notification_state if job else 'not_requested'

    def get_execution_state(self, obj):
        # A newly queued MR job can exist before its new Review record is created.
        # Keep historical generation/publication phases separate from MR availability.
        cache = getattr(self, '_active_job_cache', None)
        if cache is None:
            cache = self._active_job_cache = {}
        path = obj.repository_path or obj.project.gitlab_project_path
        key = (obj.project_id, path)
        if key not in cache:
            cache[key] = active_mr_jobs(obj.project, path)
        active = cache[key].get(obj.mr_iid)
        if active:
            return active.status
        job = self._latest_job(obj)
        return job.status if job else 'not_requested'

    def _latest_job(self, obj):
        cache = getattr(self, '_job_cache', None)
        if cache is None:
            cache = self._job_cache = {}
        if obj.pk not in cache:
            prefetched = getattr(obj, '_prefetched_objects_cache', {})
            cache[obj.pk] = next(iter(prefetched['jobs']), None) if 'jobs' in prefetched else obj.jobs.order_by('-created_at', '-id').first()
        return cache[obj.pk]

    def get_generation_state(self, obj):
        if obj.status in (Review.Status.COMPLETED, Review.Status.BLOCKED):
            return 'completed'
        job = self._latest_job(obj)
        if job and isinstance(job.analysis_result, dict):
            return 'completed'
        if job and job.status in ('queued', 'running'):
            return job.status
        return obj.status if obj.status in (Review.Status.QUEUED, Review.Status.RUNNING, Review.Status.FAILED) else 'unknown'

    def get_publication_state(self, obj):
        comments = list(obj.comments.all())
        job = self._latest_job(obj)
        incomplete = obj.summary_sync_status in ('pending', 'failed') or any(comment.status in (LineComment.Status.OPEN, LineComment.Status.FAILED) for comment in comments)
        if incomplete and job and isinstance(job.analysis_result, dict):
            if job.status == 'running':
                return 'running'
            if job.status == 'queued':
                return 'pending'
            if job.status == 'failed':
                return 'failed'
        if obj.summary_sync_status == 'failed' or any(comment.status == LineComment.Status.FAILED for comment in comments):
            return 'failed'
        if (obj.summary_sync_status not in ('synced', 'pending')
                or any(comment.status not in LineComment.Status.values for comment in comments)):
            return 'unknown'
        if obj.summary_sync_status == 'pending' or any(comment.status == LineComment.Status.OPEN for comment in comments):
            return 'pending'
        return 'completed'

    def get_merge_status(self, obj):
        job = self._latest_job(obj)
        return job.merge_status if job else 'not_requested'

    def get_merge_notification_state(self, obj):
        job = self._latest_job(obj)
        return job.merge_notification_state if job else 'not_requested'

    def get_merge_attempts(self, obj):
        job = self._latest_job(obj)
        return job.merge_attempts if job else 0

    def get_merge_error(self, obj):
        job = self._latest_job(obj)
        return job.merge_error if job else ''

    project_name = serializers.CharField(source='project.name', read_only=True)
    source_label = serializers.CharField(source='get_source_display', read_only=True)
    status_label = serializers.SerializerMethodField()
    gate_status = serializers.SerializerMethodField()
    gate_status_label = serializers.SerializerMethodField()
    gate_reason = serializers.SerializerMethodField()
    comments = serializers.SerializerMethodField()

    def get_comments(self, obj):
        comments = list({comment.issue_id or comment.pk: comment for comment in obj.comments.all()}.values())
        return LineCommentSerializer(comments, many=True, context=self.context).data
    comment_count = serializers.IntegerField(read_only=True)
    blocking_comment_count = serializers.IntegerField(read_only=True)

    def get_status_label(self, obj):
        if obj.status in (Review.Status.COMPLETED, Review.Status.BLOCKED):
            return '有阻断' if obj.blocking_comment_count else '已完成'
        return obj.get_status_display()

    def _current_gate(self, obj):
        if obj.status not in (Review.Status.COMPLETED, Review.Status.BLOCKED):
            return None
        if not hasattr(self, '_gate_results'):
            self._gate_results = {}
        if obj.pk not in self._gate_results:
            self._gate_results[obj.pk] = evaluate_gate(obj)
        return self._gate_results[obj.pk]

    def get_gate_status(self, obj):
        gate = self._current_gate(obj)
        return gate.status if gate else obj.gate_status

    def get_gate_status_label(self, obj):
        return dict(Review.GateStatus.choices)[self.get_gate_status(obj)]

    def get_gate_reason(self, obj):
        gate = self._current_gate(obj)
        return gate.reason if gate else obj.gate_reason

    class Meta:
        model = Review
        fields = (
            'id', 'project', 'project_name', 'repository_path', 'mr_iid', 'title', 'branch', 'source', 'source_label',
            'status', 'status_label', 'execution_state', 'generation_state', 'publication_state', 'gate_status', 'gate_status_label', 'gate_reason',
            'summary', 'recheck_result', 'summary_sync_status', 'notification_state', 'merge_status', 'merge_notification_state', 'merge_attempts', 'merge_error',
            'diff_text', 'changed_files', 'additions', 'deletions', 'gitlab_url',
            'base_sha', 'start_sha', 'head_sha', 'engine_id', 'engine_version', 'engine_model', 'duration_ms',
            'error_message', 'started_at', 'completed_at', 'created_at', 'updated_at',
            'comments', 'comment_count', 'blocking_comment_count',
        )
        read_only_fields = fields


class ReviewListSerializer(ReviewSerializer):
    comments = None

    class Meta(ReviewSerializer.Meta):
        fields = tuple(field for field in ReviewSerializer.Meta.fields if field != 'comments')


class RepositoryInputSerializer(serializers.Serializer):
    path = serializers.CharField(max_length=255)
    gitlab_project_id = serializers.CharField(max_length=80, required=False, allow_blank=True)
    gitlab_url = serializers.URLField(required=False, allow_blank=True)

    def validate(self, attrs):
        path = attrs['path'].strip().strip('/')
        if path.startswith(('http://', 'https://')):
            try:
                parsed = urlsplit(path)
            except ValueError:
                raise serializers.ValidationError({'path': 'GitLab 项目地址格式无效'})
            attrs['gitlab_url'] = attrs.get('gitlab_url') or f'{parsed.scheme}://{parsed.netloc}'
            path = parsed.path.strip('/')
        if path.endswith('.git'):
            path = path[:-4]
        if not path or '/' not in path:
            raise serializers.ValidationError({'path': '请输入 group/project 路径或完整 GitLab 项目地址'})
        attrs['path'] = path
        return attrs


class ProjectConnectionCheckSerializer(serializers.Serializer):
    repositories = RepositoryInputSerializer(many=True, allow_empty=False, max_length=10)

    def validate_repositories(self, repositories):
        paths = [repository['path'] for repository in repositories]
        if len(paths) != len(set(paths)):
            raise serializers.ValidationError('仓库路径不能重复')
        return repositories


class ProjectSerializer(PreserveBlankCredentialsMixin, serializers.ModelSerializer):
    credential_fields = ('webhook_secret',)
    repositories = RepositoryInputSerializer(many=True, required=False, write_only=True)
    current_user_role = serializers.SerializerMethodField()
    webhook_secret = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=255)
    policy = serializers.SerializerMethodField()
    model = serializers.SerializerMethodField()
    notification = serializers.SerializerMethodField()
    reviews_count = serializers.SerializerMethodField()
    blockers = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    status_reasons = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = (
            'id', 'name', 'gitlab_project_path', 'gitlab_project_id', 'gitlab_url', 'repositories',
            'owner', 'enabled', 'model_inherit', 'notification_inherit', 'engine_inherit', 'webhook_secret', 'status',
            'status_reasons',
            'reviews_count', 'blockers', 'policy', 'model', 'notification',
            'created_at', 'updated_at', 'current_user_role',
        )
        extra_kwargs = {'gitlab_project_path': {'required': False}}

    def validate(self, attrs):
        repositories = attrs.get('repositories')
        if repositories is None:
            if self.instance is None and not attrs.get('gitlab_project_path'):
                raise serializers.ValidationError({'repositories': '至少配置一个 GitLab 仓库'})
            raw_path = attrs.get('gitlab_project_path')
            if raw_path and raw_path.startswith(('http://', 'https://')):
                parsed = urlsplit(raw_path)
                attrs['gitlab_project_path'] = parsed.path.strip('/').removesuffix('.git')
                attrs['gitlab_url'] = attrs.get('gitlab_url') or f'{parsed.scheme}://{parsed.netloc}'
            return attrs
        if not repositories:
            raise serializers.ValidationError({'repositories': '至少保留一个 GitLab 仓库'})
        paths = [item['path'] for item in repositories]
        if len(paths) != len(set(paths)):
            raise serializers.ValidationError({'repositories': '仓库路径不能重复'})
        for path in paths:
            existing_repo = ProjectRepository.objects.filter(path=path).first()
            existing_project = Project.objects.filter(gitlab_project_path=path).first()
            if ((existing_repo and (self.instance is None or existing_repo.project_id != self.instance.pk))
                    or (existing_project and (self.instance is None or existing_project.pk != self.instance.pk))):
                raise serializers.ValidationError({'repositories': f'{path} 已绑定其他项目'})
        attrs['gitlab_project_path'] = repositories[0]['path']
        attrs['gitlab_project_id'] = repositories[0].get('gitlab_project_id', '')
        attrs['gitlab_url'] = repositories[0].get('gitlab_url', '')
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        repositories = validated_data.pop('repositories', None)
        project = super().create(validated_data)
        self._sync_repositories(project, repositories or [{
            'path': project.gitlab_project_path,
            'gitlab_project_id': project.gitlab_project_id,
            'gitlab_url': project.gitlab_url,
        }])
        return project

    @transaction.atomic
    def update(self, instance, validated_data):
        repositories = validated_data.pop('repositories', None)
        old_path = instance.gitlab_project_path
        project = super().update(instance, validated_data)
        if repositories is not None:
            self._sync_repositories(project, repositories)
        elif old_path != project.gitlab_project_path:
            self._sync_repositories(project, [{
                'path': project.gitlab_project_path,
                'gitlab_project_id': project.gitlab_project_id,
                'gitlab_url': project.gitlab_url,
            }])
        return project

    @staticmethod
    def _sync_repositories(project, repositories):
        active_paths = []
        for position, item in enumerate(repositories):
            path = item['path']
            active_paths.append(path)
            row = ProjectRepository.objects.select_for_update().filter(path=path).first()
            if row and row.project_id != project.pk:
                raise serializers.ValidationError({'repositories': f'{path} 已绑定其他项目'})
            values = {
                'position': position, 'enabled': True,
                'gitlab_project_id': item.get('gitlab_project_id', ''),
                'gitlab_url': item.get('gitlab_url', ''),
            }
            if row:
                for field, value in values.items():
                    setattr(row, field, value)
                row.save(update_fields=(*values, 'updated_at'))
            else:
                try:
                    ProjectRepository.objects.create(project=project, path=path, **values)
                except IntegrityError as exc:
                    raise serializers.ValidationError({'repositories': f'{path} 已绑定其他项目'}) from exc
        ProjectRepository.objects.filter(project=project).exclude(path__in=active_paths).update(enabled=False)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        rows = list(instance.repositories.filter(enabled=True))
        data['repositories'] = [{
            'path': row.path, 'gitlab_project_id': row.gitlab_project_id,
            'gitlab_url': row.gitlab_url,
        } for row in rows] or [{
            'path': instance.gitlab_project_path,
            'gitlab_project_id': instance.gitlab_project_id,
            'gitlab_url': instance.gitlab_url,
        }]
        return data

    def get_current_user_role(self, obj: Project):
        request = self.context.get('request')
        if request is None or not request.user.is_authenticated or not request.user.is_active:
            return None
        if is_platform_admin(request.user):
            return 'admin'
        return obj.memberships.filter(user=request.user).values_list('role', flat=True).first()

    def get_policy(self, obj: Project):
        policy, _ = ProjectPolicy.objects.get_or_create(project=obj)
        return ProjectPolicySerializer(policy).data

    def get_model(self, obj: Project):
        inherited = obj.model_inherit
        config = (
            ModelConfig.objects.filter(scope=ModelConfig.Scope.GLOBAL, project__isnull=True).first()
            if inherited else getattr(obj, 'model_config', None)
        )
        if not config:
            return {'inherited': inherited, 'configured': False, 'model': ''}
        configured = LLMClient(config).remote_enabled
        return {
            **ModelConfigSerializer(config).data,
            'inherited': inherited,
            'configured': configured,
            'model': config.model or settings.CCSWITCH_MODEL,
        }

    def get_notification(self, obj: Project):
        inherited = obj.notification_inherit
        config = (
            NotificationConfig.objects.filter(scope=NotificationConfig.Scope.GLOBAL, project__isnull=True).first()
            if inherited else getattr(obj, 'notification_config', None)
        )
        if not config:
            return {'inherited': inherited, 'configured': bool(inherited and settings.WECOM_WEBHOOK_URL)}
        return {
            **NotificationConfigSerializer(config).data,
            'inherited': inherited,
            'configured': bool(config.enabled and (config.webhook_url or (settings.WECOM_WEBHOOK_URL if inherited else ''))),
        }

    def get_reviews_count(self, obj: Project) -> int:
        return obj.reviews.count()

    def get_blockers(self, obj: Project) -> int:
        try:
            levels = obj.policy.blocking_levels
        except ProjectPolicy.DoesNotExist:
            levels = [LineComment.Level.P0, LineComment.Level.P1, LineComment.Level.UNRATED]
        return distinct_issue_count(LineComment.objects.filter(review__project=obj, level__in=levels).exclude(status=LineComment.Status.RESOLVED))

    def get_status_reasons(self, obj: Project) -> list[str]:
        if not obj.enabled:
            return ['项目已由管理员暂停；Webhook 和手动评审均不执行']
        reasons = []
        gitlab_config = GitLabConfig.objects.filter(pk=1).first()
        if gitlab_config and not gitlab_config.enabled:
            reasons.append('平台 GitLab 连接已关闭')
        gitlab_url = obj.gitlab_url or (gitlab_config.base_url if gitlab_config else '') or settings.GITLAB_URL
        gitlab_token = (gitlab_config.api_token if gitlab_config else '') or settings.GITLAB_TOKEN
        if not gitlab_url or not gitlab_token:
            reasons.append('请在系统设置配置 GitLab 地址与 API Token')
        model = self.get_model(obj)
        if not model.get('configured'):
            reasons.append('请配置可调用的评审模型')
        if not obj.notification_inherit:
            notification = NotificationConfig.objects.filter(project=obj).first()
            if notification is None:
                reasons.append('项目通知覆盖尚未保存；可配置机器人或明确关闭通知')
            elif notification.enabled and not notification.webhook_url:
                reasons.append('项目通知已开启，但机器人 Webhook 地址为空')
        if not obj.repositories.filter(enabled=True).exists() and not obj.gitlab_project_path:
            reasons.append('请至少绑定一个 GitLab 仓库')
        return reasons

    def get_status(self, obj: Project) -> str:
        if not obj.enabled:
            return '已暂停'
        return '待配置' if self.get_status_reasons(obj) else '运行中'


class ReviewJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewJob
        fields = '__all__'


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = '__all__'
