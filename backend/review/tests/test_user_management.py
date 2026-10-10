from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from review.models import AuditLog, Project, ProjectMembership
from review.services.execution_locks import resource_lock


class UserManagementTests(TestCase):
    password = 'Unique-test-credential-481!'

    def setUp(self):
        self.admin = get_user_model().objects.create_user('platform', is_staff=True)
        self.member = get_user_model().objects.create_user('member')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = '/api/users/'
        sentinel = patch('requests.sessions.Session.request', side_effect=AssertionError('No external identity requests'))
        sentinel.start()
        self.addCleanup(sentinel.stop)

    def detail(self, user=None):
        return f'{self.url}{(user or self.member).pk}/'

    def payload(self, **changes):
        return dict(username='created', display_name='新用户', password=self.password, **changes)

    def test_admin_create_and_list_safe_fields_and_audit(self):
        result = self.client.post(self.url, self.payload(), format='json')
        self.assertEqual(result.status_code, 201)
        created = get_user_model().objects.get(username='created')
        self.assertTrue(created.check_password(self.password))
        self.assertFalse(created.is_staff or created.is_superuser)
        self.assertTrue(created.is_active)
        self.assertEqual(result.data['display_name'], '新用户')
        rows = self.client.get(self.url)
        self.assertEqual(rows.status_code, 200)
        self.assertEqual(len(rows.data), 3)
        self.assertEqual(set(rows.data[0]), {'id', 'username', 'display_name', 'platform_role', 'is_active', 'last_login'})
        audit = AuditLog.objects.get()
        self.assertEqual(audit.actor_user, self.admin)
        self.assertIsNone(audit.project_id)
        self.assertEqual(audit.metadata['user_id'], created.pk)
        self.assertNotIn(self.password, str(audit.metadata))
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 400)

    def test_anonymous_and_project_admin_cannot_manage_accounts(self):
        project = Project.objects.create(name='A', gitlab_project_path='a')
        ProjectMembership.objects.create(project=project, user=self.member, role='admin')
        for actor, status in [(None, 401), (self.member, 403)]:
            self.client.force_authenticate(actor)
            self.assertEqual(self.client.get(self.url).status_code, status)
            self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, status)
            self.assertEqual(self.client.patch(self.detail(), {'platform_role': 'admin'}, format='json').status_code, status)
            self.assertEqual(self.client.post(self.detail() + 'reset-password/', {'new_password': self.password}, format='json').status_code, status)
        self.assertFalse(AuditLog.objects.exists())

    def test_create_schema_and_password_validation(self):
        invalid = [[], {}, {'username': 'only'}, self.payload(is_superuser=True), self.payload(platform_role='owner')]
        for field, values in [('username', ['', 'bad name', 'x' * 151, 23]), ('display_name', ['', 'x' * 151, None]), ('password', ['', '1234567890', 'short', 'x' * 4097, 25])]:
            for value in values:
                invalid.append({**self.payload(), field: value})
        for data in invalid:
            with self.subTest(keys=list(data) if isinstance(data, dict) else 'array'):
                self.assertEqual(self.client.post(self.url, data, format='json').status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertFalse(AuditLog.objects.exists())

    def test_edit_role_name_and_activation_without_deleting_account(self):
        result = self.client.patch(self.detail(), {'display_name': '新显示名', 'platform_role': 'admin', 'is_active': False}, format='json')
        self.assertEqual(result.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.first_name, '新显示名')
        self.assertTrue(self.member.is_staff)
        self.assertFalse(self.member.is_active)
        self.assertFalse(self.member.is_superuser)
        self.assertEqual(self.client.patch(self.detail(), {'is_active': True}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(self.detail(), {'platform_role': 'member'}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(self.detail()).status_code, 405)

    def test_patch_rejects_unsafe_or_malformed_fields_and_missing_user(self):
        for data in [[], {}, {'username': 'other'}, {'password': self.password}, {'is_active': 'false'}, {'is_active': 1}, {'platform_role': []}, {'display_name': ''}]:
            with self.subTest(data=data):
                self.assertEqual(self.client.patch(self.detail(), data, format='json').status_code, 400)
        self.assertEqual(self.client.patch('/api/users/99999/', {'display_name': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.client.post('/api/users/99999/reset-password/', {'new_password': self.password}, format='json').status_code, 404)
        self.assertFalse(AuditLog.objects.exists())

    def test_last_active_platform_admin_protection_includes_superuser(self):
        self.assertEqual(self.client.patch(self.detail(self.admin), {'is_active': False}, format='json').status_code, 409)
        self.assertEqual(self.client.patch(self.detail(self.admin), {'platform_role': 'member'}, format='json').status_code, 409)
        self.member.is_superuser = True
        self.member.save(update_fields=['is_superuser'])
        self.assertEqual(self.client.patch(self.detail(self.admin), {'platform_role': 'member'}, format='json').status_code, 200)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.patch(self.detail(), {'platform_role': 'member'}, format='json').status_code, 409)
        self.admin.is_staff = True
        self.admin.save(update_fields=['is_staff'])
        self.assertEqual(self.client.patch(self.detail(), {'platform_role': 'member'}, format='json').status_code, 200)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_superuser or self.member.is_staff)

    def test_project_last_admin_disable_requires_enabled_replacement(self):
        project = Project.objects.create(name='A', gitlab_project_path='a')
        ProjectMembership.objects.create(project=project, user=self.member, role='admin')
        self.assertEqual(self.client.patch(self.detail(), {'is_active': False}, format='json').status_code, 409)
        replacement = get_user_model().objects.create_user('replacement', is_active=False)
        ProjectMembership.objects.create(project=project, user=replacement, role='admin')
        self.assertEqual(self.client.patch(self.detail(), {'is_active': False}, format='json').status_code, 409)
        replacement.is_active = True
        replacement.save(update_fields=['is_active'])
        self.assertEqual(self.client.patch(self.detail(), {'is_active': False}, format='json').status_code, 200)
        self.assertEqual(ProjectMembership.objects.filter(project=project).count(), 2)

    def test_reset_validation_and_self_reset_rejection(self):
        for data in [{}, [], {'new_password': 'short'}, {'new_password': self.password, 'is_staff': True}]:
            self.assertEqual(self.client.post(self.detail() + 'reset-password/', data, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.detail(self.admin) + 'reset-password/', {'new_password': self.password}, format='json').status_code, 400)
        self.assertFalse(AuditLog.objects.exists())

    def test_real_session_csrf_reset_invalidation_and_disabled_login(self):
        self.member.set_password(self.password)
        self.member.save()
        target = APIClient(enforce_csrf_checks=True)
        target.force_login(self.member)
        self.assertEqual(target.get('/api/auth/session/').json()['user']['id'], self.member.pk)
        administrator = APIClient(enforce_csrf_checks=True)
        administrator.force_login(self.admin)
        url = self.detail() + 'reset-password/'
        new_password = 'Replacement-test-only-284!'
        self.assertEqual(administrator.post(url, {'new_password': new_password}, format='json').status_code, 403)
        csrf = administrator.get('/api/auth/session/').json()['csrf_token']
        result = administrator.post(url, {'new_password': new_password}, format='json', HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(target.get('/api/auth/session/').json()['user'])
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password(new_password))
        self.assertFalse(self.member.check_password(self.password))
        self.assertNotIn(new_password, str(result.data))
        self.assertNotIn(new_password, str(list(AuditLog.objects.values('metadata'))))
        csrf_target = target.get('/api/auth/session/').json()['csrf_token']
        login = target.post('/api/auth/login/', {'username': 'member', 'password': new_password}, format='json', HTTP_X_CSRFTOKEN=csrf_target)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.client.patch(self.detail(), {'is_active': False}, format='json').status_code, 200)
        self.assertIsNone(target.get('/api/auth/session/').json()['user'])
        csrf_target = target.get('/api/auth/session/').json()['csrf_token']
        self.assertEqual(target.post('/api/auth/login/', {'username': 'member', 'password': new_password}, format='json', HTTP_X_CSRFTOKEN=csrf_target).status_code, 401)

    def test_lock_contention_and_fresh_platform_authority(self):
        with resource_lock('identity-management'):
            self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 409)
            self.assertEqual(self.client.patch(self.detail(), {'display_name': 'x'}, format='json').status_code, 409)
            self.assertEqual(self.client.post(self.detail() + 'reset-password/', {'new_password': self.password}, format='json').status_code, 409)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_staff=False)
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.assertFalse(AuditLog.objects.exists())

    def test_create_admin_and_text_max_lengths(self):
        data = {**self.payload(platform_role='admin'), 'username': 'u' * 150, 'display_name': '名' * 150}
        result = self.client.post(self.url, data, format='json')
        self.assertEqual(result.status_code, 201)
        user = get_user_model().objects.get(pk=result.data['id'])
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_partial_update_preserves_existing_full_name_over_display_field_limit(self):
        self.member.first_name = '名' * 150
        self.member.last_name = '姓' * 150
        self.member.save(update_fields=['first_name', 'last_name'])
        result = self.client.patch(self.detail(), {'is_active': False}, format='json')
        self.assertEqual(result.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.first_name, '名' * 150)
        self.assertEqual(self.member.last_name, '姓' * 150)
