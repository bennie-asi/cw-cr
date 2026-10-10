from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from review.models import Project, Review


@override_settings(REVIEW_EXECUTE_INLINE=False)
class WorkspacePermissionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('member')
        self.admin = get_user_model().objects.create_user('admin', is_staff=True)
        self.project = Project.objects.create(name='visible', gitlab_project_path='team/visible')
        self.other = Project.objects.create(name='hidden', gitlab_project_path='team/hidden')
        self.review = Review.objects.create(project=self.project, mr_iid=1)
        self.foreign_review = Review.objects.create(project=self.other, mr_iid=2)
        self.client = APIClient()
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('Permission tests must never use network'))
        network.start()
        self.addCleanup(network.stop)

    def join(self, role='member'):
        from review.models import ProjectMembership
        ProjectMembership.objects.create(project=self.project, user=self.user, role=role)
        self.client.force_authenticate(self.user)

    def test_project_response_roles_follow_request_user_on_list_detail_and_update(self):
        self.join('admin')
        self.assertEqual(self.client.get('/api/projects/').data['results'][0]['current_user_role'], 'admin')
        self.assertEqual(self.client.get(f'/api/projects/{self.project.pk}/').data['current_user_role'], 'admin')
        self.assertEqual(self.client.patch(f'/api/projects/{self.project.pk}/', {'name': 'changed'}, format='json').data['current_user_role'], 'admin')
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/projects/', {'name': 'new', 'gitlab_project_path': 'team/new'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['current_user_role'], 'admin')

    def project_routes(self, project, review):
        p, r = project.pk, review.pk
        return [
            ('get', f'/api/projects/{p}/'), ('patch', f'/api/projects/{p}/'),
            ('get', f'/api/projects/{p}/policy/'), ('patch', f'/api/projects/{p}/policy/'),
            ('get', f'/api/projects/{p}/model-config/'), ('patch', f'/api/projects/{p}/model-config/'),
            ('post', f'/api/projects/{p}/model-config/test/'),
            ('get', f'/api/projects/{p}/notification-config/'), ('patch', f'/api/projects/{p}/notification-config/'),
            ('post', f'/api/projects/{p}/notification-config/test/'),
            ('get', f'/api/projects/{p}/merge-requests/'), ('get', f'/api/projects/{p}/reviews/'),
            ('post', f'/api/projects/{p}/reviews/'), ('post', f'/api/projects/{p}/trigger/'),
            ('get', f'/api/reviews/{r}/'), ('patch', f'/api/reviews/{r}/summary/'),
            ('post', f'/api/reviews/{r}/notify/'), ('post', f'/api/reviews/{r}/retry/'),
            ('post', f'/api/reviews/{r}/merge/'),
            ('post', f'/api/reviews/{r}/comments/{uuid4()}/publish/'),
            ('post', f'/api/reviews/{r}/comments/{uuid4()}/resolve/'),
            ('get', f'/api/merge-gate/?project={p}'), ('patch', f'/api/merge-gate/?project={p}'),
            ('post', f'/api/merge-gate/dry-run/?project={p}'),
        ]

    def global_routes(self):
        return [
            ('get', '/api/system/'), ('post', '/api/system/retry/'), ('get', '/api/audit-logs/'),
            *[(method, f'/api/{name}/') for name in ['model-config', 'notification-config', 'gitlab-config'] for method in ['get', 'patch']],
            *[('post', f'/api/{name}/test/') for name in ['model-config', 'notification-config', 'gitlab-config']],
        ]

    def test_all_business_endpoints_reject_anonymous_before_any_action(self):
        routes = self.project_routes(self.project, self.review) + self.global_routes() + [
            ('get', '/api/projects/'), ('post', '/api/projects/'), ('get', '/api/reviews/'), ('get', '/api/overview/'),
        ]
        for method, path in routes:
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 401)

    def test_foreign_project_and_review_ids_are_not_accessible(self):
        self.join()
        for method, path in self.project_routes(self.other, self.foreign_review):
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 404)

    def test_member_can_read_configuration_but_cannot_write_or_probe_or_merge(self):
        self.join()
        for suffix in ['', 'policy/', 'model-config/', 'notification-config/']:
            path = f'/api/projects/{self.project.pk}/{suffix}'
            self.assertEqual(self.client.get(path).status_code, 200)
            self.assertEqual(self.client.patch(path, {}, format='json').status_code, 403)
        for suffix in ['model-config/test/', 'notification-config/test/']:
            self.assertEqual(self.client.post(f'/api/projects/{self.project.pk}/{suffix}').status_code, 403)
        self.assertEqual(self.client.post(f'/api/reviews/{self.review.pk}/merge/').status_code, 403)
        self.assertEqual(self.client.patch(f'/api/merge-gate/?project={self.project.pk}').status_code, 403)
        self.assertEqual(self.client.post(f'/api/merge-gate/dry-run/?project={self.project.pk}').status_code, 403)

    def test_project_admin_can_manage_own_project_but_never_global_configuration(self):
        self.join('admin')
        self.assertEqual(self.client.patch(f'/api/projects/{self.project.pk}/policy/', {'command_review': False}, format='json').status_code, 200)
        for method, path in self.global_routes():
            with self.subTest(path=path, method=method):
                self.assertEqual(getattr(self.client, method)(path).status_code, 403)
        self.assertEqual(self.client.post('/api/projects/', {}, format='json').status_code, 403)

    def test_project_admin_cannot_rebind_repository_identity_to_use_global_gitlab_token(self):
        self.join('admin')
        original = (self.project.gitlab_project_path, self.project.gitlab_project_id, self.project.gitlab_url, self.project.name)
        for field, value in [('gitlab_project_path', 'private/another'), ('gitlab_project_id', '999'), ('gitlab_url', 'https://another.example')]:
            with self.subTest(field=field):
                response = self.client.patch(f'/api/projects/{self.project.pk}/', {field: value, 'name': 'partial-write-forbidden'}, format='json')
                self.assertEqual(response.status_code, 403)
                self.project.refresh_from_db()
                self.assertEqual((self.project.gitlab_project_path, self.project.gitlab_project_id, self.project.gitlab_url, self.project.name), original)
        self.assertEqual(self.client.patch(f'/api/projects/{self.project.pk}/', {'name': 'renamed'}, format='json').status_code, 200)

    def test_platform_admin_can_rebind_project_repository(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f'/api/projects/{self.project.pk}/', {'gitlab_project_id': '999'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.gitlab_project_id, '999')

    def test_platform_admin_can_read_global_and_all_project_data(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/system/').status_code, 200)
        self.assertEqual(self.client.get('/api/projects/').json()['count'], 2)
        self.assertEqual(self.client.get(f'/api/projects/{self.other.pk}/policy/').status_code, 200)

    def test_collections_and_overview_only_aggregate_visible_projects(self):
        self.join()
        self.assertEqual([p['id'] for p in self.client.get('/api/projects/').json()['results']], [str(self.project.pk)])
        self.assertEqual([r['id'] for r in self.client.get('/api/reviews/').json()['results']], [str(self.review.pk)])
        data = self.client.get('/api/overview/').json()
        self.assertEqual(data['projects'], 1)
        self.assertEqual(data['reviews'], 1)
        self.assertEqual([r['id'] for r in data['recent_reviews']], [str(self.review.pk)])

    def test_unassigned_user_has_empty_aggregate_not_all_projects(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get('/api/projects/').json()['count'], 0)
        self.assertEqual(self.client.get('/api/reviews/').json()['count'], 0)
        self.assertEqual(self.client.get('/api/overview/').json()['projects'], 0)

    def test_gate_and_project_filters_require_valid_visible_explicit_id(self):
        self.join()
        for path in ['/api/merge-gate/', '/api/merge-gate/?project=not-a-uuid', '/api/reviews/?project=invalid']:
            self.assertEqual(self.client.get(path).status_code, 400)
        self.assertEqual(self.client.get(f'/api/reviews/?project={self.other.pk}').status_code, 404)
        self.assertEqual(self.client.get(f'/api/reviews/?project={uuid4()}').status_code, 404)
        self.assertEqual(self.client.get(f'/api/reviews/?project={self.project.pk}').json()['count'], 1)
        self.assertEqual(self.client.get(f'/api/merge-gate/?project={self.project.pk}').json()['project_id'], str(self.project.pk))

    def test_member_can_trigger_review_but_cannot_choose_foreign_project_in_body(self):
        self.join()
        response = self.client.post(f'/api/projects/{self.project.pk}/trigger/', {'mr_iid': 1, 'project': str(self.other.pk)}, format='json')
        self.assertEqual(response.status_code, 202)
        from review.models import ReviewJob
        self.assertEqual(ReviewJob.objects.get().project_id, self.project.pk)

    def test_health_and_secret_webhook_remain_independent_of_session(self):
        self.assertEqual(self.client.get('/api/health/').status_code, 200)
        self.project.webhook_secret = 'test-secret'
        self.project.save()
        payload = {'project': {'path_with_namespace': self.project.gitlab_project_path}, 'object_kind': 'push'}
        self.assertEqual(self.client.post('/api/webhooks/gitlab/', payload, format='json').status_code, 403)
        self.assertEqual(self.client.post('/api/webhooks/gitlab/', payload, format='json', HTTP_X_GITLAB_TOKEN='test-secret').status_code, 200)

    def test_unknown_routes_and_disabled_users_are_denied(self):
        from review.permissions import WorkspacePermission, visible_projects, is_platform_admin
        from django.contrib.auth.models import AnonymousUser
        permission = WorkspacePermission()
        request = SimpleNamespace(user=self.admin, resolver_match=SimpleNamespace(url_name='future-unclassified'), method='GET')
        self.assertFalse(permission.has_permission(request, SimpleNamespace(kwargs={})))
        self.admin.is_active = False
        self.assertFalse(is_platform_admin(self.admin))
        self.assertFalse(visible_projects(self.admin).exists())
        self.assertFalse(visible_projects(AnonymousUser()).exists())
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/projects/').status_code, 403)

    def test_project_admin_cannot_forward_inherited_key_to_new_gateway(self):
        from review.models import ModelConfig
        self.join('admin')
        ModelConfig.objects.create(scope='global', base_url='https://trusted.example/v1', api_key='global-private-key', model='m')
        for method, suffix in [('patch', ''), ('post', 'test/')]:
            with self.subTest(method=method):
                response = getattr(self.client, method)(f'/api/projects/{self.project.pk}/model-config/{suffix}', {
                    'base_url': 'https://different.example/v1',
                }, format='json')
                self.assertEqual(response.status_code, 400)
        self.assertFalse(ModelConfig.objects.filter(project=self.project).exists())

    def test_new_gateway_with_explicit_project_key_is_allowed(self):
        from review.models import ModelConfig
        self.join('admin')
        ModelConfig.objects.create(scope='global', base_url='https://trusted.example/v1', api_key='global-private-key', model='m')
        response = self.client.patch(f'/api/projects/{self.project.pk}/model-config/', {
            'base_url': 'https://different.example/v1', 'api_key': 'project-specific-key',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        config = ModelConfig.objects.get(project=self.project)
        self.assertEqual(config.api_key, 'project-specific-key')

    def test_same_gateway_can_keep_inherited_key_for_model_override(self):
        from review.models import ModelConfig
        self.join('admin')
        ModelConfig.objects.create(scope='global', base_url='https://trusted.example/v1', api_key='global-private-key', model='m')
        response = self.client.patch(f'/api/projects/{self.project.pk}/model-config/', {'model': 'another'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ModelConfig.objects.get(project=self.project).api_key, 'global-private-key')

    @override_settings(CCSWITCH_BASE_URL='https://trusted.example/v1', CCSWITCH_API_KEY='environment-secret')
    def test_environment_key_is_never_fallback_for_another_gateway(self):
        from review.models import ModelConfig
        from review.integrations.llm import LLMClient
        untrusted = LLMClient(ModelConfig(base_url='https://different.example/v1', api_key=''))
        self.assertEqual(untrusted.api_key, '')
        self.assertFalse(untrusted.connection_enabled)
        trusted = LLMClient(ModelConfig(base_url='https://trusted.example/v1/', api_key=''))
        self.assertEqual(trusted.api_key, 'environment-secret')

    @override_settings(CCSWITCH_BASE_URL='https://trusted.example/v1', CCSWITCH_API_KEY='environment-secret')
    def test_project_configuration_status_does_not_claim_unrelated_environment_key_is_usable(self):
        from review.models import ModelConfig
        self.join()
        ModelConfig.objects.create(scope='global', base_url='https://different.example/v1', api_key='', model='m')
        response = self.client.get(f'/api/projects/{self.project.pk}/')
        self.assertFalse(response.json()['model']['configured'])

    def test_membership_is_unique_and_superuser_needs_no_membership(self):
        from django.db import IntegrityError, transaction
        from review.models import ProjectMembership
        from review.permissions import is_platform_admin, visible_projects
        self.join()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectMembership.objects.create(project=self.project, user=self.user)
        self.user.is_superuser = True
        self.assertTrue(is_platform_admin(self.user))
        self.assertEqual(visible_projects(self.user).count(), 2)

    def test_own_details_are_visible_but_foreign_comment_cannot_be_addressed_via_own_review(self):
        from review.models import LineComment
        self.join()
        self.assertEqual(self.client.get(f'/api/reviews/{self.review.pk}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/projects/{self.project.pk}/reviews/').json()['count'], 1)
        comment = LineComment.objects.create(review=self.foreign_review, file_path='secret.py', new_line=1)
        for action in ['publish', 'resolve']:
            self.assertEqual(self.client.post(f'/api/reviews/{self.review.pk}/comments/{comment.pk}/{action}/').status_code, 404)

    def test_session_csrf_still_protects_business_writes_but_not_webhook_secret_ingress(self):
        self.join('admin')
        strict = APIClient(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.get(f'/api/projects/{self.project.pk}/').status_code, 200)
        response = strict.patch(f'/api/projects/{self.project.pk}/policy/', {'command_review': False}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertIn('CSRF', response.json()['detail'])
        self.project.webhook_secret = 'separate-hook-secret'
        self.project.save()
        response = strict.post('/api/webhooks/gitlab/', {
            'project': {'path_with_namespace': self.project.gitlab_project_path}, 'object_kind': 'push',
        }, format='json', HTTP_X_GITLAB_TOKEN='separate-hook-secret')
        self.assertEqual(response.status_code, 200)
