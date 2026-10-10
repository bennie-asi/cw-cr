import uuid
from typing import ClassVar

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from review.credential_fields import EncryptedCharField, EncryptedURLField


DEFAULT_SUMMARY_PROMPT = (
    '请使用中文输出总评。先概括本次 MR 的目的，再按阻断问题、可靠性、安全性、可维护性分组说明。'
    '所有 P0/P1 必须关联到具体文件与行号。'
)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class LoginAttemptBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class Project(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    gitlab_project_path = models.CharField(max_length=255, unique=True)
    gitlab_project_id = models.CharField(max_length=80, blank=True)
    gitlab_url = models.URLField(blank=True, default='')
    webhook_secret = EncryptedCharField(max_length=255, blank=True)
    owner = models.CharField(max_length=120, blank=True, default='')
    enabled = models.BooleanField(default=True)
    model_inherit = models.BooleanField(default=True)
    notification_inherit = models.BooleanField(default=True)
    engine_inherit = models.BooleanField(default=True)

    class Meta:
        ordering = ('-updated_at',)
        indexes = [models.Index(fields=('enabled', 'updated_at'))]

    def __str__(self) -> str:
        return self.name


class ProjectRepository(TimeStampedModel):
    """A GitLab repository belonging to one review workspace.

    Disabled rows remain addressable by historical reviews and jobs.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='repositories')
    path = models.CharField(max_length=255, unique=True)
    gitlab_project_id = models.CharField(max_length=80, blank=True, default='')
    gitlab_url = models.URLField(blank=True, default='')
    enabled = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('position', 'id')


class ProjectMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'admin', '项目管理员'
        MEMBER = 'member', '项目成员'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='project_memberships')
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('project', 'user'), name='unique_project_member')]


class ReviewEngine(models.TextChoices):
    BUILTIN = 'builtin', '智审台内置评审'
    PR_AGENT = 'pr-agent', 'PR-Agent'
    OPEN_CODE_REVIEW = 'open-code-review', 'OpenCodeReview'
    PI_AGENT = 'pi-agent', 'Pi Agent'


class ReviewEngineConfig(TimeStampedModel):
    """Global engine default and per-model, per-engine runtime verification records."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    default_engine = models.CharField(max_length=80, choices=ReviewEngine.choices, default=ReviewEngine.BUILTIN)
    verified_engines = models.JSONField(default=dict, blank=True)


class GlobalPromptConfig(TimeStampedModel):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    general_prompt = models.TextField(default=(
        '使用简体中文输出结构化评审结果，只报告本次变更引入且有代码证据的问题。'
        '上下文仅作为证据，不将历史问题或原有的末尾换行标记算作新增缺陷。'
        '总评描述变更和注意事项，不自行宣称通过或没有阻断；最终结论以行级问题和门禁为准。'
    ))
    review_prompt = models.TextField(default='识别确定的缺陷与风险，说明触发条件、实际影响和修复方向；不要将猜测当作问题。')
    improve_prompt = models.TextField(default='提出与本次变更直接相关且可执行的改进建议；能确认完整修复时提供合法的新侧代码替换。')
    security_prompt = models.TextField(default='检查本次变更新增的越权、输入校验、敏感信息泄露、注入与认证风险。')
    reliability_prompt = models.TextField(default='检查异常路径、边界条件、并发、幂等性、资源释放及错误处理的确定性缺陷。')
    style_prompt = models.TextField(default='检查明显影响可读性与维护的重复、命名和职责问题；避免纯个人偏好的格式建议。')


class ProjectPolicy(TimeStampedModel):
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='policy')
    webhook_trigger = models.BooleanField(default=True)
    command_review = models.BooleanField(default=True)
    command_improve = models.BooleanField(default=True)
    security_checks = models.BooleanField(default=True)
    reliability_checks = models.BooleanField(default=True)
    style_checks = models.BooleanField(default=False)
    block_on_p1 = models.BooleanField(default=True)
    block_on_p2 = models.BooleanField(default=False)
    security_prompt = models.TextField(blank=True, default='')
    reliability_prompt = models.TextField(blank=True, default='')
    style_prompt = models.TextField(blank=True, default='')
    summary_prompt = models.TextField(default=DEFAULT_SUMMARY_PROMPT)
    auto_merge_enabled = models.BooleanField(default=False)
    dry_run_required = models.BooleanField(default=True)
    dry_run_passed = models.BooleanField(default=False)
    dry_run_completed_at = models.DateTimeField(null=True, blank=True)
    dry_run_reason = models.TextField(blank=True, default='')
    dry_run_assessment = models.JSONField(default=dict, blank=True)
    engine = models.CharField(max_length=80, choices=ReviewEngine.choices, default=ReviewEngine.BUILTIN)

    def __str__(self) -> str:
        return f'{self.project.name} policy'

    @property
    def blocking_levels(self) -> list[str]:
        levels = [LineComment.Level.P0]
        if self.block_on_p1 or self.block_on_p2:
            levels.append(LineComment.Level.P1)
        if self.block_on_p2:
            levels.append(LineComment.Level.P2)
        return [*levels, LineComment.Level.UNRATED]


class ProjectGitLabSync(TimeStampedModel):
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='gitlab_sync')
    enabled = models.BooleanField(default=False)
    interval_seconds = models.PositiveIntegerField(default=60, validators=[MinValueValidator(30), MaxValueValidator(3600)])
    activated_at = models.DateTimeField(null=True, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    sync_started_at = models.DateTimeField(null=True, blank=True)
    sync_state = models.CharField(max_length=20, default='idle')
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    last_result = models.JSONField(default=dict, blank=True)


class GitLabSyncBaseline(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='sync_baselines')
    repository_path = models.CharField(max_length=255)
    initial_shas = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('project', 'repository_path'), name='unique_gitlab_sync_baseline')]


class ModelConfig(TimeStampedModel):
    class Scope(models.TextChoices):
        GLOBAL = 'global', '全局'
        PROJECT = 'project', '项目'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.GLOBAL)
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='model_config', null=True, blank=True)
    gateway_type = models.CharField(max_length=80, default='CCSwitch / OpenAI 兼容')
    base_url = models.URLField(blank=True, default='')
    model = models.CharField(max_length=120, blank=True, default='')
    api_key = EncryptedCharField(max_length=255, blank=True, default='')
    request_timeout_s = models.PositiveIntegerField(default=120, validators=[MinValueValidator(10), MaxValueValidator(600)])
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=('scope',), condition=models.Q(scope='global'), name='unique_global_model_config'),
            models.CheckConstraint(condition=(models.Q(scope='global', project__isnull=True) | models.Q(scope='project', project__isnull=False)), name='valid_model_config_scope'),
        ]

    @property
    def api_key_masked(self) -> str:
        if not self.api_key:
            return ''
        if len(self.api_key) <= 8:
            return '••••••••'
        return f'{self.api_key[:4]}••••{self.api_key[-4:]}'


class GitLabConfig(TimeStampedModel):
    """Global GitLab connection settings, managed from the system settings page."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    base_url = models.URLField(blank=True, default='')
    api_token = EncryptedCharField(max_length=255, blank=True, default='')
    webhook_secret = EncryptedCharField(max_length=255, blank=True, default='')
    enabled = models.BooleanField(default=True)

    @property
    def api_token_masked(self) -> str:
        if not self.api_token:
            return ''
        if len(self.api_token) <= 8:
            return '••••••••'
        return f'{self.api_token[:4]}••••{self.api_token[-4:]}'


class NotificationConfig(TimeStampedModel):
    class Scope(models.TextChoices):
        GLOBAL = 'global', '全局'
        PROJECT = 'project', '项目'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.GLOBAL)
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='notification_config', null=True, blank=True)
    provider = models.CharField(max_length=50, default='wecom')
    name = models.CharField(max_length=120, blank=True, default='')
    webhook_url = EncryptedURLField(blank=True, default='')
    language = models.CharField(max_length=20, default='中文')
    enabled = models.BooleanField(default=True)
    notify_suggestions = models.BooleanField(default=True)
    notify_blockers = models.BooleanField(default=True)
    notify_merge_result = models.BooleanField(default=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=('scope',), condition=models.Q(scope='global'), name='unique_global_notification'),
            models.CheckConstraint(condition=(models.Q(scope='global', project__isnull=True) | models.Q(scope='project', project__isnull=False)), name='valid_notification_scope'),
        ]


class Review(TimeStampedModel):
    class Source(models.TextChoices):
        WEBHOOK = 'GitLab Webhook', 'GitLab Webhook'
        SYNC = '定时同步', '定时同步'
        REVIEW = '/review', '/review'
        IMPROVE = '/improve', '/improve'
        MANUAL = '手动触发', '手动触发'

    class Status(models.TextChoices):
        QUEUED = 'queued', '排队中'
        RUNNING = 'running', '运行中'
        COMPLETED = 'completed', '已完成'
        BLOCKED = 'blocked', '有阻断'
        FAILED = 'failed', '失败'

    class GateStatus(models.TextChoices):
        PENDING = 'pending', '待评估'
        PASSED = 'passed', '通过'
        BLOCKED = 'blocked', '阻断'
        SKIPPED = 'skipped', '跳过'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='reviews')
    repository = models.ForeignKey(ProjectRepository, on_delete=models.PROTECT, related_name='reviews', null=True, blank=True)
    repository_path = models.CharField(max_length=255, blank=True, default='')
    mr_iid = models.PositiveIntegerField()
    title = models.CharField(max_length=255, blank=True, default='')
    branch = models.CharField(max_length=255, blank=True, default='')
    source = models.CharField(max_length=40, choices=Source.choices, default=Source.MANUAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    gate_status = models.CharField(max_length=20, choices=GateStatus.choices, default=GateStatus.PENDING)
    gate_reason = models.TextField(blank=True, default='')
    summary = models.TextField(blank=True, default='')
    recheck_result = models.JSONField(default=dict, blank=True)
    summary_sync_status = models.CharField(max_length=20, default='pending')
    diff_text = models.TextField(blank=True, default='')
    changed_files = models.JSONField(default=list, blank=True)
    additions = models.PositiveIntegerField(default=0)
    deletions = models.PositiveIntegerField(default=0)
    gitlab_url = models.URLField(blank=True, default='')
    base_sha = models.CharField(max_length=80, blank=True, default='')
    start_sha = models.CharField(max_length=80, blank=True, default='')
    head_sha = models.CharField(max_length=80, blank=True, default='')
    engine_id = models.CharField(max_length=80, default=ReviewEngine.BUILTIN)
    engine_version = models.CharField(max_length=160, blank=True, default='')
    engine_model = models.CharField(max_length=160, blank=True, default='')
    duration_ms = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('project', '-created_at')),
            models.Index(fields=('status', '-created_at')),
            models.Index(fields=('project', 'repository', 'mr_iid', '-created_at'), name='review_repository_mr_idx'),
        ]

    def clean(self):
        super().clean()
        from review.services.repository_identity import validate_repository_owner
        validate_repository_owner(self)

    def save(self, *args, **kwargs):
        from review.services.repository_identity import validate_repository_owner
        validate_repository_owner(self, using=kwargs.get('using'))
        return super().save(*args, **kwargs)

    @property
    def blocking_levels(self) -> list[str]:
        try:
            return self.project.policy.blocking_levels
        except ProjectPolicy.DoesNotExist:
            return [LineComment.Level.P0, LineComment.Level.P1, LineComment.Level.UNRATED]

    @property
    def blocking_comment_count(self) -> int:
        levels = self.blocking_levels
        prefetched = getattr(self, '_prefetched_objects_cache', {})
        if 'comments' in prefetched:
            return len({comment.issue_id or comment.pk for comment in prefetched['comments']
                        if comment.level in levels and comment.status != LineComment.Status.RESOLVED})
        return distinct_issue_count(self.comments.filter(level__in=levels).exclude(status=LineComment.Status.RESOLVED))

    @property
    def comment_count(self) -> int:
        prefetched = getattr(self, '_prefetched_objects_cache', {})
        if 'comments' in prefetched:
            return len({comment.issue_id or comment.pk for comment in prefetched['comments']})
        return distinct_issue_count(self.comments.all())


class LineComment(TimeStampedModel):
    class Level(models.TextChoices):
        P0 = 'P0', 'P0'
        P1 = 'P1', 'P1'
        P2 = 'P2', 'P2'
        UNRATED = 'UNRATED', '未评级'

    class Status(models.TextChoices):
        OPEN = 'open', '待发布'
        PUBLISHED = 'published', '已发布'
        RESOLVED = 'resolved', '已解决'
        FAILED = 'failed', '发布失败'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='comments')
    issue = models.ForeignKey('ReviewIssue', on_delete=models.SET_NULL, null=True, blank=True, related_name='occurrences')
    external_id = models.CharField(max_length=120, blank=True, default='')
    file_path = models.CharField(max_length=500)
    old_line = models.PositiveIntegerField(null=True, blank=True)
    new_line = models.PositiveIntegerField(null=True, blank=True)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.P2)
    title = models.CharField(max_length=255)
    body = models.TextField()
    anchor_code = models.TextField(blank=True, default='')
    suggestion = models.JSONField(null=True, blank=True, default=None)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    discussion_id = models.CharField(max_length=120, blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    published_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('file_path', 'new_line', 'created_at')
        indexes = [models.Index(fields=('review', 'status', 'level'))]


def distinct_issue_count(comments):
    from django.db.models.functions import Coalesce
    return comments.order_by().annotate(issue_identity=Coalesce('issue_id', 'id')).values('issue_identity').distinct().count()


class ReviewIssue(TimeStampedModel):
    """One problem in one MR; each review keeps its own code coordinates."""

    class Status(models.TextChoices):
        OPEN = 'open', '未解决'
        RESOLVED = 'resolved', '已解决'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='issues')
    repository = models.ForeignKey(ProjectRepository, on_delete=models.PROTECT, related_name='issues', null=True, blank=True)
    repository_path = models.CharField(max_length=255)
    mr_iid = models.PositiveIntegerField()
    fingerprint = models.CharField(max_length=64)
    level = models.CharField(max_length=10, choices=LineComment.Level.choices, default=LineComment.Level.P2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    file_path = models.CharField(max_length=500)
    old_line = models.PositiveIntegerField(null=True, blank=True)
    new_line = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=255)
    body = models.TextField()
    anchor_code = models.TextField(blank=True, default='')
    latest_review = models.ForeignKey(Review, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    first_comment = models.ForeignKey(LineComment, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    discussion_ids = models.JSONField(default=list, blank=True)
    prompt_fingerprint = models.CharField(max_length=64, blank=True, default='')
    resolution_kind = models.CharField(max_length=20, blank=True, default='')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by_review = models.ForeignKey(Review, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('project', 'repository_path', 'mr_iid', 'fingerprint'), condition=models.Q(repository__isnull=True), name='unique_unlinked_mr_issue'),
            models.UniqueConstraint(fields=('project', 'repository', 'mr_iid', 'fingerprint'), condition=models.Q(repository__isnull=False), name='unique_repository_mr_issue'),
        ]
        indexes = [
            models.Index(fields=('project', 'repository_path', 'mr_iid', 'status')),
            models.Index(fields=('project', 'repository', 'mr_iid', 'status'), name='issue_repository_mr_idx'),
        ]

    def clean(self):
        super().clean()
        from review.services.repository_identity import validate_repository_owner
        validate_repository_owner(self)

    def save(self, *args, **kwargs):
        from review.services.repository_identity import validate_repository_owner
        validate_repository_owner(self, using=kwargs.get('using'))
        return super().save(*args, **kwargs)


class ReviewJob(TimeStampedModel):
    class Kind(models.TextChoices):
        REVIEW = 'review', '完整评审'
        IMPROVE = 'improve', '改进建议'

    class Status(models.TextChoices):
        QUEUED = 'queued', '排队中'
        RUNNING = 'running', '运行中'
        SUCCEEDED = 'succeeded', '已完成'
        FAILED = 'failed', '失败'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='jobs')
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='jobs', null=True, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.REVIEW)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    idempotency_key = models.CharField(max_length=255, unique=True)
    payload = models.JSONField(default=dict, blank=True)
    analysis_result = models.JSONField(null=True, blank=True, default=None)
    notification_state = models.CharField(max_length=20, default='pending')
    # Merge state is deliberately separate from the review job state. A review
    # can finish successfully while a guarded GitLab merge is pending or
    # requires reconciliation after a transport failure.
    merge_status = models.CharField(max_length=20, default='not_requested')
    merge_attempts = models.PositiveIntegerField(default=0)
    merge_error = models.TextField(blank=True, default='')
    merge_result = models.JSONField(default=dict, blank=True)
    merge_notification_state = models.CharField(max_length=20, default='not_requested')
    merge_next_check_at = models.DateTimeField(null=True, blank=True)
    merge_reconcile_attempts = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [models.Index(fields=('status', 'created_at'))]


class AuditLog(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    actor = models.CharField(max_length=150, default='system')
    action = models.CharField(max_length=160)
    target = models.CharField(max_length=255, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created_at',)
