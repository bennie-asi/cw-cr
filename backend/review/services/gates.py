from __future__ import annotations

from dataclasses import dataclass

from review.models import LineComment, ProjectPolicy, Review, distinct_issue_count


@dataclass(frozen=True)
class GateResult:
    passed: bool
    status: str
    reason: str


def evaluate_gate(review: Review, policy: ProjectPolicy | None = None) -> GateResult:
    levels = policy.blocking_levels if policy else review.blocking_levels
    cached = getattr(review, '_prefetched_objects_cache', {}).get('comments')
    if cached is not None:
        pending = list({comment.issue_id or comment.pk: comment for comment in cached
                        if comment.status != LineComment.Status.RESOLVED}.values())
        unclassified = sum(comment.level == LineComment.Level.UNRATED for comment in pending)
    else:
        unclassified = distinct_issue_count(review.comments.filter(level=LineComment.Level.UNRATED).exclude(
            status=LineComment.Status.RESOLVED,
        ))
    if unclassified:
        return GateResult(
            passed=False,
            status=Review.GateStatus.BLOCKED,
            reason=f'还有 {unclassified} 个未评级行级评论需人工确认',
        )
    blocking_levels = [level for level in levels
                       if level != LineComment.Level.UNRATED]
    blocking = (sum(comment.level in blocking_levels for comment in pending) if cached is not None else
                distinct_issue_count(review.comments.filter(level__in=blocking_levels).exclude(status=LineComment.Status.RESOLVED)))
    if blocking:
        levels_label = '/'.join(blocking_levels)
        return GateResult(
            passed=False,
            status=Review.GateStatus.BLOCKED,
            reason=f'还有 {blocking} 个 {levels_label} 行级评论未解决',
        )
    return GateResult(
        passed=True,
        status=Review.GateStatus.PASSED,
        reason='所有阻断级行级评论已解决',
    )
