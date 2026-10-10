from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from review.models import AuditLog, Project, ProjectMembership


class ScopedAuditTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('audit-reader')
        self.admin = get_user_model().objects.create_user('audit-admin', is_staff=True)
        self.project = Project.objects.create(name='same-name', gitlab_project_path='team/a')
        self.foreign = Project.objects.create(name='same-name', gitlab_project_path='team/b')
        self.membership = ProjectMembership.objects.create(user=self.user, project=self.project)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('No external network'))
        network.start()
        self.addCleanup(network.stop)

    def test_own_project_audit_excludes_foreign_global_and_unassigned_legacy_rows(self):
        own = AuditLog.objects.create(project=self.project, action='测试企业微信通知', target='same-name')
        AuditLog.objects.create(project=self.foreign, action='测试企业微信通知', target='same-name')
        AuditLog.objects.create(action='测试企业微信通知', target='same-name')
        response = self.client.get(f'/api/projects/{self.project.pk}/audit-logs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data], [own.pk])
        self.assertEqual(response.json()[0]['project'], str(self.project.pk))
        self.assertEqual(self.client.get(f'/api/projects/{self.foreign.pk}/audit-logs/').status_code, 404)
        self.assertEqual(self.client.get('/api/audit-logs/').status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'/api/projects/{self.project.pk}/audit-logs/').status_code, 401)
        self.client.force_authenticate(self.admin)
        self.assertEqual(len(self.client.get('/api/audit-logs/').data), 3)

    def test_project_feed_is_bounded_and_newest_first(self):
        for index in range(105):
            AuditLog.objects.create(project=self.project, action=f'event-{index}')
        response = self.client.get(f'/api/projects/{self.project.pk}/audit-logs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 100)
        self.assertEqual(response.data[0]['action'], 'event-104')
        self.assertEqual(response.data[-1]['action'], 'event-5')

    def test_record_helper_snapshots_actor_and_preserves_exact_project(self):
        from review.services.audit import record_audit
        log = record_audit(user=self.user, project=self.project, action='修改配置', target='same-name')
        self.assertEqual(log.actor_user_id, self.user.pk)
        self.assertEqual(log.actor, self.user.username)
        self.assertEqual(log.project_id, self.project.pk)
        self.user.username = 'renamed'
        self.user.save()
        log.refresh_from_db()
        self.assertEqual(log.actor, 'audit-reader')

    def test_record_helper_uses_system_without_authenticated_actor(self):
        from review.services.audit import record_audit
        for actor in [None, AnonymousUser()]:
            log = record_audit(user=actor, action='后台任务')
            self.assertEqual(log.actor, 'system')
            self.assertIsNone(log.actor_user_id)
            self.assertIsNone(log.project_id)

    @override_settings(WECOM_WEBHOOK_URL='https://example.invalid/test-webhook')
    def test_project_test_and_save_record_user_and_scope_while_global_stays_unowned(self):
        self.membership.role = 'admin'
        self.membership.save()
        response = self.client.patch(f'/api/projects/{self.project.pk}/notification-config/', {'name': 'safe'}, format='json')
        self.assertEqual(response.status_code, 200)
        response_mock = Mock(status_code=200)
        response_mock.json.return_value = {'errcode': 0}
        with patch('review.integrations.wecom.requests.post', return_value=response_mock):
            response = self.client.post(f'/api/projects/{self.project.pk}/notification-config/test/', {}, format='json')
            self.assertEqual(response.status_code, 200)
            project_rows = AuditLog.objects.filter(project=self.project)
            self.assertEqual(project_rows.count(), 2)
            self.assertTrue(all(row.actor_user_id == self.user.pk and row.actor == self.user.username for row in project_rows))
            self.client.force_authenticate(self.admin)
            self.assertEqual(self.client.post('/api/notification-config/test/', {}, format='json').status_code, 200)
        global_row = AuditLog.objects.get(project__isnull=True)
        self.assertEqual(global_row.actor_user_id, self.admin.pk)
