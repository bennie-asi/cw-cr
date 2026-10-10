from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from review.models import AuditLog, Project, ProjectMembership
from review.services.execution_locks import resource_lock


class MemberManagementTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('platform', is_staff=True)
        self.manager = get_user_model().objects.create_user('manager')
        self.reader = get_user_model().objects.create_user('reader')
        self.target = get_user_model().objects.create_user('target', first_name='测试成员')
        self.project = Project.objects.create(name='A', gitlab_project_path='team/a')
        self.other = Project.objects.create(name='B', gitlab_project_path='team/b')
        self.manager_link = ProjectMembership.objects.create(project=self.project, user=self.manager, role='admin')
        self.reader_link = ProjectMembership.objects.create(project=self.project, user=self.reader)
        self.foreign_link = ProjectMembership.objects.create(project=self.other, user=self.target, role='admin')
        self.url = f'/api/projects/{self.project.pk}/members/'
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        sentinel = patch('requests.sessions.Session.request', side_effect=AssertionError('No network in identity tests'))
        sentinel.start()
        self.addCleanup(sentinel.stop)

    def detail(self, member=None):
        return f'{self.url}{(member or self.reader_link).pk}/'

    def test_reads_are_project_scoped_and_safe(self):
        self.client.force_authenticate(self.reader)
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200)
        self.assertEqual({row['user_id'] for row in result.data}, {self.reader.pk, self.manager.pk})
        self.assertEqual(set(result.data[0]), {'id', 'user_id', 'username', 'display_name', 'role', 'is_active', 'platform_role'})
        self.assertEqual(self.client.get(f'/api/projects/{self.other.pk}/members/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_members_cannot_mutate_and_foreign_member_ids_are_hidden(self):
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.post(self.url, {'username': 'target'}, format='json').status_code, 403)
        self.assertEqual(self.client.patch(self.detail(), {'role': 'admin'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(self.detail()).status_code, 403)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.patch(self.detail(self.foreign_link), {'role': 'member'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(self.detail(self.foreign_link)).status_code, 404)
        self.assertFalse(AuditLog.objects.exists())

    def test_add_default_role_audit_and_duplicate(self):
        result = self.client.post(self.url, {'username': 'target'}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.data['role'], 'member')
        self.assertEqual(result.data['display_name'], '测试成员')
        self.assertFalse(self.target.is_staff)
        self.assertEqual(ProjectMembership.objects.get(pk=result.data['id']).project, self.project)
        audit = AuditLog.objects.get()
        self.assertEqual((audit.project_id, audit.actor_user_id), (self.project.pk, self.manager.pk))
        self.assertEqual(self.client.post(self.url, {'username': 'target'}, format='json').status_code, 400)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_payload_validation_and_inactive_or_missing_targets(self):
        invalid = [[], {}, {'username': 42}, {'username': ''}, {'username': ' '}, {'username': 'x' * 151},
                   {'username': 'missing'}, {'username': 'target', 'role': 'owner'},
                   {'username': 'target', 'role': []}, {'username': 'target', 'is_staff': True}]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 400)
        self.target.is_active = False
        self.target.save(update_fields=['is_active'])
        self.assertEqual(self.client.post(self.url, {'username': 'target'}, format='json').status_code, 400)
        for payload in [[], {}, {'role': None}, {'role': 'owner'}, {'role': 'admin', 'user_id': self.admin.pk}]:
            with self.subTest(patch=payload):
                self.assertEqual(self.client.patch(self.detail(), payload, format='json').status_code, 400)
        self.assertEqual(self.client.delete(self.detail(), {'role': 'admin'}, format='json').status_code, 400)
        self.assertFalse(AuditLog.objects.exists())

    def test_last_enabled_project_admin_cannot_be_removed_or_demoted(self):
        for actor in [self.manager, self.admin]:
            self.client.force_authenticate(actor)
            self.assertEqual(self.client.delete(self.detail(self.manager_link)).status_code, 409)
            self.assertEqual(self.client.patch(self.detail(self.manager_link), {'role': 'member'}, format='json').status_code, 409)
        self.manager_link.refresh_from_db()
        self.assertEqual(self.manager_link.role, 'admin')
        self.assertFalse(AuditLog.objects.exists())
        self.reader.is_active = False
        self.reader.save(update_fields=['is_active'])
        self.reader_link.role = 'admin'
        self.reader_link.save(update_fields=['role'])
        self.assertEqual(self.client.delete(self.detail(self.manager_link)).status_code, 409)
        self.assertEqual(self.client.delete(self.detail(self.reader_link)).status_code, 204)

    def test_admin_can_recover_empty_project_and_add_admin_then_demote_and_remove(self):
        self.client.force_authenticate(self.admin)
        empty = Project.objects.create(name='empty', gitlab_project_path='team/empty')
        result = self.client.post(f'/api/projects/{empty.pk}/members/', {'username': 'platform', 'role': 'admin'}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.data['platform_role'], 'admin')
        result = self.client.post(self.url, {'username': 'target', 'role': 'admin'}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertEqual(self.client.patch(self.detail(self.manager_link), {'role': 'admin'}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(self.detail(self.manager_link), {'role': 'member'}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(self.detail(self.manager_link)).status_code, 204)
        self.assertTrue(get_user_model().objects.filter(pk=self.manager.pk).exists())
        self.assertTrue(ProjectMembership.objects.filter(pk=self.foreign_link.pk).exists())
        self.assertEqual(AuditLog.objects.filter(project=self.project, actor_user=self.admin).count(), 4)

    def test_manager_can_change_and_remove_another_member_without_platform_promotion(self):
        response = self.client.patch(self.detail(), {'role': 'admin'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.reader.refresh_from_db()
        self.assertFalse(self.reader.is_staff)
        self.assertEqual(self.client.delete(self.detail()).status_code, 204)
        self.assertEqual(AuditLog.objects.filter(project=self.project, actor_user=self.manager).count(), 2)

    def test_identity_lock_contention_does_not_write(self):
        with resource_lock('identity-management'):
            self.assertEqual(self.client.post(self.url, {'username': 'target'}, format='json').status_code, 409)
            self.assertEqual(self.client.patch(self.detail(), {'role': 'admin'}, format='json').status_code, 409)
            self.assertEqual(self.client.delete(self.detail()).status_code, 409)
        self.assertFalse(AuditLog.objects.exists())

    def test_fresh_actor_state_checked_inside_write(self):
        # force_authenticate deliberately retains the pre-change user object.
        get_user_model().objects.filter(pk=self.manager.pk).update(is_active=False)
        self.assertEqual(self.client.post(self.url, {'username': 'target'}, format='json').status_code, 403)
        get_user_model().objects.filter(pk=self.manager.pk).update(is_active=True)
        self.client.force_authenticate(self.admin)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_staff=False)
        self.assertEqual(self.client.post(self.url, {'username': 'target'}, format='json').status_code, 403)
        self.assertFalse(AuditLog.objects.exists())

    def test_username_max_length_is_accepted(self):
        get_user_model().objects.create_user('x' * 150)
        self.assertEqual(self.client.post(self.url, {'username': 'x' * 150}, format='json').status_code, 201)
