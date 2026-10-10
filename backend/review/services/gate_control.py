"""Live, read-only GitLab assessment and server-owned dry-run evidence."""

import hashlib
import json

import requests
from django.conf import settings
from django.utils import timezone

from review.integrations.errors import IntegrationNotConfiguredError, safe_error
from review.integrations.gitlab import GitLabClient
from review.engines.registry import effective_engine
from review.models import GitLabConfig
from review.services.gate_assessment import assess_review


def fingerprint(policy):
    values = {key: getattr(policy, key) for key in (
        'webhook_trigger', 'command_review', 'command_improve', 'security_checks',
        'reliability_checks', 'style_checks', 'block_on_p1', 'block_on_p2', 'summary_prompt', 'engine',
        'security_prompt', 'reliability_prompt', 'style_prompt',
    )}
    values['repository'] = {key: getattr(policy.project, key) for key in (
        'gitlab_url', 'gitlab_project_path', 'gitlab_project_id',
    )}
    values['repositories'] = list(policy.project.repositories.order_by('position', 'id').values(
        'path', 'gitlab_project_id', 'gitlab_url', 'enabled', 'position',
    ))
    values['effective_engine'] = effective_engine(policy.project)
    values['engine_inherit'] = policy.project.engine_inherit
    values['global_gitlab_url'] = (
        GitLabConfig.objects.filter(pk=1).values_list('base_url', flat=True).first()
        or settings.GITLAB_URL
    )
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def evaluate_live(review, policy):
    if review.project_id != policy.project_id:
        raise ValueError('评审和策略必须属于同一项目')
    review.refresh_from_db()
    policy.refresh_from_db()
    before = fingerprint(policy)
    error = ''
    current = {}
    try:
        client = GitLabClient(review.project, repository_path=review.repository_path)
        if not client.remote_enabled:
            raise IntegrationNotConfiguredError('GitLab 未配置完整连接信息')
        current = client.fetch_merge_request(review.mr_iid, {})
        if not isinstance(current, dict):
            raise ValueError('Invalid GitLab response')
    except Exception as exc:
        current = {}
        error = safe_error(exc) if isinstance(exc, (requests.RequestException, IntegrationNotConfiguredError)) else '无法验证 GitLab 当前 MR 状态'
    # HTTP can take seconds: never evaluate cached local state after it returns.
    review.refresh_from_db()
    policy.refresh_from_db()
    result = assess_review(review, policy, current)
    result['checks']['gitlab_access'] = not bool(error)
    result['checks']['configuration_unchanged'] = before == fingerprint(policy)
    result['remote_error'] = bool(error)
    result['policy_fingerprint'] = before
    if error:
        result.update(passed=False, reason=error)
    elif not result['checks']['configuration_unchanged']:
        result.update(passed=False, reason='验证期间项目策略或仓库连接发生变化，请重新验证')
    return result


def store_assessment(policy, review, result):
    if (
        review.project_id != policy.project_id
        or result.get('project_id') != str(policy.project_id)
        or result.get('review_id') != str(review.pk)
    ):
        raise ValueError('验证结果与项目或评审不匹配')
    now = timezone.now()
    snapshot = {**result, 'completed_at': now.isoformat()}
    policy.dry_run_assessment = snapshot
    policy.dry_run_passed = result['passed'] is True
    policy.dry_run_completed_at = now
    policy.dry_run_reason = result['reason']
    policy.save(update_fields=(
        'dry_run_assessment', 'dry_run_passed', 'dry_run_completed_at', 'dry_run_reason', 'updated_at',
    ))
    return snapshot


def proof_matches(policy, review):
    proof = policy.dry_run_assessment
    if not isinstance(proof, dict) or not policy.dry_run_passed or not policy.dry_run_completed_at:
        return False
    expected = {
        'project_id': str(policy.project_id), 'review_id': str(review.pk),
        'mr_iid': review.mr_iid, 'reviewed_sha': review.head_sha,
        'current_sha': review.head_sha, 'policy_fingerprint': fingerprint(policy),
        'completed_at': policy.dry_run_completed_at.isoformat(),
    }
    return (
        review.project_id == policy.project_id and bool(review.head_sha)
        and proof.get('passed') is True
        and all(proof.get(key) == value for key, value in expected.items())
    )
