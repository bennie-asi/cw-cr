from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from review.models import Project, Review
from review.tests.auth_helpers import admin_client


@override_settings(GITLAB_URL='https://gitlab.example', GITLAB_TOKEN='test-token')
class MergeRequestListTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='sample', gitlab_project_path='group/sample')
        self.url = f'/api/projects/{self.project.pk}/merge-requests/'

    @patch('review.integrations.gitlab.requests.get')
    def test_lists_real_remote_mrs_without_creating_reviews(self, get):
        get.return_value = Mock(headers={'X-Next-Page': '3'})
        get.return_value.json.return_value = [{'iid': 357, 'title': 'Test MR', 'state': 'opened', 'private_field': 'not exposed'}]
        response = self.client.get(self.url, {'page': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['iid'], 357)
        self.assertNotIn('private_field', response.json()['results'][0])
        self.assertTrue(response.json()['has_next'])
        self.assertEqual(get.call_args.kwargs['params']['page'], 2)
        self.assertEqual(Review.objects.count(), 0)

    def test_rejects_invalid_page(self):
        self.assertEqual(self.client.get(self.url, {'page': '-1'}).status_code, 400)

    @patch('review.integrations.gitlab.requests.get', side_effect=RuntimeError('upstream error'))
    def test_remote_error_is_not_an_empty_success(self, get):
        self.assertEqual(self.client.get(self.url).status_code, 502)
