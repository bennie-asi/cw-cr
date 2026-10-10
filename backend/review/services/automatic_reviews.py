"""One automatic full-review task per repository/MR/commit across trigger paths."""
import hashlib
import json
import re

from django.db.models import Q

from review.models import GitLabSyncBaseline, Project, ProjectGitLabSync, Review, ReviewJob
from review.services.execution_locks import resource_lock
from review.services.mr_coverage import active_mr_jobs, enabled_repository_paths
from review.services.review_runner import _create_locked_review_job, _default_payload


def enqueue_automatic_review(project, repository_path, mr, source, payload=None):
    iid = mr.get('iid')
    refs = mr.get('diff_refs') or {}
    sha = mr.get('sha') or refs.get('head_sha')
    if isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0:
        raise ValueError('GitLab MR IID 无效')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{40,64}', sha):
        raise ValueError('GitLab MR 提交 SHA 无效')
    if mr.get('state') != 'opened' or mr.get('draft', mr.get('work_in_progress', False)) is True:
        return None, False
    with resource_lock(f'mr-job:{project.pk}:{repository_path}:{iid}'):
        if not Project.objects.filter(pk=project.pk, enabled=True).exists() or repository_path not in enabled_repository_paths(project):
            return None, False
        if ProjectGitLabSync.objects.filter(project=project, enabled=True).exists():
            baseline = GitLabSyncBaseline.objects.filter(project=project, repository_path=repository_path).first()
            # A newly attached repository must be baselined before either automatic trigger processes it.
            if baseline is None or baseline.initial_shas.get(str(iid)) == sha:
                return None, False
        active = active_mr_jobs(project, repository_path, {iid})
        if iid in active:
            return active[iid], False
        identity = Q(review__repository_path=repository_path)
        if repository_path == project.gitlab_project_path:
            identity |= Q(review__repository_path='')
        completed = ReviewJob.objects.filter(
            project=project, kind=ReviewJob.Kind.REVIEW, status=ReviewJob.Status.SUCCEEDED,
            review__mr_iid=iid, review__head_sha=sha,
            review__status__in=(Review.Status.COMPLETED, Review.Status.BLOCKED),
        ).filter(identity).select_related('review').order_by('-created_at').first()
        if completed:
            return completed, False
        event_identity = [str(project.pk), repository_path, iid, sha, ReviewJob.Kind.REVIEW]
        event_uuid = 'automatic:' + hashlib.sha256(json.dumps(event_identity).encode()).hexdigest()
        values = {
            **(payload or {}), 'mr_iid': iid, 'repository_path': repository_path,
            'source': source, 'event_uuid': event_uuid, 'automatic_head_sha': sha,
            'title': mr.get('title', ''), 'branch': mr.get('source_branch', ''),
            'gitlab_url': mr.get('web_url', ''),
        }
        return _create_locked_review_job(project, _default_payload(project, values, ReviewJob.Kind.REVIEW), ReviewJob.Kind.REVIEW)
