from unittest.mock import Mock, patch

from django.test import TestCase
from review.tests.auth_helpers import admin_client

from review.models import AuditLog, GitLabConfig, LineComment, ModelConfig, NotificationConfig, Project, ProjectPolicy, Review, ReviewJob


class ReviewApiTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(
            name='cw-cr-demo',
            gitlab_project_path='demo/cw-cr-demo',
        )
        ProjectPolicy.objects.create(project=self.project)

    def test_project_list_and_policy_patch(self):
        response = self.client.get('/api/projects/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)

        response = self.client.patch(
            f'/api/projects/{self.project.pk}/policy/',
            {'command_review': False, 'summary_prompt': '请严格使用中文输出。'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['command_review'])
        self.assertEqual(response.json()['summary_prompt'], '请严格使用中文输出。')

    def test_project_reviews_nested_endpoint_lists_and_triggers(self):
        response = self.client.get(f'/api/projects/{self.project.pk}/reviews/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 0)

        response = self.client.post(
            f'/api/projects/{self.project.pk}/reviews/',
            {'mode': 'improve', 'mr_iid': 364, 'title': '项目级入口'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['review']['project'], str(self.project.pk))

    def test_trigger_rejects_missing_mr_iid_instead_of_creating_placeholder_review(self):
        response = self.client.post(
            f'/api/projects/{self.project.pk}/trigger/',
            {'mode': 'review', 'title': '缺少 MR 编号'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Review.objects.exists())

    def test_trigger_review_returns_detail_and_resolve_action(self):
        response = self.client.post(
            f'/api/projects/{self.project.pk}/trigger/',
            {'mode': 'review', 'mr_iid': 357, 'title': '自动接入代码审查平台'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['job_status'], 'succeeded')
        review_id = response.json()['review']['id']
        self.assertEqual(response.json()['review']['status'], Review.Status.BLOCKED)
        comment_id = response.json()['review']['comments'][0]['id']

        response = self.client.post(f'/api/reviews/{review_id}/comments/{comment_id}/resolve/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], LineComment.Status.RESOLVED)

        response = self.client.get(f'/api/reviews/{review_id}/')
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()['comment_count'], 0)

    def test_summary_can_be_edited_and_audited(self):
        response = self.client.post(
            f'/api/projects/{self.project.pk}/trigger/',
            {'mode': 'review', 'mr_iid': 362, 'title': '总评编辑'},
            format='json',
        )
        review_id = response.json()['review']['id']

        response = self.client.patch(
            f'/api/reviews/{review_id}/summary/',
            {'summary': '人工补充的中文总评'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary'], '人工补充的中文总评')

    @patch('review.views.GitLabClient.create_summary_note')
    def test_manual_summary_edit_resyncs_gitlab_note(self, create_summary_note):
        response = self.client.post(
            f'/api/projects/{self.project.pk}/trigger/',
            {'mode': 'review', 'mr_iid': 363, 'title': '重新同步总评'},
            format='json',
        )
        review_id = response.json()['review']['id']
        create_summary_note.reset_mock()

        response = self.client.patch(
            f'/api/reviews/{review_id}/summary/',
            {'summary': '更新后的中文总评'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        create_summary_note.assert_called_once()

    @patch('review.views.WeComClient.send_review')
    def test_review_can_be_sent_to_wecom_and_is_audited(self, send_review):
        send_review.return_value = {'ok': True, 'demo': True, 'message': '已记录本地通知'}
        response = self.client.post(
            f'/api/projects/{self.project.pk}/trigger/',
            {'mode': 'review', 'mr_iid': 367, 'title': '发送通知'},
            format='json',
        )
        review_id = response.json()['review']['id']
        send_review.reset_mock()

        response = self.client.post(f'/api/reviews/{review_id}/notify/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        send_review.assert_called_once()
        self.assertTrue(AuditLog.objects.filter(action='发送企业微信评审通知').exists())

    def test_global_model_config_is_masked(self):
        response = self.client.patch(
            '/api/model-config/',
            {'base_url': 'https://ccswitch.example/v1', 'api_key': 'sk-super-secret', 'model': 'Qwen3-Coder'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['api_key_masked'], 'sk-s••••cret')
        self.assertNotIn('sk-super-secret', str(response.json()))

    @patch('review.integrations.gitlab.requests.get')
    def test_gitlab_config(self, get):
        response = Mock()
        response.json.return_value = {'id': 42, 'username': 'review-bot'}
        get.return_value = response

        response = self.client.patch(
            '/api/gitlab-config/',
            {
                'base_url': 'https://gitlab.example.com',
                'api_token': 'glpat-super-secret',
                'webhook_secret': 'hook-super-secret',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['api_token_masked'], 'glpa••••cret')
        self.assertTrue(response.json()['api_token_configured'])
        self.assertTrue(response.json()['webhook_secret_configured'])
        self.assertNotIn('glpat-super-secret', str(response.json()))

        response = self.client.patch(
            '/api/gitlab-config/',
            {'base_url': 'https://gitlab.example.com'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['api_token_masked'], 'glpa••••cret')

        response = self.client.post('/api/gitlab-config/test/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(response.json()['username'], 'review-bot')
        self.assertNotIn('glpat-super-secret', str(response.json()))
        self.assertTrue(AuditLog.objects.filter(action='测试 GitLab 连接').exists())

    def test_project_model_and_notification_overrides_are_scoped(self):
        response = self.client.patch(
            f'/api/projects/{self.project.pk}/model-config/',
            {'base_url': 'https://ccswitch.internal/v1', 'api_key': 'sk-project-secret', 'model': 'DeepSeek-V3'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['scope'], ModelConfig.Scope.PROJECT)
        self.assertEqual(response.json()['api_key_masked'], 'sk-p••••cret')
        self.assertFalse(Project.objects.get(pk=self.project.pk).model_inherit)

        response = self.client.patch(
            f'/api/projects/{self.project.pk}/notification-config/',
            {'name': '项目专属研发群', 'webhook_url': 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=demo'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['scope'], NotificationConfig.Scope.PROJECT)
        self.assertTrue(response.json()['webhook_configured'])
        self.assertFalse(Project.objects.get(pk=self.project.pk).notification_inherit)

    def test_notification_test_endpoint_returns_safe_result(self):
        response = self.client.post('/api/notification-config/test/', format='json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])

    def test_model_test_endpoint_returns_safe_result(self):
        response = self.client.post('/api/model-config/test/', format='json')

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['ok'])
        self.assertIn('模型网关', response.json()['error'])

    @patch('review.integrations.llm.requests.get')
    def test_model_test_endpoint_accepts_unsaved_form_values_and_returns_catalog(self, get):
        response = Mock()
        response.json.return_value = {'data': [{'id': 'team-review-model'}]}
        get.return_value = response

        result = self.client.post(
            '/api/model-config/test/',
            {
                'base_url': 'https://ccswitch.internal/v1',
                'api_key': 'sk-transient-test',
            },
            format='json',
        )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['model_ids'], ['team-review-model'])
        self.assertFalse(ModelConfig.objects.filter(base_url='https://ccswitch.internal/v1').exists())

    def test_global_config_changes_are_audited(self):
        self.client.patch('/api/model-config/', {'model': 'DeepSeek-V3'}, format='json')
        self.client.patch('/api/notification-config/', {'name': '审查通知群'}, format='json')

        actions = set(AuditLog.objects.values_list('action', flat=True))
        self.assertIn('更新模型配置', actions)
        self.assertIn('更新企业微信配置', actions)

    def test_system_endpoint_reports_queue_and_audit_counts(self):
        response = self.client.get('/api/system/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('queue', response.json())
        self.assertIn('audit_logs', response.json())

    def test_system_retry_requeues_failed_jobs_for_worker(self):
        review = Review.objects.create(project=self.project, mr_iid=368, title='失败重试', status=Review.Status.FAILED)
        job = ReviewJob.objects.create(
            project=self.project,
            review=review,
            kind=ReviewJob.Kind.REVIEW,
            status=ReviewJob.Status.FAILED,
            idempotency_key='failed-job-368',
            payload={'mr_iid': 368, 'title': '失败重试'},
            attempts=3,
            error_message='模型超时',
        )

        response = self.client.post('/api/system/retry/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.QUEUED)
        self.assertEqual(job.review_id, review.pk)
        self.assertEqual(job.attempts, 0)

    def test_merge_endpoint_requires_gate_and_policy(self):
        response = self.client.post(
            f'/api/projects/{self.project.pk}/trigger/',
            {'mode': 'review', 'mr_iid': 359, 'title': '门禁测试'},
            format='json',
        )
        review_id = response.json()['review']['id']

        response = self.client.post(f'/api/reviews/{review_id}/merge/', format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['status'], 'blocked')

        review = Review.objects.get(pk=review_id)
        review.comments.filter(level__in=[LineComment.Level.P0, LineComment.Level.P1]).update(status=LineComment.Status.RESOLVED)
        response = self.client.patch(
            f'/api/projects/{self.project.pk}/policy/',
            {'auto_merge_enabled': True, 'dry_run_required': False},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        policy = ProjectPolicy.objects.get(project=self.project)
        self.assertFalse(policy.auto_merge_enabled)
        self.assertTrue(policy.dry_run_required)
        response = self.client.post(f'/api/reviews/{review_id}/merge/', format='json')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()['merged'])

    def test_gate_normalizes_boolean_for_persistent_project_switch(self):
        for value in (True, 'true', 'True', 1):
            with self.subTest(value=value):
                response = self.client.patch(
                    f'/api/merge-gate/?project={self.project.pk}',
                    {'auto_merge_enabled': value}, format='json',
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['enabled'])
                policy = ProjectPolicy.objects.get(project=self.project)
                self.assertTrue(policy.auto_merge_enabled)
                self.assertFalse(policy.dry_run_passed)

        for value in (False, 'false', 'False', 0):
            with self.subTest(value=value):
                response = self.client.patch(
                    f'/api/merge-gate/?project={self.project.pk}',
                    {'auto_merge_enabled': value}, format='json',
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()['enabled'])
                self.assertFalse(ProjectPolicy.objects.get(project=self.project).auto_merge_enabled)

    def test_gate_rejects_cross_scope_and_forged_fields_atomically(self):
        for field, value in (
            ('dry_run_required', False), ('dry_run_passed', True),
            ('dry_run_reason', 'forged'), ('dry_run_completed_at', '2026-09-23T00:00:00Z'),
            ('block_on_p1', False), ('summary_prompt', 'wrong endpoint'), ('unknown', True),
        ):
            with self.subTest(field=field):
                before = ProjectPolicy.objects.filter(project=self.project).values().get()
                response = self.client.patch(
                    f'/api/merge-gate/?project={self.project.pk}',
                    {field: value, 'auto_merge_enabled': False}, format='json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
                self.assertEqual(ProjectPolicy.objects.filter(project=self.project).values().get(), before)

    def test_gate_disable_does_not_require_passing_dry_run(self):
        ProjectPolicy.objects.filter(project=self.project).update(auto_merge_enabled=True)
        response = self.client.patch(
            f'/api/merge-gate/?project={self.project.pk}',
            {'auto_merge_enabled': False}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['enabled'])

    def test_legacy_dry_run_setting_does_not_control_project_activation(self):
        ProjectPolicy.objects.filter(project=self.project).update(dry_run_required=False)
        response = self.client.patch(
            f'/api/merge-gate/?project={self.project.pk}',
            {'auto_merge_enabled': True}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ProjectPolicy.objects.get(project=self.project).auto_merge_enabled)
        self.assertTrue(response.json()['automatic_validation_required'])

    @patch('review.integrations.gitlab.requests.get')
    def test_optional_dry_run_is_persisted_independently_of_project_switch(self, get):
        response = self.client.post(
            f'/api/projects/{self.project.pk}/trigger/',
            {'mode': 'review', 'mr_iid': 365, 'title': 'dry-run 持久化'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        review_id = response.json()['review']['id']
        Review.objects.filter(pk=review_id).update(head_sha='a' * 40, summary_sync_status='synced')
        GitLabConfig.objects.create(base_url='https://gitlab.test', api_token='test-only')
        get.return_value = Mock(json=lambda: {
            'iid': 365, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
            'has_conflicts': False, 'detailed_merge_status': 'mergeable',
        })

        response = self.client.post(f'/api/merge-gate/dry-run/?project={self.project.pk}', {'review_id': review_id}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['passed'])

        response = self.client.patch(
            f'/api/merge-gate/?project={self.project.pk}',
            {'auto_merge_enabled': True},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['enabled'])
        self.assertFalse(ProjectPolicy.objects.get(project=self.project).dry_run_passed)

        Review.objects.get(pk=review_id).comments.filter(level__in=[LineComment.Level.P0, LineComment.Level.P1]).update(status=LineComment.Status.RESOLVED)
        response = self.client.post(f'/api/merge-gate/dry-run/?project={self.project.pk}', {'review_id': review_id}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['passed'])
        self.assertTrue(ProjectPolicy.objects.get(project=self.project).auto_merge_enabled)

        response = self.client.patch(
            f'/api/merge-gate/?project={self.project.pk}',
            {'auto_merge_enabled': True},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['enabled'])
