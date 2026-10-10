from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from types import SimpleNamespace

from review.models import GitLabConfig, ModelConfig, NotificationConfig, Project, ProjectMembership
from review.serializers import GitLabConfigSerializer, ModelConfigSerializer, NotificationConfigSerializer, ProjectSerializer


class SerializerTests(TestCase):
    def test_current_project_role_is_request_specific_and_read_only(self):
        project = Project.objects.create(name='role', gitlab_project_path='team/role')
        member = get_user_model().objects.create_user('reader')
        membership = ProjectMembership.objects.create(project=project, user=member)
        admin = get_user_model().objects.create_user('admin', is_staff=True)
        other = get_user_model().objects.create_user('other')
        for user, expected in [(member, 'member'), (admin, 'admin'), (other, None), (AnonymousUser(), None)]:
            with self.subTest(user=str(user)):
                serializer = ProjectSerializer(project, context={'request': SimpleNamespace(user=user)})
                self.assertEqual(serializer.data['current_user_role'], expected)
        membership.role = 'admin'
        membership.save()
        self.assertEqual(ProjectSerializer(project, context={'request': SimpleNamespace(user=member)}).data['current_user_role'], 'admin')
        member.is_active = False
        self.assertIsNone(ProjectSerializer(project, context={'request': SimpleNamespace(user=member)}).data['current_user_role'])
        self.assertIsNone(ProjectSerializer(project).data['current_user_role'])
        serializer = ProjectSerializer(project, data={'current_user_role': 'admin'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('current_user_role', serializer.validated_data)

    def test_gitlab_config_serializer_masks_secrets_and_reports_configuration(self):
        config = GitLabConfig.objects.create(
            base_url='https://gitlab.example.com',
            api_token='glpat-super-secret',
            webhook_secret='hook-super-secret',
        )

        data = GitLabConfigSerializer(config).data

        self.assertEqual(data['base_url'], 'https://gitlab.example.com')
        self.assertEqual(data['api_token_masked'], 'glpa••••cret')
        self.assertTrue(data['api_token_configured'])
        self.assertTrue(data['webhook_secret_configured'])
        self.assertNotIn('glpat-super-secret', str(data))
        self.assertNotIn('hook-super-secret', str(data))

    def test_model_config_serializer_masks_api_key(self):
        config = ModelConfig.objects.create(
            scope=ModelConfig.Scope.GLOBAL,
            base_url='https://ccswitch.example/v1',
            api_key='sk-super-secret-value',
        )

        data = ModelConfigSerializer(config).data

        self.assertEqual(data['api_key_masked'], 'sk-s••••alue')
        self.assertNotIn('sk-super-secret-value', str(data))
        self.assertNotIn('engine', data)

    def test_model_config_serializer_does_not_own_review_engine_selection(self):
        config = ModelConfig.objects.create(scope=ModelConfig.Scope.GLOBAL)
        serializer = ModelConfigSerializer(config, data={'engine': 'pi-agent'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('engine', serializer.validated_data)
        serializer.save()
        config.refresh_from_db()
        self.assertEqual(config.model, '')

    def test_project_serializer_exposes_policy_summary_without_webhook_secret(self):
        project = Project.objects.create(
            name='demo',
            gitlab_project_path='group/demo',
            webhook_secret='private-secret',
        )

        data = ProjectSerializer(project).data

        self.assertEqual(data['name'], 'demo')
        self.assertNotIn('private-secret', str(data))
        self.assertIn('policy', data)

    @override_settings(WECOM_WEBHOOK_URL='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=env')
    def test_notification_serializer_reports_environment_webhook(self):
        config = NotificationConfig.objects.create(webhook_url='')

        data = NotificationConfigSerializer(config).data

        self.assertTrue(data['webhook_configured'])

    def test_notification_serializer_rejects_invalid_webhook_without_replacing_saved_url(self):
        config = NotificationConfig.objects.create(webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=kept')
        for value in ('invalid-webhook', 'ftp://example.com/send', 'javascript:alert(1)'):
            with self.subTest(value=value):
                serializer = NotificationConfigSerializer(config, data={'webhook_url': value}, partial=True)
                self.assertFalse(serializer.is_valid())
                self.assertIn('webhook_url', serializer.errors)
                config.refresh_from_db()
                self.assertEqual(config.webhook_url, 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=kept')

    def test_notification_serializer_accepts_http_and_https_webhooks_without_exposing_them(self):
        config = NotificationConfig.objects.create()
        for value in ('http://127.0.0.1:8021/wecom', 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test'):
            with self.subTest(value=value):
                serializer = NotificationConfigSerializer(config, data={'webhook_url': value}, partial=True)
                self.assertTrue(serializer.is_valid(), serializer.errors)
                config = serializer.save()
                self.assertEqual(config.webhook_url, value)
                self.assertNotIn('webhook_url', serializer.data)
