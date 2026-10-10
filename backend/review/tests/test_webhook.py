from copy import deepcopy

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from review.models import AuditLog, GitLabConfig, Project, ProjectPolicy, Review, ReviewJob


class GitLabWebhookTests(TestCase):
    @override_settings(REVIEW_EXECUTE_INLINE=False)
    def test_event_identifier_is_scoped_to_project(self):
        other = Project.objects.create(name='other', gitlab_project_path='demo/other', webhook_secret='other-secret')
        second_payload = deepcopy(self.payload)
        second_payload['project']['path_with_namespace'] = other.gitlab_project_path
        first = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='hook-secret')
        second = self.client.post('/api/webhooks/gitlab/', second_payload, format='json', HTTP_X_GITLAB_TOKEN='other-secret')
        self.assertNotEqual(first.json()['job_id'], second.json()['job_id'])
        self.assertEqual(ReviewJob.objects.get(pk=second.json()['job_id']).project_id, other.pk)

    def setUp(self):
        self.client = APIClient()
        self.project = Project.objects.create(
            name='cw-cr-demo',
            gitlab_project_path='demo/cw-cr-demo',
            webhook_secret='hook-secret',
        )
        ProjectPolicy.objects.create(project=self.project)
        self.payload = {
            'event_uuid': 'gitlab-event-357',
            'object_kind': 'merge_request',
            'project': {'path_with_namespace': 'demo/cw-cr-demo'},
            'object_attributes': {
                'iid': 357,
                'title': '自动接入代码审查平台',
                'source_branch': 'feature/auto-review',
                'action': 'open',
            },
        }

    def test_webhook_is_idempotent_and_creates_review_job(self):
        first = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='hook-secret')
        second = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='hook-secret')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['job_id'], second.json()['job_id'])
        self.assertEqual(first.json()['status'], ReviewJob.Status.SUCCEEDED)
        self.assertEqual(ReviewJob.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='Webhook 触发评审').count(), 1)
        self.assertEqual(AuditLog.objects.get(action='Webhook 触发评审').project_id, self.project.pk)

    def test_webhook_rejects_invalid_token(self):
        response = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='wrong')

        self.assertEqual(response.status_code, 403)

    def test_note_webhook_triggers_improve_command(self):
        payload = {
            'event_uuid': 'gitlab-note-357',
            'object_kind': 'note',
            'project': {'path_with_namespace': 'demo/cw-cr-demo'},
            'object_attributes': {
                'note': '/improve 请重点看异常处理',
                'noteable_type': 'MergeRequest',
            },
            'merge_request': {
                'iid': 357,
                'title': '自动接入代码审查平台',
                'source_branch': 'feature/auto-review',
            },
        }

        response = self.client.post('/api/webhooks/gitlab/', payload, format='json', HTTP_X_GITLAB_TOKEN='hook-secret')

        self.assertEqual(response.status_code, 201)
        job = ReviewJob.objects.get(id=response.json()['job_id'])
        self.assertEqual(job.kind, ReviewJob.Kind.IMPROVE)
        self.assertEqual(job.review.source, Review.Source.IMPROVE)

    def test_merge_request_webhook_respects_automatic_trigger_policy(self):
        ProjectPolicy.objects.filter(project=self.project).update(webhook_trigger=False)

        response = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='hook-secret')

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()['accepted'])
        self.assertEqual(ReviewJob.objects.count(), 0)

    @override_settings(GITLAB_WEBHOOK_SECRET='global-hook-secret')
    def test_global_webhook_secret_is_used_when_project_secret_is_empty(self):
        self.project.webhook_secret = ''
        self.project.save(update_fields=('webhook_secret', 'updated_at'))

        rejected = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='wrong')
        self.assertEqual(rejected.status_code, 403)

        response = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='global-hook-secret')

        self.assertEqual(response.status_code, 201)

    def test_page_configured_webhook_secret_is_used_when_project_secret_is_empty(self):
        self.project.webhook_secret = ''
        self.project.save(update_fields=('webhook_secret', 'updated_at'))
        GitLabConfig.objects.create(webhook_secret='page-hook-secret')

        rejected = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='wrong')
        self.assertEqual(rejected.status_code, 403)

        response = self.client.post('/api/webhooks/gitlab/', self.payload, format='json', HTTP_X_GITLAB_TOKEN='page-hook-secret')

        self.assertEqual(response.status_code, 201)

    def test_pushes_without_event_header_are_not_collapsed_into_one_job(self):
        first_payload = deepcopy(self.payload)
        first_payload.pop('event_uuid')
        first_payload['object_attributes']['last_commit'] = {'id': 'commit-one'}
        second_payload = deepcopy(first_payload)
        second_payload['object_attributes']['last_commit'] = {'id': 'commit-two'}

        first = self.client.post('/api/webhooks/gitlab/', first_payload, format='json', HTTP_X_GITLAB_TOKEN='hook-secret')
        second = self.client.post('/api/webhooks/gitlab/', second_payload, format='json', HTTP_X_GITLAB_TOKEN='hook-secret')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(ReviewJob.objects.count(), 2)
