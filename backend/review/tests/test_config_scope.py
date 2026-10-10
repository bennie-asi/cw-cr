from django.test import TestCase, override_settings
from unittest.mock import patch
from review.tests.auth_helpers import admin_client

from review.models import ModelConfig, NotificationConfig, Project, Review


class ConfigScopeTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='scope', gitlab_project_path='team/scope')

    def test_global_configuration_cannot_be_reassigned_to_project(self):
        for model, endpoint in ((ModelConfig, 'model-config'), (NotificationConfig, 'notification-config')):
            with self.subTest(endpoint=endpoint):
                config = model.objects.create(scope='global', project=None)
                response = self.client.patch(f'/api/{endpoint}/', {
                    'scope': 'project', 'project': str(self.project.pk),
                }, format='json')
                self.assertEqual(response.status_code, 200)
                config.refresh_from_db()
                self.assertEqual(config.scope, 'global')
                self.assertIsNone(config.project_id)
                self.assertEqual(model.objects.filter(scope='global', project=None).count(), 1)

    def test_project_configuration_uses_url_owner_not_submitted_owner(self):
        other = Project.objects.create(name='other', gitlab_project_path='team/other')
        for model, endpoint in ((ModelConfig, 'model-config'), (NotificationConfig, 'notification-config')):
            with self.subTest(endpoint=endpoint):
                global_config = model.objects.create(scope='global', project=None)
                response = self.client.patch(f'/api/projects/{self.project.pk}/{endpoint}/', {
                    'scope': 'global', 'project': str(other.pk),
                }, format='json')
                self.assertEqual(response.status_code, 200)
                owned = model.objects.get(project=self.project)
                self.assertEqual(owned.scope, 'project')
                self.assertFalse(model.objects.filter(project=other).exists())
                global_config.refresh_from_db()
                self.assertIsNone(global_config.project_id)

    def test_restore_inheritance_preserves_saved_overrides_and_global_secrets(self):
        for model, endpoint, flag, field in (
            (ModelConfig, 'model-config', 'model_inherit', 'api_key'),
            (NotificationConfig, 'notification-config', 'notification_inherit', 'webhook_url'),
        ):
            with self.subTest(endpoint=endpoint):
                global_config = model.objects.create(scope='global', **{field: 'global-secret'})
                override = model.objects.create(scope='project', project=self.project, **{field: 'project-secret'})
                setattr(self.project, flag, False)
                self.project.save()
                response = self.client.patch(f'/api/projects/{self.project.pk}/{endpoint}/', {'inherited': True}, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertIs(response.data['inherited'], True)
                self.project.refresh_from_db()
                self.assertTrue(getattr(self.project, flag))
                override.refresh_from_db()
                global_config.refresh_from_db()
                self.assertEqual(getattr(override, field), 'project-secret')
                self.assertEqual(getattr(global_config, field), 'global-secret')
                self.assertNotIn('global-secret', str(response.data))

    def test_restore_inheritance_rejects_mixed_configuration_payload(self):
        for endpoint in ('model-config', 'notification-config'):
            response = self.client.patch(f'/api/projects/{self.project.pk}/{endpoint}/', {'inherited': True, 'enabled': False}, format='json')
            self.assertEqual(response.status_code, 400)

    @override_settings(WECOM_WEBHOOK_URL='https://wecom.example/global-hook')
    def test_unconfigured_project_override_does_not_send_to_global_group(self):
        from review.integrations.wecom import WeComClient
        from review.services.review_runner import _notification_config
        self.project.notification_inherit = False
        self.project.save(update_fields=('notification_inherit',))
        review = Review.objects.create(project=self.project, mr_iid=7, summary='真实总评')
        with patch('review.integrations.wecom.requests.post') as post:
            result = WeComClient(_notification_config(self.project)).send_review(review)
        self.assertTrue(result['skipped'])
        post.assert_not_called()
        project_response = self.client.get(f'/api/projects/{self.project.pk}/')
        self.assertIn('项目通知覆盖尚未保存', '；'.join(project_response.data['status_reasons']))
