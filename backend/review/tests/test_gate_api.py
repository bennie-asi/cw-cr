from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from review.models import AuditLog, GitLabConfig, LineComment, Project, ProjectMembership, ProjectPolicy, Review
from review.services.execution_locks import resource_lock
from review.tests.auth_helpers import admin_client


@override_settings(GITLAB_URL='', GITLAB_TOKEN='', REVIEW_ALLOW_TEST_FALLBACK=True)
class ExplicitGateApiTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='gate', gitlab_project_path='qa/gate')
        self.policy = ProjectPolicy.objects.create(project=self.project)
        self.review = Review.objects.create(
            project=self.project, mr_iid=42, status='completed', summary='中文总评',
            summary_sync_status='synced', head_sha='a' * 40,
        )
        GitLabConfig.objects.create(base_url='https://gitlab.test', api_token='test-only')
        self.remote = {
            'iid': 42, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
            'has_conflicts': False, 'detailed_merge_status': 'mergeable',
        }
        self.gate_url = f'/api/merge-gate/?project={self.project.pk}'
        self.run_url = f'/api/merge-gate/dry-run/?project={self.project.pk}'
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('No real HTTP'))
        network.start()
        self.addCleanup(network.stop)
        get = patch('review.integrations.gitlab.requests.get', side_effect=lambda *a, **kw: Mock(json=lambda: self.remote))
        self.get = get.start()
        self.addCleanup(get.stop)

    def dry_run(self):
        return self.client.post(self.run_url, {'review_id': str(self.review.pk)}, format='json')

    def enable(self):
        return self.client.patch(self.gate_url, {'auto_merge_enabled': True}, format='json')

    def test_explicit_selection_not_arbitrary_latest_and_get_snapshot(self):
        Review.objects.create(project=self.project, mr_iid=99, status='failed')
        response = self.dry_run()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['passed'])
        self.assertEqual(response.data['review_id'], str(self.review.pk))
        self.assertEqual(response.data['mr_iid'], 42)
        self.assertEqual(response.data['current_sha'], self.review.head_sha)
        self.assertTrue(response.data['checks']['gitlab_ready'])
        self.get.reset_mock()
        read = self.client.get(self.gate_url)
        self.assertEqual(read.data['assessment'], response.data)
        self.get.assert_not_called()
        self.assertTrue(AuditLog.objects.filter(project=self.project, action='执行门禁 dry-run').exists())

    def test_missing_malformed_extra_or_foreign_review_rejected(self):
        for data in ({}, {'review_id': 'bad'}, {'review_id': None}, [],
                     {'review_id': str(self.review.pk), 'sha': self.review.head_sha}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.run_url, data, format='json').status_code, 400)
        other = Project.objects.create(name='other', gitlab_project_path='qa/other')
        foreign = Review.objects.create(project=other, mr_iid=42)
        for pk in (foreign.pk, uuid4()):
            self.assertEqual(self.client.post(self.run_url, {'review_id': str(pk)}, format='json').status_code, 404)
        self.get.assert_not_called()
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.dry_run_assessment, {})

    def test_remote_failure_clears_previous_pass_and_sanitizes(self):
        self.assertTrue(self.dry_run().data['passed'])
        self.get.side_effect = requests.Timeout('secret-token')
        response = self.dry_run()
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.data['passed'])
        self.assertNotIn('secret-token', str(response.data))
        self.policy.refresh_from_db()
        self.assertFalse(self.policy.dry_run_passed)

    def test_enable_is_independent_of_previous_mr_sha_and_never_merges(self):
        self.assertTrue(self.dry_run().data['passed'])
        self.remote['sha'] = 'b' * 40
        self.get.reset_mock()
        response = self.enable()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['enabled'])
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.auto_merge_enabled)
        self.get.assert_not_called()
        self.assertTrue(AuditLog.objects.filter(project=self.project, action='更新自动合并门禁').exists())

    def test_legacy_corrupt_and_stale_preview_does_not_limit_project_switch(self):
        for proof in ({}, [], {'review_id': 'bad'}, {'review_id': str(uuid4())}):
            ProjectPolicy.objects.filter(pk=self.policy.pk).update(dry_run_passed=True, dry_run_assessment=proof)
            self.assertEqual(self.enable().status_code, 200)
        self.get.assert_not_called()
        self.assertTrue(self.dry_run().data['passed'])
        ProjectPolicy.objects.filter(pk=self.policy.pk).update(block_on_p1=False)
        self.get.reset_mock()
        self.assertEqual(self.enable().status_code, 200)
        self.get.assert_not_called()

    def test_mr_blocker_or_unsynced_summary_does_not_block_project_configuration(self):
        LineComment.objects.create(review=self.review, file_path='a.py', level='P0', title='block', body='block')
        self.assertEqual(self.enable().status_code, 200)
        self.assertFalse(self.dry_run().data['passed'])
        Review.objects.filter(pk=self.review.pk).update(summary_sync_status='failed')
        self.assertFalse(self.dry_run().data['passed'])
        self.assertTrue(self.client.get(self.gate_url).data['enabled'])

    def test_toggle_works_without_network_when_gitlab_is_unavailable(self):
        self.get.side_effect = requests.ConnectionError('secret-token')
        self.assertEqual(self.enable().status_code, 200)
        self.get.reset_mock()
        ProjectPolicy.objects.filter(pk=self.policy.pk).update(auto_merge_enabled=True)
        response = self.client.patch(self.gate_url, {'auto_merge_enabled': False}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['enabled'])
        self.assertEqual(self.client.patch(self.gate_url, {}, format='json').status_code, 200)
        self.get.assert_not_called()

    def test_dry_run_and_toggle_are_serialized_by_project_lock(self):
        self.assertTrue(self.dry_run().data['passed'])
        self.assertEqual(self.enable().status_code, 200)
        self.get.reset_mock()
        with resource_lock(f'gate:{self.project.pk}'):
            self.assertEqual(self.dry_run().status_code, 409)
            self.assertEqual(self.enable().status_code, 409)
            self.assertEqual(self.client.post(f'/api/reviews/{self.review.pk}/merge/').status_code, 409)
        self.get.assert_not_called()

    def test_normal_policy_save_cannot_race_gate_operation(self):
        with resource_lock(f'gate:{self.project.pk}'):
            response = self.client.patch(
                f'/api/projects/{self.project.pk}/policy/', {'block_on_p1': False}, format='json',
            )
        self.assertEqual(response.status_code, 409)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.block_on_p1)

    def test_proof_fields_cannot_be_forged_through_either_endpoint(self):
        for path in (self.gate_url, f'/api/projects/{self.project.pk}/policy/'):
            response = self.client.patch(path, {'dry_run_assessment': {'passed': True}}, format='json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.patch(self.gate_url, [], format='json').status_code, 400)

    def test_scope_roles_checked_before_reading_gitlab(self):
        self.client = APIClient()
        self.assertEqual(self.dry_run().status_code, 401)
        user = get_user_model().objects.create_user('gate-member')
        ProjectMembership.objects.create(project=self.project, user=user, role='member')
        self.client.force_authenticate(user)
        self.assertEqual(self.dry_run().status_code, 403)
        self.assertEqual(self.enable().status_code, 403)
        self.get.assert_not_called()
        ProjectMembership.objects.filter(user=user).update(role='admin')
        self.assertEqual(self.dry_run().status_code, 200)
        self.assertEqual(self.enable().status_code, 200)

    def test_toggle_only_updates_execution_switch_and_preserves_other_policy_fields(self):
        ProjectPolicy.objects.filter(pk=self.policy.pk).update(summary_prompt='已保存的要求')
        self.assertEqual(self.enable().status_code, 200)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.summary_prompt, '已保存的要求')
        self.assertTrue(self.policy.auto_merge_enabled)
        self.get.assert_not_called()

    @patch('review.integrations.gitlab.requests.put')
    def test_merge_execution_requires_proof_even_with_legacy_exemption(self, put):
        ProjectPolicy.objects.filter(pk=self.policy.pk).update(
            auto_merge_enabled=True, dry_run_required=False, dry_run_passed=True,
        )
        response = self.client.post(f'/api/reviews/{self.review.pk}/merge/')
        self.assertEqual(response.status_code, 409)
        put.assert_not_called()
        self.get.assert_not_called()

    @patch('review.integrations.gitlab.requests.put')
    def test_merge_execution_rechecks_sha_before_guarded_put(self, put):
        self.assertTrue(self.dry_run().data['passed'])
        self.assertEqual(self.enable().status_code, 200)
        self.remote['sha'] = 'b' * 40
        self.assertEqual(self.client.post(f'/api/reviews/{self.review.pk}/merge/').status_code, 409)
        put.assert_not_called()
        self.remote['sha'] = 'a' * 40
        self.assertTrue(self.dry_run().data['passed'])
        put.return_value.json.return_value = {'state': 'merged'}
        response = self.client.post(f'/api/reviews/{self.review.pk}/merge/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['merged'])
        self.assertEqual(put.call_count, 1)
        self.assertEqual(put.call_args.kwargs['json']['sha'], 'a' * 40)

    @patch('review.integrations.gitlab.requests.put')
    def test_merge_execution_remote_error_never_puts(self, put):
        self.assertTrue(self.dry_run().data['passed'])
        self.assertEqual(self.enable().status_code, 200)
        self.get.side_effect = requests.Timeout('secret')
        response = self.client.post(f'/api/reviews/{self.review.pk}/merge/')
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.data['merged'])
        put.assert_not_called()

    @patch('review.integrations.gitlab.requests.put')
    def test_merge_stops_if_disabled_during_remote_validation(self, put):
        self.assertTrue(self.dry_run().data['passed'])
        self.assertEqual(self.enable().status_code, 200)
        def remote(*args, **kwargs):
            ProjectPolicy.objects.filter(pk=self.policy.pk).update(auto_merge_enabled=False)
            return Mock(json=lambda: self.remote)
        self.get.side_effect = remote
        self.assertEqual(self.client.post(f'/api/reviews/{self.review.pk}/merge/').status_code, 409)
        put.assert_not_called()
