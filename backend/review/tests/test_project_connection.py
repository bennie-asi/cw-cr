from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from review.models import GitLabConfig, Project, ProjectRepository
from review.tests.auth_helpers import admin_client


def upstream(payload, status=200, next_page=''):
    response = Mock(status_code=status, headers={'X-Next-Page': next_page})
    response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError('private upstream body token=test-token')
    return response


@override_settings(GITLAB_URL='https://gitlab.example', GITLAB_TOKEN='test-token')
class ProjectConnectionTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.url = '/api/projects/connection-check/'
        self.payload = {'repositories': [{'path': 'team/api', 'gitlab_url': '', 'gitlab_project_id': ''}]}
        self.webhook_url = 'http://testserver/api/webhooks/gitlab/'

    @patch('review.integrations.gitlab.requests.get')
    def test_preflight_normalizes_paths_checks_project_and_hook_without_saving(self, get):
        get.side_effect = [upstream({'id': 44, 'private': 'discard'}), upstream([
            {'url': self.webhook_url, 'merge_requests_events': True, 'alert_status': 'executable', 'disabled_until': None}
        ])]
        response = self.client.post(self.url, {'repositories': [{'path': ' https://gitlab.example/team/api.git/ '}]}, format='json')
        self.assertEqual(response.status_code, 200)
        row = response.data['repositories'][0]
        self.assertEqual(row['path'], 'team/api')
        self.assertEqual(row['connection']['state'], 'ready')
        self.assertEqual(row['webhook']['state'], 'ready')
        self.assertIn('Secret', row['webhook']['detail'])
        self.assertIn('投递', row['webhook']['detail'])
        self.assertEqual(response.data['webhook_url'], self.webhook_url)
        self.assertEqual((Project.objects.count(), ProjectRepository.objects.count(), GitLabConfig.objects.count()), (0, 0, 0))
        self.assertTrue(all(call.kwargs['allow_redirects'] is False for call in get.call_args_list))
        self.assertTrue(all(call.kwargs['timeout'] <= 3 for call in get.call_args_list))
        self.assertNotIn('test-token', str(response.data))
        self.assertNotIn('discard', str(response.data))

    @patch('review.integrations.gitlab.requests.get')
    def test_hook_permission_failure_preserves_repository_connection_and_reports_unknown(self, get):
        get.side_effect = [upstream({'id': 44}), upstream({}, status=403)]
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        row = response.data['repositories'][0]
        self.assertEqual(row['connection']['state'], 'ready')
        self.assertEqual(row['webhook']['state'], 'unknown')
        self.assertNotIn('private upstream body', str(response.data))

    @patch('review.integrations.gitlab.requests.get')
    def test_missing_disabled_or_wrong_event_hook_is_a_configuration_caution(self, get):
        for hooks in ([], [{'url': self.webhook_url, 'merge_requests_events': False}],
                      [{'url': self.webhook_url, 'merge_requests_events': True, 'alert_status': 'disabled'}],
                      [{'url': self.webhook_url, 'merge_requests_events': True, 'disabled_until': '2099-01-01T00:00:00Z'}]):
            with self.subTest(hooks=hooks):
                get.side_effect = [upstream({'id': 44}), upstream(hooks)]
                response = self.client.post(self.url, self.payload, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['repositories'][0]['webhook']['state'], 'missing')

    @override_settings(PUBLIC_BASE_URL='https://review.example')
    @patch('review.integrations.gitlab.requests.get')
    def test_configured_public_webhook_url_is_used_for_matching(self, get):
        get.side_effect = [upstream({'id': 44}), upstream([
            {'url': 'https://review.example/api/webhooks/gitlab/', 'merge_requests_events': True}
        ])]
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['webhook_url'], 'https://review.example/api/webhooks/gitlab/')
        self.assertEqual(response.data['repositories'][0]['webhook']['state'], 'ready')

    @patch('review.integrations.gitlab.requests.get')
    def test_hooks_are_paginated_before_declaring_them_missing(self, get):
        get.side_effect = [upstream({'id': 44}), upstream([], next_page='2'), upstream([
            {'url': self.webhook_url, 'merge_requests_events': True}
        ])]
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['repositories'][0]['webhook']['state'], 'ready')
        self.assertEqual(get.call_args.kwargs['params']['page'], 2)

    @patch('review.integrations.gitlab.requests.get')
    def test_other_origin_or_credentials_in_repository_url_never_receive_the_global_token(self, get):
        for address in ('https://evil.example', 'http://gitlab.example', 'https://gitlab.example:444',
                        'https://user:password@gitlab.example'):
            with self.subTest(address=address):
                response = self.client.post(self.url, {'repositories': [{'path': 'team/api', 'gitlab_url': address}]}, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['repositories'][0]['connection']['state'], 'error')
                self.assertNotIn('password', str(response.data))
        get.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    def test_explicit_default_port_is_ready_and_usable_by_the_mr_client(self, get):
        project = Project.objects.create(name='API', gitlab_project_path='team/api', gitlab_url='https://gitlab.example:443')
        get.side_effect = [upstream({'id': 44}), upstream([]), upstream([])]
        response = self.client.post(self.url, {'repositories': [{'path': 'team/api', 'gitlab_url': project.gitlab_url}]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['repositories'][0]['connection']['state'], 'ready')
        response = self.client.get(f'/api/projects/{project.pk}/merge-requests/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_count, 3)

    @patch('review.integrations.gitlab.requests.get')
    def test_redirects_are_not_success_and_upstream_exceptions_are_sanitized(self, get):
        for result in (upstream({'id': 44}, status=302), requests.ConnectionError('token=test-token private body')):
            with self.subTest(result=type(result)):
                get.side_effect = [result]
                response = self.client.post(self.url, self.payload, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['repositories'][0]['connection']['state'], 'error')
                self.assertNotIn('test-token', str(response.data))
                self.assertNotIn('private body', str(response.data))

    @override_settings(GITLAB_URL='', GITLAB_TOKEN='')
    @patch('review.integrations.gitlab.requests.get')
    def test_unconfigured_or_disabled_global_connection_is_not_ready(self, get):
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['repositories'][0]['connection']['state'], 'unconfigured')
        GitLabConfig.objects.create(base_url='https://gitlab.example', api_token='test-token', enabled=False)
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.data['repositories'][0]['connection']['state'], 'unconfigured')
        get.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    def test_admin_only_and_anonymous_checks_make_no_external_request(self, get):
        user = get_user_model().objects.create_user('project-check-member')
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(client.post(self.url, self.payload, format='json').status_code, 403)
        self.assertEqual(APIClient().post(self.url, self.payload, format='json').status_code, 401)
        get.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    def test_validates_repository_inputs_and_caps_the_repository_count(self, get):
        for body in ({}, {'repositories': []}, {'repositories': [{'path': 'invalid'}]},
                     {'repositories': [{'path': 'team/api'}, {'path': 'team/api.git'}]},
                     {'repositories': [{'path': f'team/repo-{i}'} for i in range(11)]}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(self.url, body, format='json').status_code, 400)
        get.assert_not_called()

    @patch('review.integrations.gitlab.requests.get')
    def test_repository_paths_normalize_leading_slashes(self, get):
        get.side_effect = [upstream({'id': 44}), upstream([])]
        response = self.client.post(self.url, {'repositories': [{'path': ' /team/api.git/ '}]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['repositories'][0]['path'], 'team/api')

    @patch('review.integrations.gitlab.requests.get')
    def test_malformed_repository_urls_are_validation_errors(self, get):
        self.client.raise_request_exception = False
        response = self.client.post(self.url, {'repositories': [{'path': 'https://[bad]/team/api'}]}, format='json')
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    @override_settings(PROJECT_CONNECTION_MAX_REQUESTS=1)
    @patch('review.integrations.gitlab.requests.get')
    def test_probe_request_budget_limits_paginated_hook_work(self, get):
        get.return_value = upstream({'id': 44})
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['repositories'][0]['connection']['state'], 'ready')
        self.assertEqual(response.data['repositories'][0]['webhook']['state'], 'unknown')
        self.assertEqual(get.call_count, 1)
