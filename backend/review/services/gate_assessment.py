"""Read-only assessment of an explicit review against server-fetched GitLab data."""
from review.models import LineComment, ProjectPolicy, Review, distinct_issue_count


def assess_review(review: Review, policy: ProjectPolicy, current_mr: dict) -> dict:
    """Callers must fetch current_mr from GitLab, never accept it from browser input.

    This result is a point-in-time assessment, not permission to perform a later
    merge. The merge path must recheck current state and submit the reviewed SHA.
    """
    if policy.project_id != review.project_id:
        raise ValueError('评审和策略必须属于同一项目')

    levels = policy.blocking_levels
    blocking = distinct_issue_count(review.comments.filter(level__in=levels).exclude(
        status=LineComment.Status.RESOLVED,
    ))
    raw_sha = current_mr.get('sha')
    current_sha = raw_sha if isinstance(raw_sha, str) else ''
    checks = {
        'project_enabled': review.project.enabled,
        'review_ready': review.status in (Review.Status.COMPLETED, Review.Status.BLOCKED),
        'summary_ready': bool(review.summary.strip()) and review.summary_sync_status == 'synced',
        'latest_review': not Review.objects.filter(
            project_id=review.project_id, repository_path=review.repository_path, mr_iid=review.mr_iid,
            created_at__gt=review.created_at,
        ).exists(),
        'blockers_resolved': blocking == 0,
        'sha_matches': bool(review.head_sha) and review.head_sha == current_sha,
        'correct_mr': type(current_mr.get('iid')) is int and current_mr['iid'] == review.mr_iid,
        'gitlab_ready': (
            current_mr.get('state') == 'opened'
            and current_mr.get('draft') is False
            and current_mr.get('work_in_progress', False) is False
            and current_mr.get('has_conflicts') is False
            and current_mr.get('detailed_merge_status') == 'mergeable'
        ),
    }
    labels = {
        'project_enabled': '项目已暂停',
        'review_ready': '评审尚未成功完成',
        'summary_ready': '总评为空或尚未同步到 GitLab',
        'latest_review': '同一 MR 存在更新的评审',
        'blockers_resolved': f'仍有 {blocking} 个阻断或未评级评论未解决',
        'sha_matches': '评审 SHA 缺失或与 GitLab 当前提交不一致',
        'correct_mr': 'GitLab 返回的 MR 编号不匹配',
        'gitlab_ready': 'GitLab 合并条件尚未就绪或状态未知',
    }
    failures = [message for key, message in labels.items() if not checks[key]]
    return {
        'project_id': str(review.project_id), 'review_id': str(review.pk),
        'mr_iid': review.mr_iid, 'reviewed_sha': review.head_sha,
        'current_sha': current_sha, 'blocking_levels': levels,
        'blocking_count': blocking, 'checks': checks,
        'passed': not failures,
        'reason': '；'.join(failures) if failures else '门禁检查通过',
    }
