from django.test import TestCase, override_settings
from review.tests.auth_helpers import admin_client

from review.models import Project, Review, ReviewJob


@override_settings(REVIEW_EXECUTE_INLINE=False)
class ManualRetryTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='retry', gitlab_project_path='team/retry')
        self.review = Review.objects.create(project=self.project, mr_iid=7, status=Review.Status.FAILED)
        self.job = ReviewJob.objects.create(
            project=self.project, review=self.review, payload={'mr_iid': 7},
            idempotency_key='manual-retry', status=ReviewJob.Status.FAILED,
            attempts=3, analysis_result={'summary': 'saved', 'findings': []},
            notification_state='done',
        )
        self.url = f'/api/reviews/{self.review.pk}/retry/'

    def test_failed_retry_resumes_original_job_and_snapshot(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['job_id'], str(self.job.pk))
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ReviewJob.Status.QUEUED)
        self.assertEqual(self.job.attempts, 0)
        self.assertEqual(self.job.review_id, self.review.pk)
        self.assertEqual(self.job.analysis_result['summary'], 'saved')
        self.assertEqual(self.job.notification_state, 'done')
        self.assertEqual(ReviewJob.objects.count(), 1)

    def test_manual_notification_cannot_race_running_job(self):
        from unittest.mock import patch
        self.review.status = Review.Status.COMPLETED
        self.review.summary = 'done'
        self.review.save()
        self.job.status = ReviewJob.Status.RUNNING
        self.job.save()
        with patch('review.views.WeComClient.send_review', return_value={'ok': True}) as send:
            response = self.client.post(f'/api/reviews/{self.review.pk}/notify/')
        self.assertEqual(response.status_code, 409)
        send.assert_not_called()

    def test_concurrent_manual_notification_is_rejected_without_changing_delivery_state(self):
        from unittest.mock import patch
        from review.services.execution_locks import resource_lock
        self.review.status = Review.Status.COMPLETED
        self.review.summary = 'done'
        self.review.save()
        with resource_lock(f'notification:{self.review.pk}'):
            with patch('review.views.WeComClient.send_review', return_value={'ok': True}) as send:
                response = self.client.post(f'/api/reviews/{self.review.pk}/notify/')
        self.assertEqual(response.status_code, 409)
        send.assert_not_called()
        self.job.refresh_from_db()
        self.assertEqual(self.job.notification_state, 'done')

    def test_repeated_retry_does_not_create_second_job(self):
        self.client.post(self.url)
        self.assertEqual(self.client.post(self.url).status_code, 409)
        self.assertEqual(ReviewJob.objects.count(), 1)

    def test_paused_project_cannot_retry(self):
        self.project.enabled = False
        self.project.save()
        self.assertEqual(self.client.post(self.url).status_code, 409)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ReviewJob.Status.FAILED)
        self.assertEqual(ReviewJob.objects.count(), 1)
