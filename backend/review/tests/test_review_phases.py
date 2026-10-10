from datetime import timedelta

from django.db.models import Prefetch
from django.test import TestCase

from review.models import LineComment, Project, Review, ReviewJob
from review.serializers import ReviewListSerializer, ReviewSerializer
from review.tests.auth_helpers import admin_client


class ReviewPhaseTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='API', gitlab_project_path='team/api')

    def test_generation_phase_is_independent_from_publication_and_gate(self):
        for status, expected in [('queued', 'queued'), ('running', 'running'), ('failed', 'failed'),
                                 ('completed', 'completed'), ('blocked', 'completed')]:
            with self.subTest(status=status):
                review = Review.objects.create(project=self.project, mr_iid=1, status=status, summary_sync_status='failed')
                data = ReviewSerializer(review).data
                self.assertEqual(data.get('generation_state'), expected)
                self.assertEqual(data['publication_state'], 'failed')

    def test_publication_phase_requires_summary_and_every_comment_to_be_published(self):
        review = Review.objects.create(project=self.project, mr_iid=1, status='completed', summary_sync_status='synced')
        self.assertEqual(ReviewSerializer(review).data.get('publication_state'), 'completed')
        comment = LineComment.objects.create(review=review, file_path='a.py', title='Issue', body='Details')
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'pending')
        comment.status = 'failed'
        comment.save()
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'failed')
        comment.status = 'resolved'
        comment.save()
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'completed')
        review.summary_sync_status = 'pending'
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'pending')
        review.summary_sync_status = 'unexpected'
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'unknown')

    def test_notification_state_comes_from_actual_latest_job_and_no_job_is_not_requested(self):
        review = Review.objects.create(project=self.project, mr_iid=1, status='completed', summary_sync_status='synced')
        self.assertEqual(ReviewSerializer(review).data['notification_state'], 'not_requested')
        old = ReviewJob.objects.create(project=self.project, review=review, idempotency_key='old', notification_state='done')
        # Windows clock resolution can assign equal created_at values to consecutive inserts.
        ReviewJob.objects.filter(pk=old.pk).update(created_at=old.created_at - timedelta(seconds=1))
        ReviewJob.objects.create(project=self.project, review=review, idempotency_key='new', notification_state='unknown')
        self.assertEqual(ReviewSerializer(review).data['notification_state'], 'unknown')

    def test_generated_snapshot_survives_a_publication_failure_and_requeue(self):
        review = Review.objects.create(project=self.project, mr_iid=8, status='failed')
        job = ReviewJob.objects.create(project=self.project, review=review, idempotency_key='snapshot',
                                       status='failed', analysis_result={'summary': '评审已生成', 'findings': []})
        comment = LineComment.objects.create(review=review, file_path='a.py', title='Issue', body='Details', status='failed')
        self.assertEqual(ReviewSerializer(review).data['generation_state'], 'completed')
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'failed')
        job.status = 'queued'
        job.save()
        self.assertEqual(ReviewSerializer(review).data['generation_state'], 'completed')
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'pending')
        job.status = 'running'
        job.save()
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'running')
        comment.status = 'published'
        comment.save()
        review.summary_sync_status = 'synced'
        self.assertEqual(ReviewSerializer(review).data['publication_state'], 'completed')

    def test_generation_retry_without_snapshot_reports_current_job_phase(self):
        review = Review.objects.create(project=self.project, mr_iid=9, status='failed')
        job = ReviewJob.objects.create(project=self.project, review=review, idempotency_key='generate-retry', status='failed')
        self.assertEqual(ReviewSerializer(review).data['generation_state'], 'failed')
        for phase in ('queued', 'running'):
            job.status = phase
            job.save()
            self.assertEqual(ReviewSerializer(review).data['generation_state'], phase)

    def test_detail_execution_state_tracks_unlinked_tasks_for_the_same_repository_and_mr(self):
        review = Review.objects.create(project=self.project, repository_path='team/api', mr_iid=12,
                                       status='completed', summary_sync_status='synced')
        ReviewJob.objects.create(project=self.project, review=review, idempotency_key='previous-result',
                                 status='succeeded', notification_state='done')
        active = ReviewJob.objects.create(project=self.project, idempotency_key='new-improve', kind='improve',
                                         payload={'repository_path': 'team/api', 'mr_iid': 12})
        client = admin_client()
        urls = ('/api/reviews/', f'/api/projects/{self.project.pk}/reviews/', f'/api/reviews/{review.pk}/')
        for phase in ('queued', 'running'):
            active.status = phase
            active.save()
            for url in urls:
                with self.subTest(phase=phase, url=url):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200)
                    data = response.data['results'][0] if 'results' in response.data else response.data
                    self.assertEqual(data['execution_state'], phase)
                    self.assertEqual(data['generation_state'], 'completed')
                    self.assertEqual(data['notification_state'], 'done')
        active.payload['repository_path'] = 'team/other'
        active.save()
        self.assertEqual(ReviewSerializer(review).data['execution_state'], 'succeeded')
        active.payload['repository_path'] = 'team/api'
        active.status = 'failed'
        active.save()
        self.assertEqual(ReviewSerializer(review).data['execution_state'], 'succeeded')

    def test_list_serializer_uses_prefetched_jobs_and_comments_without_per_review_queries(self):
        for iid in range(1, 7):
            review = Review.objects.create(project=self.project, mr_iid=iid, status='blocked', summary_sync_status='synced')
            LineComment.objects.create(review=review, file_path='a.py', title='Issue', body='Details', level='P1', status='published')
            ReviewJob.objects.create(project=self.project, review=review, idempotency_key=f'job-{iid}',
                                     status='succeeded', notification_state='failed', merge_status='unknown', merge_attempts=2)
        ReviewJob.objects.create(project=self.project, idempotency_key='new-unlinked',
                                 payload={'repository_path': 'team/api', 'mr_iid': 1})
        rows = list(Review.objects.select_related('project', 'project__policy').prefetch_related('jobs', 'comments',
            Prefetch('project__jobs', queryset=ReviewJob.objects.filter(status__in=('queued', 'running')).select_related('review'),
                     to_attr='active_review_jobs')))
        with self.assertNumQueries(0):
            data = ReviewListSerializer(rows, many=True).data
        self.assertEqual(data[0]['generation_state'], 'completed')
        self.assertEqual(data[0]['publication_state'], 'completed')
        self.assertEqual(data[0]['notification_state'], 'failed')
        self.assertEqual(data[0]['blocking_comment_count'], 1)
        self.assertEqual(data[0]['merge_status'], 'unknown')
        self.assertEqual(data[0]['merge_attempts'], 2)
        self.assertEqual(next(row for row in data if row['mr_iid'] == 1)['execution_state'], 'queued')

    def test_both_list_and_detail_endpoints_expose_phases_without_claiming_notification_success(self):
        review = Review.objects.create(project=self.project, mr_iid=7, status='blocked', summary_sync_status='failed')
        client = admin_client()
        for url in ('/api/reviews/', f'/api/projects/{self.project.pk}/reviews/', f'/api/reviews/{review.pk}/'):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 200)
                data = response.data['results'][0] if 'results' in response.data else response.data
                self.assertEqual(data.get('generation_state'), 'completed')
                self.assertEqual(data['publication_state'], 'failed')
                self.assertEqual(data['notification_state'], 'not_requested')
