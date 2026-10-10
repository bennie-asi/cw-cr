from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from review.models import LineComment, Project, ProjectMembership, ProjectRepository, Review, ReviewJob
from review.integrations.gitlab import GitLabClient
from review.services.review_runner import create_review_job
from review.tests.auth_helpers import admin_client


def mr(iid, sha='a' * 40, **values):
    return {'iid': iid, 'title': f'MR {iid}', 'state': 'opened', 'sha': sha,
            'draft': False, 'updated_at': '2026-09-30T01:00:00Z', **values}


def page(items, next_page=''):
    response = Mock(status_code=200, headers={'X-Next-Page': next_page})
    response.json.return_value = items
    return response


@override_settings(GITLAB_URL='https://gitlab.example', GITLAB_TOKEN='test-token', REVIEW_EXECUTE_INLINE=False)
class MergeRequestCoverageTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='API', gitlab_project_path='team/api')
        self.url = '/api/overview/coverage/'

    def review(self, iid, **values):
        return Review.objects.create(project=self.project, mr_iid=iid,
                                     **{'status': 'completed', 'head_sha': 'a' * 40, **values})

    @patch('review.integrations.gitlab.requests.get')
    def test_scans_every_page_including_drafts_and_counts_only_current_full_reviews(self, get):
        self.review(1)
        self.review(2, status='blocked')
        self.review(3, source='/improve')
        self.review(4, head_sha='b' * 40)
        self.review(5, status='failed')
        get.side_effect = [page([mr(1), mr(2, draft=True)], '2'), page([mr(3), mr(4), mr(5)])]
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data['state'], data['open_mrs'], data['reviewed_mrs'], data['unreviewed_mrs']),
                         ('complete', 5, 2, 3))
        self.assertEqual(data['percentage'], 40)
        self.assertEqual((data['repositories_checked'], data['repositories_total']), (1, 1))
        self.assertTrue(data['checked_at'])
        self.assertEqual(data['errors'], [])
        self.assertEqual([call.kwargs['params']['page'] for call in get.call_args_list], [1, 2])
        self.assertTrue(all(call.kwargs['params']['state'] == 'opened' for call in get.call_args_list))
        self.assertTrue(all('draft' not in call.kwargs['params'] for call in get.call_args_list))

    @patch('review.integrations.gitlab.requests.get')
    def test_visible_paused_projects_are_checked_but_hidden_and_disabled_repositories_are_not(self, get):
        self.project.enabled = False
        self.project.save()
        ProjectRepository.objects.create(project=self.project, path='team/api', enabled=False)
        ProjectRepository.objects.create(project=self.project, path='team/ui')
        Project.objects.create(name='Hidden', gitlab_project_path='secret/hidden')
        user = get_user_model().objects.create_user('coverage-reader')
        ProjectMembership.objects.create(project=self.project, user=user)
        client = APIClient()
        client.force_authenticate(user)
        get.return_value = page([mr(7)])
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['repositories_total'], 1)
        self.assertEqual(response.data['open_mrs'], 1)
        self.assertIn('team%2Fui', get.call_args.args[0])
        self.assertNotIn('secret/hidden', str(response.data))
        self.assertEqual(get.call_count, 1)

    @patch('review.integrations.gitlab.requests.get')
    def test_legacy_blank_repository_review_belongs_only_to_primary_repository(self, get):
        ProjectRepository.objects.create(project=self.project, path='team/ui')
        self.review(7)
        get.return_value = page([mr(7)])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['open_mrs'], response.data['reviewed_mrs']), (2, 1))
        self.assertEqual(response.data['percentage'], 50)

    @patch('review.integrations.gitlab.requests.get')
    def test_failure_keeps_known_counts_without_publishing_an_overall_percentage_or_upstream_details(self, get):
        ProjectRepository.objects.create(project=self.project, path='team/ui')
        self.review(1)
        get.side_effect = [page([mr(1)]), requests.ConnectionError('token=test-token upstream-private-body')]
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['state'], 'partial')
        self.assertEqual((response.data['open_mrs'], response.data['reviewed_mrs']), (1, 1))
        self.assertIsNone(response.data['percentage'])
        self.assertEqual(response.data['repositories_checked'], 1)
        self.assertEqual(response.data['errors'][0]['path'], 'team/ui')
        self.assertNotIn('test-token', str(response.data))
        self.assertNotIn('upstream-private-body', str(response.data))

    @override_settings(MR_COVERAGE_MAX_REQUESTS=1)
    @patch('review.integrations.gitlab.requests.get')
    def test_request_budget_truncation_is_partial_and_stops_pagination(self, get):
        get.return_value = page([mr(1)], '2')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['state'], response.data['open_mrs']), ('partial', 1))
        self.assertIsNone(response.data['percentage'])
        self.assertEqual(response.data['repositories_checked'], 0)
        self.assertTrue(response.data['errors'])
        self.assertEqual(get.call_count, 1)

    @override_settings(MR_COVERAGE_MAX_REQUESTS=1)
    @patch('review.integrations.gitlab.requests.get')
    def test_exhausted_budget_stops_repository_setup_for_remaining_paths(self, get):
        ProjectRepository.objects.create(project=self.project, path='team/ui')
        get.return_value = page([mr(1)], '2')
        with patch('review.services.mr_coverage.GitLabClient', wraps=GitLabClient) as client_type:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client_type.call_count, 1)
        self.assertEqual(response.data['repositories_total'], 2)
        self.assertEqual({item['path'] for item in response.data['errors']}, {'team/api', 'team/ui'})

    @override_settings(MR_COVERAGE_MAX_SECONDS=1, MR_COVERAGE_REQUEST_TIMEOUT=3)
    @patch('review.integrations.gitlab.requests.get')
    def test_elapsed_budget_stops_followup_requests_and_limits_per_call_timeout(self, get):
        clock = [0.0]
        def fetch(*args, **kwargs):
            clock[0] = 2.0
            return page([mr(1)], '2')
        get.side_effect = fetch
        with patch('time.monotonic', side_effect=lambda: clock[0]):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['state'], 'unavailable')
        self.assertEqual(response.data['open_mrs'], 0)
        self.assertIsNone(response.data['percentage'])
        self.assertEqual(get.call_count, 1)
        self.assertLessEqual(get.call_args.kwargs['timeout'], 1)

    @patch('review.integrations.gitlab.requests.get')
    def test_missing_mr_sha_cannot_be_treated_as_covered_or_as_complete_percentage(self, get):
        self.review(1, head_sha='')
        get.return_value = page([mr(1, sha=None)])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['state'], 'partial')
        self.assertEqual(response.data['reviewed_mrs'], 0)
        self.assertIsNone(response.data['percentage'])
        self.assertTrue(response.data['errors'])

    @override_settings(GITLAB_URL='', GITLAB_TOKEN='')
    @patch('review.integrations.gitlab.requests.get')
    def test_unconfigured_gitlab_is_unavailable_and_makes_no_request(self, get):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['state'], 'unavailable')
        self.assertIsNone(response.data['percentage'])
        get.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    def test_empty_mr_population_has_no_percentage(self, get):
        get.return_value = page([])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['state'], 'complete')
        self.assertEqual(response.data['open_mrs'], 0)
        self.assertIsNone(response.data['percentage'])

    @patch('review.integrations.gitlab.requests.get')
    def test_risk_overview_does_not_scan_gitlab(self, get):
        self.assertEqual(self.client.get('/api/overview/').status_code, 200)
        get.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    def test_anonymous_cannot_trigger_scanning(self, get):
        self.assertEqual(APIClient().get(self.url).status_code, 401)
        get.assert_not_called()


@override_settings(GITLAB_URL='https://gitlab.example', GITLAB_TOKEN='test-token', REVIEW_EXECUTE_INLINE=False)
class MergeRequestReviewStateTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='API', gitlab_project_path='team/api')
        ProjectRepository.objects.create(project=self.project, path='team/ui')
        self.url = f'/api/projects/{self.project.pk}/merge-requests/'

    @patch('review.integrations.gitlab.requests.get')
    def test_full_review_matching_is_scoped_to_project_repository_and_current_sha(self, get):
        review = Review.objects.create(project=self.project, repository_path='team/ui', mr_iid=7,
                                       head_sha='a' * 40, status='blocked')
        LineComment.objects.create(review=review, file_path='a.py', title='Issue', body='Details')
        get.return_value = page([mr(7, draft=True)])
        row = self.client.get(self.url, {'repository': 'team/ui'}).data['results'][0]
        self.assertEqual(row.get('review_state'), 'reviewed')
        self.assertEqual(row['review_id'], str(review.pk))
        self.assertEqual((row['review_sha'], row['issue_count']), ('a' * 40, 1))
        self.assertTrue(row['draft'])
        self.assertEqual(row['sha'], 'a' * 40)
        self.assertEqual(row['updated_at'], '2026-09-30T01:00:00Z')
        primary = self.client.get(self.url, {'repository': 'team/api'}).data['results'][0]
        self.assertEqual(primary['review_state'], 'unreviewed')
        self.assertIsNone(primary['review_id'])
        self.assertIsNone(primary['issue_count'])

    @patch('review.integrations.gitlab.requests.get')
    def test_legacy_primary_and_improve_reviews_never_contaminate_secondary_full_review_state(self, get):
        Review.objects.create(project=self.project, mr_iid=7, head_sha='a' * 40, status='completed')
        Review.objects.create(project=self.project, repository_path='team/ui', mr_iid=7, head_sha='a' * 40,
                              source='/improve', status='completed')
        other = Project.objects.create(name='Other', gitlab_project_path='team/other')
        Review.objects.create(project=other, repository_path='team/ui', mr_iid=7, head_sha='a' * 40, status='completed')
        get.return_value = page([mr(7)])
        row = self.client.get(self.url, {'repository': 'team/ui'}).data['results'][0]
        self.assertEqual(row.get('review_state'), 'unreviewed')
        self.assertIsNone(row['issue_count'])

    @patch('review.integrations.gitlab.requests.get')
    def test_stale_failed_missing_sha_and_unreviewed_states_do_not_report_zero_issues(self, get):
        stale = Review.objects.create(project=self.project, repository_path='team/ui', mr_iid=1,
                                      head_sha='b' * 40, status='completed')
        Review.objects.create(project=self.project, repository_path='team/ui', mr_iid=2,
                              head_sha='a' * 40, status='failed')
        get.return_value = page([mr(1), mr(2), mr(3, sha=None), mr(4)])
        rows = self.client.get(self.url, {'repository': 'team/ui'}).data['results']
        self.assertEqual([row.get('review_state') for row in rows], ['stale', 'failed', 'unknown', 'unreviewed'])
        self.assertTrue(all(row['issue_count'] is None for row in rows))
        self.assertEqual(rows[0]['review_id'], str(stale.pk))
        self.assertEqual(rows[0]['review_sha'], 'b' * 40)

    @patch('review.integrations.gitlab.requests.get')
    def test_active_full_jobs_are_visible_even_before_review_creation_or_after_sha_changes(self, get):
        # Older queue records can still be unlinked; keep covering that format
        # even though newly enqueued jobs now include a visible queued review.
        ReviewJob.objects.create(project=self.project, idempotency_key='legacy-unlinked',
                                 payload={'mr_iid': 1, 'repository_path': 'team/ui'})
        review = Review.objects.create(project=self.project, repository_path='team/ui', mr_iid=2,
                                       head_sha='b' * 40, status='failed')
        ReviewJob.objects.create(project=self.project, review=review, status='running',
                                 idempotency_key='running', payload={'mr_iid': 2, 'repository_path': 'team/ui'})
        get.return_value = page([mr(1), mr(2)])
        rows = self.client.get(self.url, {'repository': 'team/ui'}).data['results']
        self.assertEqual([row.get('review_state') for row in rows], ['queued', 'running'])
        self.assertIsNone(rows[0]['review_id'])
        self.assertIsNone(rows[1]['issue_count'])

    def test_any_active_kind_prevents_duplicate_for_same_mr_even_with_new_sha(self):
        first = create_review_job(self.project, {'mr_iid': 7, 'repository_path': 'team/ui', 'head_sha': 'a' * 40})
        second = create_review_job(self.project, {'mr_iid': 7, 'repository_path': 'team/ui', 'head_sha': 'b' * 40}, 'improve')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ReviewJob.objects.count(), 1)
        primary = create_review_job(self.project, {'mr_iid': 7, 'repository_path': 'team/api'})
        self.assertNotEqual(first.pk, primary.pk)

    @patch('review.integrations.gitlab.requests.get')
    def test_active_improve_jobs_prevent_actionable_stale_unreviewed_or_unknown_mr_rows(self, get):
        Review.objects.create(project=self.project, repository_path='team/ui', mr_iid=2,
                              head_sha='b' * 40, status='completed')
        for iid, status in ((1, 'queued'), (2, 'running'), (3, 'queued')):
            ReviewJob.objects.create(project=self.project, kind='improve', status=status,
                                     idempotency_key=f'improve-{iid}',
                                     payload={'mr_iid': iid, 'repository_path': 'team/ui', 'source': '/improve'})
        get.return_value = page([mr(1), mr(2), mr(3, sha=None)])
        rows = self.client.get(self.url, {'repository': 'team/ui'}).data['results']
        self.assertEqual([row['review_state'] for row in rows], ['queued', 'running', 'queued'])
        self.assertTrue(all(row['issue_count'] is None for row in rows))

    def test_failed_review_retry_does_not_reactivate_job_while_another_kind_is_active_for_same_mr(self):
        review = Review.objects.create(project=self.project, repository_path='team/ui', mr_iid=7,
                                       head_sha='a' * 40, status='failed')
        failed = ReviewJob.objects.create(project=self.project, review=review, status='failed',
                                         idempotency_key='failed', payload={'mr_iid': 7, 'repository_path': 'team/ui'})
        create_review_job(self.project, {'mr_iid': 7, 'repository_path': 'team/ui', 'head_sha': 'b' * 40}, 'improve')
        response = self.client.post(f'/api/reviews/{review.pk}/retry/')
        self.assertEqual(response.status_code, 409)
        failed.refresh_from_db()
        self.assertEqual(failed.status, 'failed')

    def test_system_retry_does_not_reactivate_job_while_same_repository_mr_is_active(self):
        review = Review.objects.create(project=self.project, repository_path='team/ui', mr_iid=7, status='failed')
        failed = ReviewJob.objects.create(project=self.project, review=review, status='failed',
                                         idempotency_key='failed', payload={'mr_iid': 7, 'repository_path': 'team/ui'})
        create_review_job(self.project, {'mr_iid': 7, 'repository_path': 'team/ui'}, 'improve')
        response = self.client.post('/api/system/retry/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)
        failed.refresh_from_db()
        self.assertEqual(failed.status, 'failed')

    @patch('review.integrations.gitlab.requests.get')
    def test_missing_pagination_header_on_a_full_page_requires_a_followup_page(self, get):
        first_page = page([mr(iid) for iid in range(1, 21)])
        first_page.headers = {}
        get.side_effect = [first_page, page([]), page([])]
        response = self.client.get('/api/overview/coverage/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['open_mrs'], 20)
        # Primary plus secondary repository; primary's full page needs a second page.
        self.assertEqual(response.data['state'], 'complete')
        self.assertEqual(get.call_count, 3)
