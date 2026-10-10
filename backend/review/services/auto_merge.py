"""Guarded, idempotent automatic merge orchestration.

The review job and merge attempt are separate state machines. A completed
review is not itself permission to merge: every first attempt obtains a fresh
server-side gate assessment, and every retry after an uncertain response only
reconciles GitLab state instead of issuing another PUT.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from review.integrations.errors import safe_error
from review.integrations.gitlab import GitLabClient
from review.integrations.wecom import WeComClient
from review.models import NotificationConfig, ProjectPolicy, Review, ReviewJob
from review.services.execution_locks import ResourceBusy, resource_lock
from review.services.gate_control import evaluate_live, store_assessment
from review.services.mr_issues import prompt_fingerprint
from review.services.review_prompts import build_review_prompt


MERGE_NOT_REQUESTED = 'not_requested'
MERGE_PENDING = 'pending'
MERGE_MERGED = 'merged'
MERGE_BLOCKED = 'blocked'
MERGE_UNKNOWN = 'unknown'
MERGE_SKIPPED = 'skipped'
MERGE_BUSY = 'busy'

NOTIFY_NOT_REQUESTED = 'not_requested'
NOTIFY_SENDING = 'sending'
NOTIFY_DONE = 'done'
NOTIFY_FAILED = 'failed'
NOTIFY_UNKNOWN = 'unknown'
NOTIFY_SKIPPED = 'skipped'


def _notification_config(project):
    if not project.notification_inherit:
        config = NotificationConfig.objects.filter(project=project).first()
        if config:
            return config
    return NotificationConfig.objects.filter(
        scope=NotificationConfig.Scope.GLOBAL,
        project__isnull=True,
    ).first()


def _persist_merge(job: ReviewJob, status: str, *, result: dict | None = None, error: str = '') -> dict:
    previous_status = job.merge_status
    if result is not None:
        job.merge_result = result
    job.merge_status = status
    job.merge_error = error
    if status in (MERGE_PENDING, MERGE_UNKNOWN):
        if previous_status in (MERGE_PENDING, MERGE_UNKNOWN):
            job.merge_reconcile_attempts += 1
        delay = min(
            max(getattr(settings, 'REVIEW_RETRY_BACKOFF_SECONDS', 2), 2)
            * (2 ** min(job.merge_reconcile_attempts, 7)),
            300,
        )
        job.merge_next_check_at = timezone.now() + timedelta(seconds=delay)
    else:
        job.merge_reconcile_attempts = 0
        job.merge_next_check_at = None
    # A state transition deserves a fresh result card. Same-state polling is
    # intentionally idempotent and must not spam WeCom.
    if (
        previous_status != status
        and job.merge_notification_state in (
            NOTIFY_SENDING,
            NOTIFY_DONE,
            NOTIFY_FAILED,
            NOTIFY_UNKNOWN,
        )
    ):
        job.merge_notification_state = NOTIFY_NOT_REQUESTED
    fields = (
        'merge_status', 'merge_error', 'merge_result',
        'merge_next_check_at', 'merge_reconcile_attempts',
        'merge_notification_state', 'updated_at',
    )
    job.save(update_fields=fields)
    return {'status': status, **(result or {}), **({'reason': error} if error else {})}


def _remote_state(client: GitLabClient, review: Review) -> dict:
    """Fetch only the fields needed for reconciliation, never a browser payload."""
    current = client.fetch_merge_request(review.mr_iid, {})
    return current if isinstance(current, dict) else {}


def _reconcile(job: ReviewJob, review: Review, client: GitLabClient, *, error: str = '') -> dict:
    """Resolve an uncertain attempt without issuing another merge request."""
    try:
        current = _remote_state(client, review)
    except Exception as exc:
        return _persist_merge(job, MERGE_UNKNOWN, error=safe_error(exc) or error or 'GitLab 合并结果无法确认')
    if current.get('state') == 'merged' or current.get('merged') is True:
        return _persist_merge(job, MERGE_MERGED, result=current)
    return _persist_merge(job, MERGE_UNKNOWN, result=current, error=error or 'GitLab 合并结果尚未确认，请人工核对')


def _reconcile_disabled(job: ReviewJob, review: Review, client: GitLabClient, *, reason: str) -> dict:
    """Reconcile without PUT after an administrator disables the gate/project."""
    try:
        current = _remote_state(client, review)
    except Exception as exc:
        detail = safe_error(exc) or 'GitLab 状态无法确认'
        return _persist_merge(
            job,
            MERGE_BLOCKED,
            error=f'{reason}；{detail}，已停止自动重试，请人工核对',
        )
    if current.get('state') == 'merged' or current.get('merged') is True:
        return _persist_merge(job, MERGE_MERGED, result=current)
    return _persist_merge(
        job,
        MERGE_BLOCKED,
        result=current,
        error=f'{reason}，已停止自动重试，请人工核对',
    )


def _reconcile_disabled_locked(job: ReviewJob, review: Review, *, reason: str) -> dict:
    with resource_lock(f'merge:{review.project_id}:{review.repository_path}:{review.mr_iid}'):
        job.refresh_from_db()
        if job.merge_status not in (MERGE_PENDING, MERGE_UNKNOWN):
            return {'status': job.merge_status, **(job.merge_result or {})}
        try:
            client = GitLabClient(review.project, repository_path=review.repository_path)
        except Exception as exc:
            return _persist_merge(job, MERGE_BLOCKED, error=f'{reason}；{safe_error(exc)}，已停止自动重试，请人工核对')
        return _reconcile_disabled(job, review, client, reason=reason)


def execute_auto_merge(job: ReviewJob, review: Review, policy: ProjectPolicy) -> dict:
    """Run one guarded merge attempt for a completed review job.

    The method is safe to call repeatedly. ``pending`` and ``unknown`` states
    are reconciled first and therefore never cause a duplicate PUT.
    """
    job.refresh_from_db()
    review.refresh_from_db()
    policy.refresh_from_db()

    if job.project_id != review.project_id or policy.project_id != review.project_id:
        return _persist_merge(job, MERGE_BLOCKED, error='自动合并对象不属于同一项目')
    if job.kind != ReviewJob.Kind.REVIEW or not policy.auto_merge_enabled:
        return _persist_merge(job, MERGE_SKIPPED, error='项目策略未开启完整评审自动合并')
    if not review.project.enabled:
        return _persist_merge(job, MERGE_BLOCKED, error='项目已暂停，禁止自动合并')
    if review.status not in (Review.Status.COMPLETED, Review.Status.BLOCKED):
        return _persist_merge(job, MERGE_BLOCKED, error='评审尚未完成，禁止自动合并')
    if job.merge_status == MERGE_MERGED:
        return {'status': MERGE_MERGED, **(job.merge_result or {})}

    try:
        client = GitLabClient(review.project, repository_path=review.repository_path)
        with resource_lock(f'merge:{review.project_id}:{review.repository_path}:{review.mr_iid}'):
            # The lock is acquired before reconciliation so two workers cannot
            # both decide that an uncertain response needs a PUT.
            job.refresh_from_db()
            if job.merge_status == MERGE_MERGED:
                return {'status': MERGE_MERGED, **(job.merge_result or {})}
            if job.merge_status in (MERGE_PENDING, MERGE_UNKNOWN):
                return _reconcile(job, review, client)

            assessment = evaluate_live(review, policy)
            analysis = job.analysis_result if isinstance(job.analysis_result, dict) else {}
            scope_matches = analysis.get('_issue_scope_fingerprint') == prompt_fingerprint(build_review_prompt(policy, job.kind))
            assessment.setdefault('checks', {})['review_scope_matches'] = scope_matches
            if not scope_matches:
                detail = '评审检查范围缺失或要求已变更，请对当前提交重新执行完整评审'
                assessment['reason'] = detail if assessment['passed'] else f"{assessment['reason']}；{detail}"
                assessment['passed'] = False
            store_assessment(policy, review, assessment)
            # The live GET can take long enough for an administrator to turn
            # the gate off or pause the project. Re-read both flags before any
            # external PUT; the earlier fingerprint intentionally excludes the
            # execution switch itself.
            policy.refresh_from_db()
            review.project.refresh_from_db()
            if not policy.auto_merge_enabled:
                return _persist_merge(job, MERGE_BLOCKED, result=assessment, error='自动合并已关闭')
            if not review.project.enabled:
                return _persist_merge(job, MERGE_BLOCKED, result=assessment, error='项目已暂停，禁止自动合并')
            if assessment.get('passed') is not True:
                return _persist_merge(
                    job,
                    MERGE_BLOCKED,
                    result=assessment,
                    error=assessment.get('reason') or '门禁检查未通过，禁止自动合并',
                )

            # Persist intent before the external PUT. A process crash after
            # this write resumes with reconciliation instead of a duplicate PUT.
            job.merge_attempts += 1
            job.merge_status = MERGE_PENDING
            job.merge_error = ''
            job.merge_result = {'assessment': assessment, 'attempted_at': timezone.now().isoformat()}
            job.save(update_fields=('merge_attempts', 'merge_status', 'merge_error', 'merge_result', 'updated_at'))
            try:
                result = client.merge(review)
            except Exception as exc:
                return _reconcile(job, review, client, error=safe_error(exc))
            if result.get('merged') is True or result.get('state') == 'merged':
                return _persist_merge(job, MERGE_MERGED, result=result)
            return _persist_merge(job, MERGE_PENDING, result=result, error='GitLab 已接受请求，等待合并结果')
    except ResourceBusy:
        return {'status': MERGE_BUSY, 'reason': '自动合并正在执行，请稍后刷新状态'}
    except Exception as exc:
        return _persist_merge(job, MERGE_BLOCKED, error=safe_error(exc))


def reconcile_pending_merges(limit: int = 10) -> int:
    """Reconcile asynchronous/ambiguous GitLab outcomes from the queue worker."""
    now = timezone.now()
    jobs = ReviewJob.objects.select_related('project', 'review').filter(
        status=ReviewJob.Status.SUCCEEDED,
        kind=ReviewJob.Kind.REVIEW,
        review__isnull=False,
        merge_status__in=(MERGE_PENDING, MERGE_UNKNOWN),
    ).filter(Q(merge_next_check_at__isnull=True) | Q(merge_next_check_at__lte=now))
    count = 0
    for job in jobs.order_by('updated_at')[:max(limit, 0)]:
        policy = ProjectPolicy.objects.filter(project_id=job.project_id).first()
        if not policy or not job.review:
            continue
        try:
            if not job.project.enabled or not policy.auto_merge_enabled:
                reason = '项目已暂停' if not job.project.enabled else '自动合并已关闭'
                _reconcile_disabled_locked(job, job.review, reason=reason)
            else:
                execute_auto_merge(job, job.review, policy)
            send_merge_result(job, job.review)
        except Exception:
            # A later worker tick or the explicit review-detail retry can
            # recover the delivery; never make the review job itself failed.
            pass
        count += 1
    return count


def _send_merge_result_locked(job: ReviewJob, review: Review, *, force: bool = False) -> dict:
    """Deliver the persisted merge outcome to the configured WeCom robot once."""
    job.refresh_from_db()
    if job.merge_notification_state in (NOTIFY_DONE, NOTIFY_FAILED, NOTIFY_SKIPPED, NOTIFY_UNKNOWN):
        if force and job.merge_notification_state in (NOTIFY_FAILED, NOTIFY_UNKNOWN):
            job.merge_notification_state = NOTIFY_NOT_REQUESTED
            job.save(update_fields=('merge_notification_state', 'updated_at'))
        else:
            return {'status': job.merge_notification_state, 'merge_result': job.merge_result or {}}
    if job.merge_notification_state == NOTIFY_SENDING:
        job.merge_notification_state = NOTIFY_UNKNOWN
        job.save(update_fields=('merge_notification_state', 'updated_at'))
        return {'status': NOTIFY_UNKNOWN, 'reason': '通知投递结果不确定，未自动重发'}

    target_merge_status = job.merge_status
    target_merge_result = dict(job.merge_result or {})
    notification = _notification_config(review.project)
    try:
        job.merge_notification_state = NOTIFY_SENDING
        job.save(update_fields=('merge_notification_state', 'updated_at'))
        result = WeComClient(notification).send_merge_result(review, job.merge_result or {}, job.merge_status)
        job.refresh_from_db()
        if job.merge_status != target_merge_status or (job.merge_result or {}) != target_merge_result:
            job.merge_notification_state = NOTIFY_NOT_REQUESTED
            job.save(update_fields=('merge_notification_state', 'updated_at'))
            return {'status': NOTIFY_UNKNOWN, 'reason': '合并状态在通知投递期间发生变化，等待发送最新结果'}
        status = NOTIFY_DONE if result.get('ok') or result.get('skipped') else NOTIFY_FAILED
        job.merge_notification_state = status
        job.save(update_fields=('merge_notification_state', 'updated_at'))
        return {'status': status, **result}
    except Exception as exc:
        job.refresh_from_db()
        if job.merge_status != target_merge_status or (job.merge_result or {}) != target_merge_result:
            job.merge_notification_state = NOTIFY_NOT_REQUESTED
            job.save(update_fields=('merge_notification_state', 'updated_at'))
            return {'status': NOTIFY_UNKNOWN, 'reason': '合并状态在通知投递期间发生变化，等待发送最新结果'}
        job.merge_notification_state = NOTIFY_UNKNOWN
        job.save(update_fields=('merge_notification_state', 'updated_at'))
        return {'status': NOTIFY_UNKNOWN, 'reason': safe_error(exc)}


def send_merge_result(job: ReviewJob, review: Review, *, force: bool = False) -> dict:
    """Deliver a result under the same per-review lock for worker and API."""
    with resource_lock(f'merge-notification:{review.pk}'):
        return _send_merge_result_locked(job, review, force=force)
