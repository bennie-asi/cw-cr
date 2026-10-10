from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from review.models import GitLabConfig, Project, ProjectPolicy, ReviewJob
from review.services.review_runner import run_review_job
from review.tests.test_gitlab_sync import GitLabPollingTests, mr


@override_settings(REVIEW_EXECUTE_INLINE=False, REVIEW_ALLOW_TEST_FALLBACK=False)
class AutomaticWebhookTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Webhooks', gitlab_project_path='team/hooks', webhook_secret='test-secret')
        ProjectPolicy.objects.create(project=self.project)
        GitLabConfig.objects.create(base_url='https://gitlab.example.com', api_token='test-token')
        self.client = APIClient()

    def hook(self, event_uuid, snapshot):
        payload = {'object_kind': 'merge_request', 'project': {'path_with_namespace': 'team/hooks'},
                   'object_attributes': {'iid': 2, 'action': 'update'}}
        with patch('review.integrations.gitlab.GitLabClient.fetch_merge_request', return_value=snapshot):
            return self.client.post('/api/webhooks/gitlab/', payload, format='json',
                HTTP_X_GITLAB_TOKEN='test-secret', HTTP_X_GITLAB_EVENT_UUID=event_uuid)

    def test_distinct_event_uuids_for_one_sha_create_one_job(self):
        first = self.hook('event-one', mr(2))
        second = self.hook('event-two', mr(2))
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(ReviewJob.objects.count(), 1)
        self.assertEqual(ReviewJob.objects.get().payload.get('automatic_head_sha'), 'a' * 40)

    def test_draft_webhook_is_ignored(self):
        response = self.hook('event-draft', mr(2, draft=True))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['accepted'])
        self.assertEqual(ReviewJob.objects.count(), 0)

    def test_current_metadata_failure_is_retryable_without_leaking_secrets(self):
        with patch('review.integrations.gitlab.GitLabClient.fetch_merge_request', side_effect=RuntimeError('secret-token')):
            response = self.client.post('/api/webhooks/gitlab/', {
                'object_kind': 'merge_request', 'project': {'path_with_namespace': 'team/hooks'},
                'object_attributes': {'iid': 2, 'action': 'update'},
            }, format='json', HTTP_X_GITLAB_TOKEN='test-secret')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(ReviewJob.objects.count(), 0)
        self.assertNotIn('secret-token', str(response.json()))

    def test_changed_sha_stops_old_automatic_job_before_ai_call(self):
        self.hook('old-head', mr(2))
        job = ReviewJob.objects.get()
        newer = {**mr(2, 'b' * 40), 'diff_refs': {'head_sha': 'b' * 40, 'base_sha': 'c' * 40, 'start_sha': 'c' * 40}}
        with patch('review.integrations.gitlab.GitLabClient.fetch_merge_request', return_value=newer), \
             patch('review.integrations.llm.LLMClient.analyze') as analyze:
            run_review_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.FAILED)
        analyze.assert_not_called()

    def test_closed_mr_stops_automatic_job_even_when_sha_is_unchanged(self):
        self.hook('open-head', mr(2))
        job = ReviewJob.objects.get()
        closed = {**mr(2), 'state': 'closed', 'diff_refs': {'head_sha': 'a' * 40, 'base_sha': 'c' * 40, 'start_sha': 'c' * 40}}
        with patch('review.integrations.gitlab.GitLabClient.fetch_merge_request', return_value=closed), \
             patch('review.integrations.llm.LLMClient.analyze') as analyze:
            run_review_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.FAILED)
        analyze.assert_not_called()

    def test_lagging_diff_refs_wait_for_current_sha_before_ai(self):
        preparing = {**mr(2), 'diff_refs': {'head_sha': 'b' * 40, 'base_sha': 'c' * 40, 'start_sha': 'c' * 40}}
        self.hook('preparing-head', preparing)
        job = ReviewJob.objects.get()
        self.assertEqual(job.payload['automatic_head_sha'], 'a' * 40)
        with patch('review.integrations.gitlab.GitLabClient.fetch_merge_request', return_value=preparing), \
             patch('review.integrations.llm.LLMClient.analyze') as analyze:
            run_review_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.QUEUED)
        self.assertIsNotNone(job.next_attempt_at)
        analyze.assert_not_called()


@override_settings(REVIEW_EXECUTE_INLINE=False, REVIEW_ALLOW_TEST_FALLBACK=False)
class DualTriggerTests(TestCase):
    def setUp(self):
        GitLabPollingTests.setUp(self)
        self.project.webhook_secret = 'test-secret'
        self.project.save()

    def test_poll_then_webhook_after_completion_is_one_job(self):
        from review.services import gitlab_sync
        self.assertTrue(hasattr(gitlab_sync, 'synchronize_project'))
        with patch('review.integrations.gitlab.GitLabClient.list_merge_requests', return_value=([mr(2)], False)):
            gitlab_sync.synchronize_project(self.config, force=True)
        job = ReviewJob.objects.get()
        GitLabPollingTests.finish(self, job, 'a' * 40)
        self.client.force_authenticate(None)
        with patch('review.integrations.gitlab.GitLabClient.fetch_merge_request', return_value=mr(2)):
            response = self.client.post('/api/webhooks/gitlab/', {
                'object_kind': 'merge_request', 'project': {'path_with_namespace': self.repository.path},
                'object_attributes': {'iid': 2, 'action': 'update'},
            }, format='json', HTTP_X_GITLAB_TOKEN='test-secret', HTTP_X_GITLAB_EVENT_UUID='different-event')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReviewJob.objects.count(), 1)
