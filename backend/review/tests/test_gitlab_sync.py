from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from review.models import GitLabConfig, GitLabSyncBaseline, Project, ProjectGitLabSync, ProjectMembership, ProjectPolicy, ProjectRepository, Review, ReviewJob
from review.services import gitlab_sync
from review.services.review_runner import create_review_job


def mr(iid=1, sha='a' * 40, draft=False):
    return {'iid': iid, 'sha': sha, 'state': 'opened', 'draft': draft,
            'title': f'MR {iid}', 'source_branch': 'feature/example', 'target_branch': 'main',
            'web_url': f'https://gitlab.example.com/team/backend/-/merge_requests/{iid}'}


@override_settings(REVIEW_EXECUTE_INLINE=False)
class GitLabSyncAPITests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Demo', gitlab_project_path='team/backend')
        self.repository = ProjectRepository.objects.create(project=self.project, path='team/backend')
        ProjectPolicy.objects.create(project=self.project)
        GitLabConfig.objects.create(base_url='https://gitlab.example.com', api_token='test-token')
        self.admin = get_user_model().objects.create_user('sync-admin', is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = f'/api/projects/{self.project.pk}/sync/'

    @patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr()], False))
    def test_enable_records_baseline_without_reviewing_existing_mr(self, read):
        response = self.client.patch(self.url, {'enabled': True, 'interval_seconds': 60}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['enabled'])
        self.assertEqual(response.json()['baseline_mrs'], 1)
        self.assertIsNotNone(response.json()['activated_at'])
        self.assertEqual(ReviewJob.objects.count(), 0)

    def test_default_is_disabled_and_has_no_network_side_effect(self):
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests') as read:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['enabled'])
        self.assertEqual(response.json()['interval_seconds'], 60)
        read.assert_not_called()

    @patch('review.integrations.gitlab.GitLabClient.list_merge_requests', side_effect=RuntimeError('secret-token upstream URL'))
    def test_activation_fails_closed_and_sanitizes_upstream_error(self, read):
        response = self.client.patch(self.url, {'enabled': True}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret-token', str(response.json()))
        self.assertFalse(self.client.get(self.url).json()['enabled'])

    @patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr(sha=None)], False))
    def test_missing_sha_cannot_be_used_as_activation_baseline(self, read):
        response = self.client.patch(self.url, {'enabled': True}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.client.get(self.url).json()['enabled'])

    @patch('review.integrations.gitlab.GitLabClient.list_merge_requests', side_effect=[([mr()], True), ([mr()], False)])
    def test_duplicate_pagination_cannot_activate(self, read):
        response = self.client.patch(self.url, {'enabled': True}, format='json')
        self.assertEqual(response.status_code, 503)

    def test_strict_interval_and_read_only_status_fields(self):
        for value in (29, 3601, 60.5, True, 'abc'):
            with self.subTest(value=value):
                response = self.client.patch(self.url, {'interval_seconds': value}, format='json')
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.patch(self.url, {'last_error': 'overwrite'}, format='json').status_code, 400)

    def test_member_can_read_but_cannot_activate_or_request_sync(self):
        member = get_user_model().objects.create_user('sync-member')
        ProjectMembership.objects.create(project=self.project, user=member, role=ProjectMembership.Role.MEMBER)
        self.client.force_authenticate(member)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.patch(self.url, {'enabled': True}, format='json').status_code, 403)
        self.assertEqual(self.client.post(self.url + 'run/', {}, format='json').status_code, 403)

    def test_outsider_cannot_read_project_sync(self):
        outsider = get_user_model().objects.create_user('sync-outsider')
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr()], False))
    def test_manual_request_only_schedules_background_sync(self, read):
        self.client.patch(self.url, {'enabled': True}, format='json')
        read.reset_mock()
        response = self.client.post(self.url + 'run/', {}, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertIsNotNone(response.json()['next_sync_at'])
        self.assertEqual(ReviewJob.objects.count(), 0)
        read.assert_not_called()

    def test_disabled_sync_cannot_process_history_via_manual_request(self):
        response = self.client.post(self.url + 'run/', {}, format='json')
        self.assertEqual(response.status_code, 409)

    @patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr()], False))
    def test_project_summary_reports_polling_when_webhook_is_disabled(self, read):
        ProjectPolicy.objects.filter(project=self.project).update(webhook_trigger=False)
        self.client.patch(self.url, {'enabled': True}, format='json')
        response = self.client.get(f'/api/projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('sync_enabled'))

    @patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr()], False))
    def test_activation_without_gitlab_connection_cannot_use_test_fallback(self, read):
        GitLabConfig.objects.all().update(enabled=False)
        self.assertEqual(self.client.patch(self.url, {'enabled': True}, format='json').status_code, 503)
        read.assert_not_called()


@override_settings(REVIEW_EXECUTE_INLINE=False)
class GitLabPollingTests(TestCase):
    def setUp(self):
        GitLabSyncAPITests.setUp(self)
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr()], False)):
            response = self.client.patch(self.url, {'enabled': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.config = ProjectGitLabSync.objects.get(project=self.project)

    def sync(self, items):
        self.assertTrue(hasattr(gitlab_sync, 'synchronize_project'), 'periodic synchronization is missing')
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=(items, False)):
            result = gitlab_sync.synchronize_project(self.config, force=True)
        self.config.refresh_from_db()
        return result

    def finish(self, job, sha, status=ReviewJob.Status.SUCCEEDED):
        job.status = status
        job.save()
        job.review.head_sha = sha
        job.review.status = Review.Status.COMPLETED if status == ReviewJob.Status.SUCCEEDED else Review.Status.FAILED
        job.review.save()

    def test_activation_baseline_is_skipped_on_repeated_scans(self):
        self.sync([mr()])
        self.sync([mr()])
        self.assertEqual(ReviewJob.objects.count(), 0)
        self.assertEqual(self.config.sync_state, 'success')
        self.assertEqual(self.config.last_result['enqueued'], 0)

    def test_new_mr_is_enqueued_once_with_sync_source(self):
        self.sync([mr(), mr(2)])
        self.sync([mr(), mr(2)])
        job = ReviewJob.objects.get()
        self.assertEqual(job.payload['mr_iid'], 2)
        self.assertEqual(job.review.source, '定时同步')
        self.assertEqual(job.payload['automatic_head_sha'], 'a' * 40)

    def test_new_sha_is_enqueued_once_even_after_completion(self):
        self.sync([mr(sha='b' * 40)])
        self.finish(ReviewJob.objects.get(), 'b' * 40)
        self.sync([mr(sha='b' * 40)])
        self.assertEqual(ReviewJob.objects.count(), 1)

    def test_completed_manual_full_review_covers_same_sha(self):
        job = create_review_job(self.project, {'mr_iid': 2, 'repository_path': self.repository.path})
        self.finish(job, 'b' * 40)
        self.sync([mr(2, 'b' * 40)])
        self.assertEqual(ReviewJob.objects.count(), 1)

    def test_busy_mr_is_caught_up_after_old_job_finishes(self):
        job = create_review_job(self.project, {'mr_iid': 2, 'repository_path': self.repository.path})
        job.status = ReviewJob.Status.RUNNING
        job.save()
        self.sync([mr(2, 'b' * 40)])
        self.assertEqual(ReviewJob.objects.count(), 1)
        self.finish(job, 'a' * 40)
        self.sync([mr(2, 'b' * 40)])
        self.assertEqual(ReviewJob.objects.count(), 2)

    def test_exhausted_same_sha_is_not_repeated_forever(self):
        self.sync([mr(2, 'b' * 40)])
        self.finish(ReviewJob.objects.get(), 'b' * 40, ReviewJob.Status.FAILED)
        self.sync([mr(2, 'b' * 40)])
        self.assertEqual(ReviewJob.objects.count(), 1)

    def test_draft_and_closed_mrs_are_not_automatically_reviewed(self):
        self.sync([mr(2, draft=True), {**mr(3), 'state': 'closed'}])
        self.assertEqual(ReviewJob.objects.count(), 0)

    def test_paused_project_does_not_read_or_enqueue(self):
        self.project.enabled = False
        self.project.save()
        self.sync([mr(2)])
        self.assertEqual(ReviewJob.objects.count(), 0)
        self.assertIsNone(self.config.last_sync_at)

    def test_new_repository_starts_with_its_own_baseline(self):
        second = ProjectRepository.objects.create(project=self.project, path='team/frontend')
        self.sync([mr(1, 'b' * 40)])
        self.assertEqual(GitLabSyncBaseline.objects.get(project=self.project, repository_path=second.path).initial_shas, {'1': 'b' * 40})
        self.assertEqual(ReviewJob.objects.count(), 1)
        self.sync([mr(1, 'c' * 40)])
        self.assertEqual(ReviewJob.objects.count(), 2)  # backend is still queued; frontend gets its new SHA
        self.assertEqual(ReviewJob.objects.get(payload__repository_path=second.path).payload['mr_iid'], 1)

    def test_failure_is_safe_and_persists_retry_backoff(self):
        self.assertTrue(hasattr(gitlab_sync, 'synchronize_project'))
        before = timezone.now()
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests', side_effect=RuntimeError('secret-token URL')):
            gitlab_sync.synchronize_project(self.config, force=True)
        self.config.refresh_from_db()
        self.assertEqual(self.config.sync_state, 'error')
        self.assertEqual(self.config.consecutive_failures, 1)
        self.assertNotIn('secret-token', str(self.config.last_result) + self.config.last_error)
        self.assertGreaterEqual(self.config.next_sync_at, before + timedelta(seconds=60))
        self.sync([mr()])
        self.assertEqual(self.config.consecutive_failures, 0)
        self.assertEqual(self.config.last_error, '')

    def test_all_pages_are_scanned_before_enqueue(self):
        self.assertTrue(hasattr(gitlab_sync, 'synchronize_project'))
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests', side_effect=[([mr()], True), ([mr(2)], False)]):
            gitlab_sync.synchronize_project(self.config, force=True)
        self.assertEqual(ReviewJob.objects.get().payload['mr_iid'], 2)

    def test_reenable_resets_baseline_instead_of_backfilling(self):
        self.client.patch(self.url, {'enabled': False}, format='json')
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr(2, 'b' * 40)], False)):
            response = self.client.patch(self.url, {'enabled': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.sync([mr(2, 'b' * 40)])
        self.assertEqual(ReviewJob.objects.count(), 0)

    def test_repository_failure_does_not_block_another_repository(self):
        second = ProjectRepository.objects.create(project=self.project, path='team/frontend')
        GitLabSyncBaseline.objects.create(project=self.project, repository_path=second.path, initial_shas={})
        self.assertTrue(hasattr(gitlab_sync, 'synchronize_project'))
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests', side_effect=[RuntimeError('private'), ([mr(2)], False)]):
            gitlab_sync.synchronize_project(self.config, force=True)
        self.config.refresh_from_db()
        self.assertEqual(self.config.sync_state, 'error')
        self.assertEqual(ReviewJob.objects.get().payload['repository_path'], second.path)
        self.assertEqual(self.config.last_result['enqueued'], 1)

    def test_concurrent_scan_is_skipped_before_any_gitlab_read(self):
        from review.services.execution_locks import resource_lock
        self.assertTrue(hasattr(gitlab_sync, 'synchronize_project'))
        with resource_lock(f'gitlab-sync:{self.project.pk}'), \
             patch('review.integrations.gitlab.GitLabClient.list_merge_requests') as read:
            result = gitlab_sync.synchronize_project(self.config, force=True)
        self.assertTrue(result['skipped'])
        read.assert_not_called()
