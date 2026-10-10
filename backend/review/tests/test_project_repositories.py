from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from review.models import Project, Review, ReviewJob
from review.serializers import ProjectSerializer
from review.services.review_runner import create_review_job, run_review_job
from review.tests.auth_helpers import admin_client


@override_settings(GITLAB_URL='https://gitlab.example', GITLAB_TOKEN='test-token')
class ProjectRepositoryTests(TestCase):
    def setUp(self):
        self.client = admin_client()

    def test_create_edit_and_pause_project_with_multiple_repositories(self):
        created = self.client.post('/api/projects/', {
            'name': 'chaos', 'owner': 'team',
            'repositories': [
                {'path': 'team/chaos-api'},
                {'path': 'team/chaos-ui'},
            ],
            'model_inherit': True, 'notification_inherit': False,
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        project_id = created.data['id']
        self.assertEqual([row['path'] for row in created.data['repositories']], ['team/chaos-api', 'team/chaos-ui'])
        self.assertFalse(created.data['notification_inherit'])
        changed = self.client.patch(f'/api/projects/{project_id}/', {
            'name': 'chaos platform', 'enabled': False,
            'repositories': [{'path': 'team/chaos-ui'}, {'path': 'team/chaos-worker'}],
        }, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['status'], '已暂停')
        self.assertEqual([row['path'] for row in changed.data['repositories']], ['team/chaos-ui', 'team/chaos-worker'])
        self.assertEqual(self.client.patch(f'/api/projects/{project_id}/', {'enabled': True}, format='json').status_code, 200)

    def test_duplicate_repository_is_rejected(self):
        Project.objects.create(name='existing', gitlab_project_path='team/existing')
        created = self.client.post('/api/projects/', {
            'name': 'new', 'repositories': [{'path': 'team/existing'}],
        }, format='json')
        self.assertEqual(created.status_code, 400)

    def test_repository_cannot_change_owner_after_validation(self):
        first = Project.objects.create(name='first', gitlab_project_path='team/first')
        second = Project.objects.create(name='second', gitlab_project_path='team/second')
        serializer = ProjectSerializer(first, data={'repositories': [{'path': 'team/new'}]}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        from review.models import ProjectRepository
        ProjectRepository.objects.create(project=second, path='team/new')
        with self.assertRaises(ValidationError):
            serializer.save()
        first.refresh_from_db()
        self.assertEqual(first.gitlab_project_path, 'team/first')
        self.assertEqual(ProjectRepository.objects.get(path='team/new').project_id, second.pk)

    @override_settings(GITLAB_WEBHOOK_SECRET='hook-secret', REVIEW_EXECUTE_INLINE=False)
    def test_webhook_routes_secondary_repository_without_mr_iid_collision(self):
        project = Project.objects.create(name='chaos', gitlab_project_path='team/api')
        from review.models import ProjectRepository
        ProjectRepository.objects.create(project=project, path='team/ui')
        payload = {
            'object_kind': 'merge_request',
            'project': {'path_with_namespace': 'team/ui'},
            'object_attributes': {'iid': 7, 'action': 'open'},
        }
        response = self.client.post('/api/webhooks/gitlab/', payload, format='json', HTTP_X_GITLAB_TOKEN='hook-secret')
        self.assertEqual(response.status_code, 202, response.data)
        job = ReviewJob.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.project_id, project.pk)
        self.assertEqual(job.payload['repository_path'], 'team/ui')

    def test_multi_repository_manual_trigger_requires_explicit_selection(self):
        project = Project.objects.create(name='chaos', gitlab_project_path='team/api')
        from review.models import ProjectRepository
        ProjectRepository.objects.create(project=project, path='team/ui')
        response = self.client.post(f'/api/projects/{project.pk}/trigger/', {'mr_iid': 7}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('repository_path', response.data['detail'])
        response = self.client.get(f'/api/projects/{project.pk}/merge-requests/', {'state': 'closed', 'repository': 'team/ui'})
        self.assertEqual(response.status_code, 400)

    @patch('review.integrations.gitlab.requests.get')
    def test_mr_list_uses_selected_repository_and_merged_state(self, get):
        project = Project.objects.create(name='chaos', gitlab_project_path='team/api')
        from review.models import ProjectRepository
        ProjectRepository.objects.create(project=project, path='team/ui')
        get.return_value = Mock(headers={'X-Next-Page': ''})
        get.return_value.json.return_value = [{'iid': 7, 'title': 'merged', 'state': 'merged'}]
        response = self.client.get(f'/api/projects/{project.pk}/merge-requests/', {'repository': 'team/ui', 'state': 'merged'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get.call_args.kwargs['params']['state'], 'merged')
        self.assertIn('team%2Fui', get.call_args.args[0])
        self.assertEqual(response.data['results'][0]['state'], 'merged')

    @override_settings(REVIEW_ALLOW_TEST_FALLBACK=True, GITLAB_URL='', GITLAB_TOKEN='')
    def test_same_iid_in_two_repositories_keeps_review_identity(self):
        project = Project.objects.create(name='chaos', gitlab_project_path='team/api')
        from review.models import ProjectRepository
        ProjectRepository.objects.create(project=project, path='team/ui')
        first = create_review_job(project, {'mr_iid': 7, 'repository_path': 'team/api', 'event_uuid': 'same-event'})
        second = create_review_job(project, {'mr_iid': 7, 'repository_path': 'team/ui', 'event_uuid': 'same-event'})
        self.assertNotEqual(first.pk, second.pk)
        run_review_job(first.pk)
        run_review_job(second.pk)
        self.assertEqual(set(Review.objects.filter(project=project).values_list('repository_path', flat=True)), {'team/api', 'team/ui'})

    def test_gate_latest_review_check_does_not_cross_repository(self):
        from review.models import ProjectPolicy
        from review.services.gate_assessment import assess_review
        project = Project.objects.create(name='chaos', gitlab_project_path='team/api')
        policy = ProjectPolicy.objects.create(project=project)
        api_review = Review.objects.create(project=project, repository_path='team/api', mr_iid=7, summary='中文', status=Review.Status.COMPLETED)
        Review.objects.create(project=project, repository_path='team/ui', mr_iid=7, summary='中文', status=Review.Status.COMPLETED)
        result = assess_review(api_review, policy, {'iid': 7, 'state': 'opened'})
        self.assertTrue(result['checks']['latest_review'])

    @override_settings(REVIEW_ALLOW_TEST_FALLBACK=True, GITLAB_URL='', GITLAB_TOKEN='')
    @patch('review.integrations.gitlab.GitLabClient.merge')
    @patch('review.integrations.gitlab.GitLabClient.fetch_merge_request')
    def test_merged_mr_review_does_not_attempt_auto_merge(self, fetch_mr, merge):
        from review.models import ProjectPolicy
        project = Project.objects.create(name='chaos', gitlab_project_path='team/api')
        ProjectPolicy.objects.create(project=project, auto_merge_enabled=True)
        fetch_mr.return_value = {'iid': 7, 'state': 'merged', 'title': '历史 MR', 'diff_refs': {}, 'web_url': ''}
        job = create_review_job(project, {'mr_iid': 7})
        run_review_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED)
        self.assertEqual(job.merge_status, 'skipped')
        merge.assert_not_called()
