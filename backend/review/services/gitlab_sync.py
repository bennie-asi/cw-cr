"""Bounded GitLab reads and persisted activation baselines for single-host sync."""
import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from review.integrations.gitlab import GitLabClient
from review.models import AuditLog, GitLabSyncBaseline, Project, Review
from review.services.execution_locks import ResourceBusy, resource_lock
from review.services.mr_coverage import enabled_repository_paths
from review.services.read_budget import ReadBudget


class SyncUnavailable(RuntimeError):
    pass


def read_open_merge_requests(project, path, budget):
    client = GitLabClient(project, repository_path=path)
    if not client.remote_enabled:
        raise SyncUnavailable('GitLab 连接未启用或缺少 API Token')
    items, seen, page = [], set(), 1
    while True:
        client.timeout = budget.next_timeout()
        batch, has_next = budget.run(lambda: client.list_merge_requests(page, 'opened'))
        if has_next and not batch:
            raise SyncUnavailable('MR 分页不完整，请稍后重试')
        for item in batch:
            iid, sha = item.get('iid'), item.get('sha')
            if isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0 or iid in seen:
                raise SyncUnavailable('MR 分页内容发生变化，请稍后重试')
            if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{40,64}', sha):
                raise SyncUnavailable('MR 缺少有效的提交 SHA，请等待 GitLab 准备完成')
            seen.add(iid)
            items.append(item)
        budget.check_deadline()
        if not has_next:
            return items
        page += 1


def activation_baselines(project):
    paths = enabled_repository_paths(project)
    if not paths:
        raise SyncUnavailable('请先启用至少一个 GitLab 仓库')
    budget = ReadBudget(100, 30, 5)
    try:
        snapshots = {path: {str(item['iid']): item['sha']
                            for item in read_open_merge_requests(project, path, budget)}
                     for path in paths}
    except SyncUnavailable:
        raise
    except Exception:
        raise SyncUnavailable('无法完整读取 GitLab MR，请检查连接、仓库权限或稍后重试') from None
    if paths != enabled_repository_paths(project):
        raise SyncUnavailable('仓库配置在读取期间发生变化，请重试')
    return snapshots


def activate_sync(config, interval_seconds):
    project = config.project
    if not project.enabled:
        raise SyncUnavailable('项目已暂停，请先恢复项目')
    snapshots = activation_baselines(project)
    now = timezone.now()
    with transaction.atomic():
        GitLabSyncBaseline.objects.filter(project=project).delete()
        GitLabSyncBaseline.objects.bulk_create([
            GitLabSyncBaseline(project=project, repository_path=path, initial_shas=shas)
            for path, shas in snapshots.items()
        ])
        config.enabled = True
        config.interval_seconds = interval_seconds
        config.activated_at = now
        config.next_sync_at = now + timedelta(seconds=interval_seconds)
        config.last_error = ''
        config.consecutive_failures = 0
        config.sync_state = 'idle'
        config.sync_started_at = None
        config.last_result = {}
        config.save()


def sync_status(config):
    baselines = GitLabSyncBaseline.objects.filter(project=config.project)
    return {
        'enabled': config.enabled, 'interval_seconds': config.interval_seconds,
        'activated_at': config.activated_at, 'next_sync_at': config.next_sync_at,
        'last_sync_at': config.last_sync_at, 'last_success_at': config.last_success_at,
        'sync_state': config.sync_state, 'last_error': config.last_error,
        'last_result': config.last_result, 'consecutive_failures': config.consecutive_failures,
        'baseline_mrs': sum(len(row.initial_shas) for row in baselines),
        'in_progress': bool(config.sync_started_at and config.sync_state == 'syncing'
                            and timezone.now() - config.sync_started_at < timedelta(seconds=120)),
    }


def synchronize_project(config, *, force=False):
    # Import locally to keep the shared enqueue helper independent of sync configuration reads.
    from review.services.automatic_reviews import enqueue_automatic_review

    try:
        with resource_lock(f'gitlab-sync:{config.project_id}'):
            config.refresh_from_db()
            project = Project.objects.get(pk=config.project_id)
            now = timezone.now()
            if not config.enabled or not project.enabled:
                return {'skipped': True, 'enqueued': 0}
            if not force and config.next_sync_at and config.next_sync_at > now:
                return {'skipped': True, 'enqueued': 0}
            config.sync_started_at = now
            config.sync_state = 'syncing'
            config.save(update_fields=('sync_started_at', 'sync_state', 'updated_at'))
            results, enqueued, errors = [], 0, []
            paths = enabled_repository_paths(project)
            if not paths:
                errors.append('没有已启用的 GitLab 仓库')
            for path in paths:
                result = {'path': path, 'opened_mrs': 0, 'enqueued': 0, 'baselined': False, 'error': ''}
                try:
                    items = read_open_merge_requests(project, path, ReadBudget(100, 15, 5))
                    result['opened_mrs'] = len(items)
                    baseline, created = GitLabSyncBaseline.objects.get_or_create(
                        project=project, repository_path=path,
                        defaults={'initial_shas': {str(item['iid']): item['sha'] for item in items}},
                    )
                    if created:
                        result['baselined'] = True
                    else:
                        for item in items:
                            job, new = enqueue_automatic_review(project, path, item, Review.Source.SYNC)
                            if new:
                                enqueued += 1
                                result['enqueued'] += 1
                                AuditLog.objects.create(project=project, action='定时同步触发评审',
                                    target=f'{path}!{item["iid"]}', metadata={'job_id': str(job.pk), 'head_sha': item['sha']})
                except Exception:
                    result['error'] = '无法完成仓库同步，请检查 GitLab 连接、权限或稍后重试'
                    errors.append(result['error'])
                results.append(result)
            finished = timezone.now()
            config.last_sync_at = finished
            config.last_result = {'enqueued': enqueued, 'repositories': results, 'checked_at': finished.isoformat()}
            config.last_error = errors[0] if errors else ''
            config.sync_state = 'error' if errors else 'success'
            config.consecutive_failures = config.consecutive_failures + 1 if errors else 0
            if not errors:
                config.last_success_at = finished
            delay = min(config.interval_seconds * 2 ** min(config.consecutive_failures, 8), 900) if errors else config.interval_seconds
            config.next_sync_at = finished + timedelta(seconds=delay)
            config.sync_started_at = None
            config.save(update_fields=('last_sync_at', 'last_success_at', 'last_result', 'last_error',
                'sync_state', 'consecutive_failures', 'next_sync_at', 'sync_started_at', 'updated_at'))
            return config.last_result
    except ResourceBusy:
        return {'skipped': True, 'enqueued': 0}
