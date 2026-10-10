from __future__ import annotations
from review.integrations.errors import IntegrationNotConfiguredError, safe_error

import time
import uuid
import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from review.integrations.gitlab import GitLabClient
from review.integrations.llm import LLMClient
from review.integrations.wecom import WeComClient
from review.engines.adapters import ADAPTERS
from review.engines.base import ReviewContext
from review.engines.registry import effective_engine, is_engine_selectable
from review.engines.snapshot import create_review_snapshot
from review.models import (
    AuditLog,
    LineComment,
    ModelConfig,
    NotificationConfig,
    Project,
    ProjectPolicy,
    Review,
    ReviewJob,
)
from review.services.gates import evaluate_gate
from review.services.execution_locks import resource_lock, ResourceBusy
from review.services.diff_positions import ground_suggestion_originals, validate_findings, validate_suggestions
from review.services.finding_locations import ground_finding_anchors
from review.services.review_prompts import build_review_prompt
from review.services.auto_merge import execute_auto_merge, send_merge_result
from review.services.mr_coverage import active_mr_jobs
from review.services import mr_issues
from review.services.repository_identity import resolve_repository


class StaleReviewError(ValueError):
    """A stale or unverifiable publication must not be automatically retried."""


def _validate_chinese_result(result: dict) -> None:
    import re
    summary = str(result.get('summary') or '').strip()
    if not re.search(r'[\u3400-\u9fff]', summary):
        raise ValueError('评审引擎没有生成中文总评，已停止发布')
    for index, finding in enumerate(result.get('findings') or []):
        for field in ('title', 'body'):
            if not re.search(r'[\u3400-\u9fff]', str(finding.get(field) or '')):
                raise ValueError(f'评审 finding {index} 的{field}不是中文，已停止发布')


def _verify_refs(review: Review, mr: dict) -> None:
    refs = mr.get('diff_refs') or {}
    for field in ('base_sha', 'start_sha', 'head_sha'):
        expected = getattr(review, field)
        if (expected or not settings.REVIEW_ALLOW_TEST_FALLBACK) and refs.get(field) != expected:
            raise StaleReviewError('MR 提交已变化或无法核验，请对最新提交重新发起评审')


def _diff_metadata(diff: str) -> tuple[list[str], int, int]:
    files: list[str] = []
    additions = 0
    deletions = 0
    for line in diff.splitlines():
        if line.startswith('+++ b/'):
            path = line[6:].strip()
            if path and path != '/dev/null' and path not in files:
                files.append(path)
        elif line.startswith('diff --git '):
            path = line.rsplit(' b/', 1)[-1].strip()
            if path and path not in files:
                files.append(path)
        elif line.startswith('+') and not line.startswith('+++'):
            additions += 1
        elif line.startswith('-') and not line.startswith('---'):
            deletions += 1
    if not files:
        files = [line.strip() for line in diff.splitlines() if line.strip() and '/' in line and not line.startswith(('+', '-'))]
    return files, additions, deletions


def _default_payload(project: Project, payload: dict, kind: str) -> dict:
    mr_iid = int(payload.get('mr_iid') or (payload.get('object_attributes') or {}).get('iid') or 0)
    attributes = payload.get('object_attributes') or {}
    return {
        **payload,
        'mr_iid': mr_iid,
        'repository_path': payload.get('repository_path') or project.gitlab_project_path,
        'title': payload.get('title') or attributes.get('title') or f'MR !{mr_iid}',
        'branch': payload.get('branch') or attributes.get('source_branch') or '',
        'gitlab_url': payload.get('gitlab_url') or attributes.get('url') or '',
        'source': payload.get('source') or (Review.Source.IMPROVE if kind == ReviewJob.Kind.IMPROVE else Review.Source.MANUAL),
    }


def create_review_job(project: Project, payload: dict, kind: str = ReviewJob.Kind.REVIEW) -> ReviewJob:
    return get_or_create_review_job(project, payload, kind)[0]


def get_or_create_review_job(project: Project, payload: dict, kind: str = ReviewJob.Kind.REVIEW):
    normalized = _default_payload(project, payload, kind)
    with resource_lock(f'mr-job:{project.pk}:{normalized["repository_path"]}:{normalized["mr_iid"]}'):
        return _create_locked_review_job(project, normalized, kind)


def _create_locked_review_job(project, normalized, kind):
    repository = resolve_repository(project, normalized['repository_path'])
    event_uuid = normalized.get('event_uuid') or normalized.get('event_id')
    if event_uuid:
        legacy = ReviewJob.objects.filter(idempotency_key=str(event_uuid), project=project, kind=kind, payload__mr_iid=normalized['mr_iid'], payload__repository_path=normalized['repository_path']).first()
        if legacy:
            return legacy, False
    active = active_mr_jobs(project, normalized['repository_path'], {normalized['mr_iid']})
    if normalized['mr_iid'] in active:
        return active[normalized['mr_iid']], False
    identity = [str(project.pk), normalized['repository_path'], normalized['mr_iid'], kind, str(event_uuid or uuid.uuid4())]
    idempotency_key = 'v2:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    # The queue and its visible review become available together. A worker must
    # not claim the job between insertion and attaching its queued detail.
    with transaction.atomic():
        job, created = ReviewJob.objects.get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                'project': project,
                'kind': kind,
                'payload': normalized,
                'max_attempts': settings.REVIEW_MAX_RETRIES,
            },
        )
        if created:
            job.review = Review.objects.create(
                project=project, repository=repository, repository_path=normalized['repository_path'],
                mr_iid=normalized['mr_iid'], title=normalized['title'],
                branch=normalized['branch'], source=normalized['source'],
                gitlab_url=normalized['gitlab_url'], status=Review.Status.QUEUED,
            )
            job.save(update_fields=('review', 'updated_at'))
    return job, created


def requeue_failed_job(job):
    payload = job.payload if isinstance(job.payload, dict) else {}
    review = job.review
    path = payload.get('repository_path') or (review.repository_path if review else '') or job.project.gitlab_project_path
    raw_iid = payload.get('mr_iid') or (review.mr_iid if review else 0)
    try:
        iid = int(raw_iid)
    except (TypeError, ValueError):
        return False
    with resource_lock(f'mr-job:{job.project_id}:{path}:{iid}'):
        if active_mr_jobs(job.project, path, {iid}):
            return False
        return bool(ReviewJob.objects.filter(pk=job.pk, status=ReviewJob.Status.FAILED, project__enabled=True).update(
            status=ReviewJob.Status.QUEUED, attempts=0, error_message='',
            started_at=None, completed_at=None, next_attempt_at=None, updated_at=timezone.now(),
        ))


def _model_config(project: Project) -> ModelConfig | None:
    if not project.model_inherit:
        return ModelConfig.objects.filter(project=project).first()
    return ModelConfig.objects.filter(scope=ModelConfig.Scope.GLOBAL, project__isnull=True).first()


def _notification_config(project: Project) -> NotificationConfig | None:
    if not project.notification_inherit:
        config = NotificationConfig.objects.filter(project=project).first()
        return config or NotificationConfig(project=project, scope=NotificationConfig.Scope.PROJECT, enabled=False)
    return NotificationConfig.objects.filter(scope=NotificationConfig.Scope.GLOBAL, project__isnull=True).first()


def run_review_job(job_id, recover=False) -> Review | None:
    try:
        with resource_lock(f'job:{job_id}'):
            if recover:
                abandoned = ReviewJob.objects.filter(pk=job_id, status=ReviewJob.Status.RUNNING).first()
                if abandoned:
                    exhausted = abandoned.attempts >= abandoned.max_attempts
                    abandoned.status = ReviewJob.Status.FAILED if exhausted else ReviewJob.Status.QUEUED
                    abandoned.error_message = '执行进程已退出，重试次数耗尽' if exhausted else '执行进程已退出，已恢复排队'
                    abandoned.next_attempt_at = None
                    abandoned.completed_at = timezone.now() if exhausted else None
                    abandoned.save(update_fields=('status', 'error_message', 'next_attempt_at', 'completed_at', 'updated_at'))
                    if exhausted and abandoned.review_id:
                        Review.objects.filter(pk=abandoned.review_id).update(status=Review.Status.FAILED, error_message=abandoned.error_message, completed_at=timezone.now())
                    AuditLog.objects.create(project=abandoned.project, action='恢复中断任务', target=str(job_id), metadata={'exhausted': exhausted})
            return _run_review_job(job_id)
    except ResourceBusy:
        return ReviewJob.objects.select_related('review').get(pk=job_id).review


def _run_review_job(job_id) -> Review | None:
    # Claim with one conditional write. Network calls must not hold a DB transaction.
    now = timezone.now()
    claimed = ReviewJob.objects.filter(
        pk=job_id, status=ReviewJob.Status.QUEUED, project__enabled=True,
        attempts__lt=F('max_attempts'),
    ).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)).update(
        status=ReviewJob.Status.RUNNING, attempts=F('attempts') + 1,
        started_at=now, next_attempt_at=None, error_message='', updated_at=now,
    )
    job = ReviewJob.objects.select_related('project', 'review').get(pk=job_id)
    if not claimed:
        return job.review
    payload = _default_payload(job.project, job.payload, job.kind)
    started = time.perf_counter()
    review = job.review
    try:
        policy, _ = ProjectPolicy.objects.get_or_create(project=job.project)
        gitlab = GitLabClient(job.project, repository_path=payload['repository_path'])
        mr = gitlab.fetch_merge_request(payload['mr_iid'], payload)
        expected_sha = payload.get('automatic_head_sha')
        if expected_sha and (mr.get('state') != 'opened' or mr.get('draft', mr.get('work_in_progress', False)) is True):
            raise StaleReviewError('MR 已结束或转为草稿，自动任务已停止')
        if expected_sha and expected_sha != ((mr.get('diff_refs') or {}).get('head_sha') or mr.get('sha')):
            raise StaleReviewError('自动任务的提交已变化，等待最新提交重新入队')
        if review is None:
            diff_refs = mr.get('diff_refs') or {}
            review = Review.objects.create(
                project=job.project,
                repository=resolve_repository(job.project, payload['repository_path'], historical=True),
                repository_path=payload['repository_path'],
                mr_iid=payload['mr_iid'],
                title=mr.get('title', payload['title']),
                branch=mr.get('source_branch', payload['branch']),
                source=payload['source'],
                gitlab_url=mr.get('web_url', payload.get('gitlab_url', '')),
                base_sha=diff_refs.get('base_sha', ''),
                start_sha=diff_refs.get('start_sha', ''),
                head_sha=diff_refs.get('head_sha', ''),
            )
            job.review = review
            job.save(update_fields=('review', 'updated_at'))
        mr_issues.assert_current(review)
        review.status = Review.Status.RUNNING
        review.started_at = timezone.now()
        review.error_message = ''
        review.completed_at = None
        review.save(update_fields=('status', 'started_at', 'error_message', 'completed_at', 'updated_at'))
        if job.analysis_result is None:
            if review.comments.exists():
                raise StaleReviewError('旧任务已有评论但缺少分析快照，请人工核对后重新发起评审')
            review.title = mr.get('title', payload['title'])
            review.branch = mr.get('source_branch', payload['branch'])
            review.gitlab_url = mr.get('web_url', payload.get('gitlab_url', ''))
            refs = mr.get('diff_refs') or {}
            for field in ('base_sha', 'start_sha', 'head_sha'):
                setattr(review, field, refs.get(field, ''))
            if not settings.REVIEW_ALLOW_TEST_FALLBACK and not all(
                getattr(review, field) for field in ('base_sha', 'start_sha', 'head_sha')
            ):
                raise StaleReviewError('MR 提交信息不完整，请等待 GitLab 准备完成后重新评审')
            review.save(update_fields=('title', 'branch', 'gitlab_url', 'base_sha', 'start_sha', 'head_sha', 'updated_at'))
            diff = gitlab.fetch_diff(payload['mr_iid'], payload)
            changed_files, additions, deletions = _diff_metadata(diff)
            review.diff_text = diff
            review.changed_files = changed_files
            review.additions = additions
            review.deletions = deletions
            review.save(update_fields=('diff_text', 'changed_files', 'additions', 'deletions', 'updated_at'))
            prompt = build_review_prompt(policy, job.kind)
            scope_fingerprint = mr_issues.prompt_fingerprint(prompt)
            prompt += mr_issues.existing_issue_context(review)
            model_config = _model_config(job.project)
            if not job.project.model_inherit and not ModelConfig.objects.filter(project=job.project).exists():
                raise IntegrationNotConfiguredError('项目选择独立模型，但尚未保存项目模型配置')
            engine_id = effective_engine(job.project)
            if engine_id == 'builtin':
                result = LLMClient(model_config).analyze(diff, prompt, job.kind)
            else:
                if not is_engine_selectable(engine_id, project=job.project, model_config=model_config):
                    raise IntegrationNotConfiguredError('当前项目所选评审引擎未通过运行时和模型配置验证，已停止发布')
                adapter = ADAPTERS.get(engine_id)
                if adapter is None:
                    raise IntegrationNotConfiguredError('当前项目所选评审引擎尚未接入')
                with create_review_snapshot(gitlab, review.base_sha, review.head_sha) as snapshot:
                    context = ReviewContext(
                        project=job.project,
                        title=review.title,
                        branch=review.branch,
                        mr_iid=review.mr_iid,
                        mr_url=review.gitlab_url,
                        mode=job.kind,
                        diff=diff,
                        prompt=prompt,
                        model_config=model_config,
                        timeout=LLMClient(model_config).timeout,
                        job_id=str(job.id),
                        repository=snapshot.repository,
                        base_ref=snapshot.base_ref,
                        head_ref=snapshot.head_ref,
                    )
                    result = adapter.analyze(context)
            _validate_chinese_result(result)
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                result['findings'] = ground_finding_anchors(diff, result.get('findings', []))
                result['findings'] = ground_suggestion_originals(diff, result.get('findings', []))
                validate_findings(diff, result.get('findings', []))
                validate_suggestions(diff, result.get('findings', []))
            # Persist before the first external write, including the zero-findings case.
            result['_issue_scope_fingerprint'] = scope_fingerprint
            job.analysis_result = result
            job.save(update_fields=('analysis_result', 'updated_at'))
        else:
            _verify_refs(review, mr)
            result = job.analysis_result
        _verify_refs(review, gitlab.fetch_merge_request(payload['mr_iid'], payload))
        if not settings.REVIEW_ALLOW_TEST_FALLBACK:
            grounded = ground_finding_anchors(review.diff_text, result.get('findings', []))
            if review.comments.exists() and any(
                before.get(field) != after.get(field)
                for before, after in zip(result.get('findings', []), grounded)
                for field in ('old_line', 'new_line')
            ):
                raise StaleReviewError('旧评审快照存在错误行号，已停止重试以避免重复发布，请重新评审')
            result['findings'] = grounded
            result['findings'] = ground_suggestion_originals(review.diff_text, result.get('findings', []))
            validate_findings(review.diff_text, result.get('findings', []))
            validate_suggestions(review.diff_text, result.get('findings', []))
            job.analysis_result = result
            job.save(update_fields=('analysis_result', 'updated_at'))
        review.summary = result.get('summary', '')
        review.save(update_fields=('summary', 'updated_at'))
        for finding in result.get('findings', []):
            file_path = str(finding.get('file_path') or '').strip()
            if not file_path:
                raise ValueError('评审 finding 缺少真实文件路径')
            if finding.get('new_line') is None and finding.get('old_line') is None:
                raise ValueError(f'评审 finding {file_path} 缺少真实行号')
        scope_fingerprint = result.get('_issue_scope_fingerprint') or mr_issues.prompt_fingerprint(build_review_prompt(policy, job.kind))
        prepared = mr_issues.prepare_findings(review, result.get('findings', []), scope_fingerprint)
        for comment, issue, _ in prepared:
            if comment.status == LineComment.Status.RESOLVED or comment.discussion_id:
                continue
            try:
                discussion = gitlab.create_discussion(review, comment)
            except Exception as exc:
                comment.status = LineComment.Status.FAILED
                comment.error_message = safe_error(exc)
                comment.save(update_fields=('status', 'error_message', 'updated_at'))
                raise
            comment.discussion_id = str(discussion.get('id', ''))
            comment.error_message = ''
            comment.status = LineComment.Status.RESOLVED if discussion.get('resolved') is True else LineComment.Status.PUBLISHED
            if comment.status == LineComment.Status.RESOLVED:
                comment.resolved_at = timezone.now()
            comment.published_at = timezone.now()
            comment.save(update_fields=('discussion_id', 'status', 'error_message', 'published_at', 'resolved_at', 'updated_at'))
            mr_issues.record_publication(comment, comment.discussion_id)
        mr_issues.reconcile(review, job, gitlab, prepared, scope_fingerprint,
                            lambda: _verify_refs(review, gitlab.fetch_merge_request(payload['mr_iid'], payload)))
        review.summary = result.get('summary', '')
        engine_result = result.get('engine')
        if isinstance(engine_result, dict):
            review.engine_id = str(engine_result.get('id') or effective_engine(job.project))[:80]
            review.engine_version = str(engine_result.get('version') or '')[:160]
        else:
            review.engine_id = str(engine_result or effective_engine(job.project))[:80]
            review.engine_version = 'builtin' if review.engine_id == 'builtin' else ''
        configured_model = _model_config(job.project)
        review.engine_model = str((configured_model.model if configured_model else '') or settings.CCSWITCH_MODEL)[:160]
        policy.refresh_from_db()
        gate = evaluate_gate(review, policy)
        review.gate_status = gate.status
        review.gate_reason = gate.reason
        review.status = Review.Status.BLOCKED if not gate.passed else Review.Status.COMPLETED
        review.duration_ms = int((time.perf_counter() - started) * 1000)
        review.completed_at = timezone.now()
        review.save(update_fields=('summary', 'engine_id', 'engine_version', 'engine_model', 'gate_status', 'gate_reason', 'status', 'duration_ms', 'completed_at', 'updated_at'))
        try:
            gitlab.create_summary_note(review)
            review.summary_sync_status = 'synced'
        except Exception as exc:  # GitLab note delivery must not erase a completed review.
            review.summary_sync_status = 'failed'
            AuditLog.objects.create(project=job.project,
                action='GitLab 总评同步失败',
                target=f'{job.project.name}!{review.mr_iid}',
                metadata={'review_id': str(review.id), 'error': safe_error(exc)},
            )
        review.save(update_fields=('summary_sync_status', 'updated_at'))
        notification = _notification_config(job.project)
        try:
            if job.notification_state == 'sending':
                job.notification_state = 'unknown'
                job.save(update_fields=('notification_state', 'updated_at'))
                AuditLog.objects.create(project=job.project, action='企业微信投递结果不确定', target=f'{job.project.name}!{review.mr_iid}', metadata={'review_id': str(review.pk), 'message': '执行中断，可能已送达，自动重试不会再次发送；请核对群消息'})
            if job.notification_state == 'pending':
                job.notification_state = 'sending'
                job.save(update_fields=('notification_state', 'updated_at'))
                notification_result = WeComClient(notification).send_review(review)
                job.notification_state = 'done' if notification_result.get('ok') or notification_result.get('skipped') else 'failed'
                job.save(update_fields=('notification_state', 'updated_at'))
            else:
                notification_result = {'skipped': True, 'delivery_state': job.notification_state}
            AuditLog.objects.create(
                project=job.project,
                action='发送企业微信评审通知',
                target=f'{job.project.name}!{review.mr_iid}',
                metadata={'review_id': str(review.id), **notification_result},
            )
        except Exception as exc:  # Notification delivery must not erase a completed review.
            job.notification_state = 'unknown'
            job.save(update_fields=('notification_state', 'updated_at'))
            AuditLog.objects.create(
                project=job.project,
                action='企业微信通知失败',
                target=f'{job.project.name}!{review.mr_iid}',
                metadata={'review_id': str(review.id), 'error': safe_error(exc)},
            )
        if job.kind == ReviewJob.Kind.REVIEW and policy.auto_merge_enabled and mr.get('state') == 'merged' and job.merge_status == 'not_requested':
            job.merge_status = 'skipped'
            job.merge_result = {'reason': 'MR 已合并；事后评审不会再次执行自动合并'}
            job.merge_notification_state = 'skipped'
            job.save(update_fields=('merge_status', 'merge_result', 'merge_notification_state', 'updated_at'))
        elif job.kind == ReviewJob.Kind.REVIEW and policy.auto_merge_enabled:
            try:
                merge_result = execute_auto_merge(job, review, policy)
                merge_notification = send_merge_result(job, review)
                AuditLog.objects.create(
                    project=job.project,
                    action='自动合并结果',
                    target=f'{job.project.name}!{review.mr_iid}',
                    metadata={
                        'review_id': str(review.id),
                        'merge': merge_result,
                        'notification': merge_notification,
                    },
                )
            except Exception as exc:  # Merge must never erase a completed review.
                AuditLog.objects.create(
                    project=job.project,
                    action='自动合并处理失败',
                    target=f'{job.project.name}!{review.mr_iid}',
                    metadata={'review_id': str(review.id), 'error': safe_error(exc)},
                )
        AuditLog.objects.create(project=job.project,
            action='评审任务完成',
            target=f'{job.project.name}!{review.mr_iid}',
            metadata={'review_id': str(review.id), 'comments': review.comment_count, 'gate_status': review.gate_status},
        )
        job.status = ReviewJob.Status.SUCCEEDED
        job.next_attempt_at = None
        job.completed_at = timezone.now()
        job.save(update_fields=('status', 'next_attempt_at', 'completed_at', 'updated_at'))
        return review
    except Exception as exc:
        if review:
            review.status = Review.Status.FAILED
            review.error_message = safe_error(exc)
            review.completed_at = timezone.now()
            review.save(update_fields=('status', 'error_message', 'completed_at', 'updated_at'))
        job.error_message = safe_error(exc)
        if job.attempts < job.max_attempts and not isinstance(exc, StaleReviewError):
            backoff_seconds = min(
                max(settings.REVIEW_RETRY_BACKOFF_SECONDS, 0) * (2 ** max(job.attempts - 1, 0)),
                300,
            )
            job.status = ReviewJob.Status.QUEUED
            job.next_attempt_at = timezone.now() + timedelta(seconds=backoff_seconds)
            job.save(update_fields=('status', 'next_attempt_at', 'error_message', 'updated_at'))
            AuditLog.objects.create(
                project=job.project,
                action='评审任务重试排队',
                target=f'{job.project.name}:{job.kind}',
                metadata={'error': safe_error(exc), 'attempt': job.attempts, 'max_attempts': job.max_attempts, 'next_attempt_at': job.next_attempt_at.isoformat()},
            )
        else:
            job.status = ReviewJob.Status.FAILED
            job.next_attempt_at = None
            job.completed_at = timezone.now()
            job.save(update_fields=('status', 'next_attempt_at', 'error_message', 'completed_at', 'updated_at'))
            AuditLog.objects.create(project=job.project, action='评审任务失败', target=f'{job.project.name}:{job.kind}', metadata={'error': safe_error(exc), 'attempt': job.attempts})
        if review is None:
            review = Review.objects.create(
                project=job.project,
                repository=resolve_repository(job.project, payload['repository_path'], historical=True),
                repository_path=payload['repository_path'],
                mr_iid=payload['mr_iid'],
                title=payload['title'],
                branch=payload.get('branch', ''),
                gitlab_url=payload.get('gitlab_url', ''),
                source=payload['source'],
                status=Review.Status.FAILED,
                error_message=safe_error(exc),
                completed_at=timezone.now(),
            )
            job.review = review
            job.save(update_fields=('review', 'updated_at'))
        return review


def trigger_review(project: Project, payload: dict, kind: str = ReviewJob.Kind.REVIEW) -> tuple[ReviewJob, Review | None]:
    job = create_review_job(project, payload, kind)
    if settings.REVIEW_EXECUTE_INLINE:
        review = run_review_job(job.id)
        job.refresh_from_db()
        return job, review
    return job, job.review
