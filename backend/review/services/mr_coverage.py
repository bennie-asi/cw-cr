"""Repository-scoped MR coverage and review state, without external writes."""
from django.conf import settings
from django.db.models import Count, Prefetch, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from review.integrations.gitlab import GitLabClient
from review.models import Review, ReviewJob
from review.services.read_budget import ReadBudget, ReadBudgetExhausted


def enabled_repository_paths(project):
    rows = list(project.repositories.all())
    paths = [row.path for row in rows if row.enabled]
    # An explicit disabled primary row must not be resurrected by the legacy fallback.
    if project.gitlab_project_path and not any(row.path == project.gitlab_project_path for row in rows):
        paths.insert(0, project.gitlab_project_path)
    return paths


def repository_reviews(project, repository_path):
    identity = Q(repository_path=repository_path)
    if repository_path == project.gitlab_project_path:
        identity |= Q(repository_path='')
    return Review.objects.filter(project=project).filter(identity).exclude(source=Review.Source.IMPROVE)


def prefetch_review_state(queryset):
    active_jobs = ReviewJob.objects.filter(status__in=(ReviewJob.Status.QUEUED, ReviewJob.Status.RUNNING))
    return queryset.select_related('project', 'project__policy').prefetch_related(
        'comments', 'jobs',
        Prefetch('project__jobs', queryset=active_jobs.select_related('review').order_by('-created_at', '-id'),
                 to_attr='active_review_jobs'),
    )


def active_mr_jobs(project, repository_path, mr_iids=None):
    jobs = getattr(project, 'active_review_jobs', None)
    if jobs is None:
        jobs = ReviewJob.objects.filter(project=project, status__in=(ReviewJob.Status.QUEUED, ReviewJob.Status.RUNNING))
        jobs = jobs.select_related('review').order_by('-created_at', '-id')
    result = {}
    for job in jobs:
        review = job.review
        payload = job.payload if isinstance(job.payload, dict) else {}
        path = payload.get('repository_path') or (review.repository_path if review else '') or project.gitlab_project_path
        raw_iid = payload.get('mr_iid') or (review.mr_iid if review else None)
        try:
            iid = int(raw_iid)
        except (TypeError, ValueError):
            continue
        if path == repository_path and (mr_iids is None or iid in mr_iids):
            result.setdefault(iid, job)
    return result


def enrich_merge_requests(project, repository_path, items):
    iids = {item['iid'] for item in items}
    latest = {}
    for review in repository_reviews(project, repository_path).filter(mr_iid__in=iids).annotate(
        issue_count=Count(Coalesce('comments__issue_id', 'comments__id'), distinct=True),
    ).order_by('-created_at', '-id'):
        latest.setdefault(review.mr_iid, review)
    active = active_mr_jobs(project, repository_path, iids)
    results = []
    for item in items:
        review = latest.get(item['iid'])
        job = active.get(item['iid'])
        if job and job.review_id:
            candidate = job.review
            if (candidate.project_id == project.pk
                    and (candidate.repository_path or project.gitlab_project_path) == repository_path):
                review = candidate
        state = 'unreviewed'
        issue_count = None
        if job:
            state = job.status
        elif not item.get('sha'):
            state = 'unknown'
        elif review:
            if review.status in (Review.Status.QUEUED, Review.Status.RUNNING):
                state = review.status
            elif review.head_sha != item['sha']:
                state = 'stale'
            elif review.status in (Review.Status.COMPLETED, Review.Status.BLOCKED):
                state = 'reviewed'
                issue_count = review.issue_count
            elif review.status == Review.Status.FAILED:
                state = 'failed'
            else:
                state = 'unknown'
        results.append({
            **item, 'review_state': state, 'review_id': str(review.pk) if review else None,
            'review_sha': review.head_sha or None if review else None, 'issue_count': issue_count,
        })
    return results


def covered_merge_requests(project, repository_path, items):
    current = {(item['iid'], item['sha']) for item in items if item.get('sha')}
    if not current:
        return 0
    reviewed = set(repository_reviews(project, repository_path).filter(
        mr_iid__in={iid for iid, _ in current},
        head_sha__in={sha for _, sha in current},
        status__in=(Review.Status.COMPLETED, Review.Status.BLOCKED),
    ).values_list('mr_iid', 'head_sha'))
    return len(current & reviewed)


def collect_mr_coverage(projects):
    budget = ReadBudget(
        getattr(settings, 'MR_COVERAGE_MAX_REQUESTS', 50),
        getattr(settings, 'MR_COVERAGE_MAX_SECONDS', 15),
        getattr(settings, 'MR_COVERAGE_REQUEST_TIMEOUT', 3),
    )
    repositories = [(project, path) for project in projects.prefetch_related('repositories')
                    for path in enabled_repository_paths(project)]
    open_mrs = reviewed_mrs = checked = successful_pages = 0
    errors = []

    def error(path, detail):
        entry = {'path': path, 'detail': detail}
        if entry not in errors:
            errors.append(entry)

    for index, (project, path) in enumerate(repositories):
        try:
            timeout = budget.next_timeout()
            client = GitLabClient(project, timeout=timeout, repository_path=path)
            page = 1
            seen = set()
            while True:
                items, has_next = budget.run(lambda: client.list_merge_requests(page, 'opened'))
                successful_pages += 1
                fresh = []
                for item in items:
                    if item['iid'] in seen:
                        error(path, 'MR 分页内容发生变化，结果不完整，请刷新')
                        continue
                    seen.add(item['iid'])
                    fresh.append(item)
                open_mrs += len(fresh)
                reviewed_mrs += covered_merge_requests(project, path, fresh)
                if any(not item.get('sha') for item in fresh):
                    error(path, '部分 MR 缺少当前提交 SHA，无法确认完整覆盖率')
                budget.check_deadline()
                if not has_next:
                    checked += 1
                    break
                page += 1
                client.timeout = budget.next_timeout()
        except ReadBudgetExhausted:
            for _, unchecked_path in repositories[index:]:
                error(unchecked_path, '读取达到请求次数或时间上限，结果不完整，请稍后刷新')
            break
        except Exception:
            # Never return an upstream body, request URL, or exception text.
            error(path, '无法读取 GitLab MR，请检查连接、访问权限和仓库路径')
    state = 'complete' if not errors else ('partial' if successful_pages else 'unavailable')
    return {
        'state': state, 'open_mrs': open_mrs, 'reviewed_mrs': reviewed_mrs,
        'unreviewed_mrs': open_mrs - reviewed_mrs,
        'percentage': round(reviewed_mrs * 100 / open_mrs, 1) if state == 'complete' and open_mrs else None,
        'checked_at': timezone.now().isoformat(), 'repositories_checked': checked,
        'repositories_total': len(repositories), 'errors': errors,
    }
