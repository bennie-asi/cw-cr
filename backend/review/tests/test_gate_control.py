from copy import deepcopy
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from review.models import GitLabConfig, LineComment, Project, ProjectPolicy, Review
from review.services.gate_control import evaluate_live, fingerprint, proof_matches, store_assessment


@override_settings(GITLAB_URL='', GITLAB_TOKEN='', REVIEW_ALLOW_TEST_FALLBACK=True)
class GateControlTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='gate', gitlab_project_path='qa/gate')
        self.policy = ProjectPolicy.objects.create(project=self.project)
        self.review = Review.objects.create(
            project=self.project, mr_iid=42, status='completed',
            summary='中文总评', summary_sync_status='synced', head_sha='a' * 40,
        )
        self.config = GitLabConfig.objects.create(base_url='https://gitlab.test', api_token='test-only')
        self.remote = {
            'iid': 42, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
            'has_conflicts': False, 'detailed_merge_status': 'mergeable',
        }
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('No real HTTP'))
        network.start()
        self.addCleanup(network.stop)

    def evaluate(self, payload=None):
        with patch('review.integrations.gitlab.requests.get', return_value=Mock(json=lambda: self.remote if payload is None else payload)) as get:
            result = evaluate_live(self.review, self.policy)
        return result, get

    def store(self):
        result, _ = self.evaluate()
        return store_assessment(self.policy, self.review, result)

    def test_live_success_and_persisted_identity(self):
        result, get = self.evaluate()
        self.assertTrue(result['passed'])
        self.assertEqual(get.call_args.args[0], 'https://gitlab.test/api/v4/projects/qa%2Fgate/merge_requests/42')
        self.assertEqual(get.call_count, 1)
        saved = store_assessment(self.policy, self.review, result)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.dry_run_assessment, saved)
        self.assertEqual(saved['review_id'], str(self.review.pk))
        self.assertEqual(saved['reviewed_sha'], self.review.head_sha)
        self.assertEqual(saved['completed_at'], self.policy.dry_run_completed_at.isoformat())
        self.assertTrue(proof_matches(self.policy, self.review))
        self.assertFalse(self.policy.auto_merge_enabled)

    def test_fingerprint_changes_only_with_review_policy_or_repository(self):
        baseline = fingerprint(self.policy)
        for field in ('webhook_trigger', 'command_review', 'command_improve', 'security_checks',
                      'reliability_checks', 'style_checks', 'block_on_p1', 'summary_prompt', 'engine'):
            original = getattr(self.policy, field)
            setattr(self.policy, field, not original if isinstance(original, bool) else 'changed')
            self.assertNotEqual(fingerprint(self.policy), baseline, field)
            setattr(self.policy, field, original)
        for field in ('gitlab_project_path', 'gitlab_project_id', 'gitlab_url'):
            original = getattr(self.project, field)
            setattr(self.project, field, 'changed')
            self.assertNotEqual(fingerprint(self.policy), baseline, field)
            setattr(self.project, field, original)
        self.policy.auto_merge_enabled = True
        self.policy.dry_run_passed = True
        self.assertEqual(fingerprint(self.policy), baseline)
        self.config.base_url = 'https://other.test'
        self.config.save()
        self.assertNotEqual(fingerprint(self.policy), baseline)

    def test_unconfigured_cannot_use_test_fallback(self):
        self.config.enabled = False
        self.config.save()
        result, get = self.evaluate()
        self.assertFalse(result['passed'])
        self.assertTrue(result['remote_error'])
        get.assert_not_called()

    def test_remote_errors_and_malformed_responses_fail_without_secrets(self):
        for exc in (requests.Timeout('secret-token'), requests.HTTPError('secret-token'), ValueError('secret-token')):
            with self.subTest(exc=type(exc).__name__), patch('review.integrations.gitlab.requests.get', side_effect=exc):
                result = evaluate_live(self.review, self.policy)
                self.assertFalse(result['passed'])
                self.assertTrue(result['remote_error'])
                self.assertNotIn('secret-token', str(result))
        for payload in ([], None, 'bad'):
            with self.subTest(payload=payload), patch('review.integrations.gitlab.requests.get', return_value=Mock(json=lambda: payload)):
                self.assertFalse(evaluate_live(self.review, self.policy)['passed'])

    def test_inflight_policy_change_blocks(self):
        def remote(*args, **kwargs):
            ProjectPolicy.objects.filter(pk=self.policy.pk).update(summary_prompt='changed')
            return Mock(json=lambda: self.remote)
        with patch('review.integrations.gitlab.requests.get', side_effect=remote):
            result = evaluate_live(self.review, self.policy)
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['configuration_unchanged'])

    def test_inflight_local_review_change_is_observed(self):
        def remote(*args, **kwargs):
            Review.objects.filter(pk=self.review.pk).update(head_sha='b' * 40)
            LineComment.objects.create(review=self.review, file_path='a.py', level='P0', title='block', body='block')
            return Mock(json=lambda: self.remote)
        with patch('review.integrations.gitlab.requests.get', side_effect=remote):
            result = evaluate_live(self.review, self.policy)
        self.assertFalse(result['checks']['sha_matches'])
        self.assertFalse(result['checks']['blockers_resolved'])

    def test_inflight_repository_change_blocks_old_remote_response(self):
        def remote(*args, **kwargs):
            Project.objects.filter(pk=self.project.pk).update(gitlab_project_path='qa/rebound')
            return Mock(json=lambda: self.remote)
        with patch('review.integrations.gitlab.requests.get', side_effect=remote):
            result = evaluate_live(self.review, self.policy)
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['configuration_unchanged'])

    def test_store_rejects_foreign_result_without_writes(self):
        result, _ = self.evaluate()
        for key in ('project_id', 'review_id'):
            invalid = {**result, key: 'foreign'}
            with self.subTest(key=key), self.assertRaises(ValueError):
                store_assessment(self.policy, self.review, invalid)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.dry_run_assessment, {})

    def test_proof_requires_complete_matching_snapshot(self):
        saved = self.store()
        for invalid in ({}, None, [], {'passed': True}):
            self.policy.dry_run_assessment = invalid
            self.assertFalse(proof_matches(self.policy, self.review))
        for field, value in (
            ('passed', False), ('project_id', 'other'), ('review_id', 'other'), ('mr_iid', 43),
            ('reviewed_sha', 'b' * 40), ('current_sha', ''), ('policy_fingerprint', 'old'),
            ('completed_at', 'old'),
        ):
            self.policy.dry_run_assessment = {**deepcopy(saved), field: value}
            self.assertFalse(proof_matches(self.policy, self.review), field)
        self.policy.dry_run_assessment = saved
        self.policy.dry_run_passed = False
        self.assertFalse(proof_matches(self.policy, self.review))
        self.policy.dry_run_passed = True
        self.policy.dry_run_completed_at = None
        self.assertFalse(proof_matches(self.policy, self.review))
        self.policy.dry_run_completed_at = timezone.now()
        self.assertFalse(proof_matches(self.policy, self.review))

    def test_foreign_policy_is_rejected(self):
        other = Project.objects.create(name='other', gitlab_project_path='qa/other')
        other_policy = ProjectPolicy.objects.create(project=other)
        with self.assertRaises(ValueError):
            evaluate_live(self.review, other_policy)
        result, _ = self.evaluate()
        with self.assertRaises(ValueError):
            store_assessment(other_policy, self.review, result)
        self.assertFalse(proof_matches(other_policy, self.review))
