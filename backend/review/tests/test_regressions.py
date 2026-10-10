from unittest.mock import Mock, patch
from django.test import TestCase, TransactionTestCase, override_settings
from django.db import connection
from django.utils import timezone
from datetime import timedelta
from review.tests.auth_helpers import admin_client
from review.models import Project, ProjectPolicy, ModelConfig, NotificationConfig, Review, ReviewJob
from review.integrations.wecom import WeComClient
from review.integrations.gitlab import GitLabClient
from review.services.review_runner import create_review_job, run_review_job


class ReliabilityRegressions(TestCase):
    @patch('review.views.WeComClient.send_review')
    def test_failed_review_cannot_send_a_passed_notification(self, send):
        send.return_value = {'ok': True}
        review = Review.objects.create(project=self.project, mr_iid=7, status=Review.Status.FAILED)
        result = self.client.post(f'/api/reviews/{review.pk}/notify/')
        self.assertEqual(result.status_code, 409)
        send.assert_not_called()

    @patch('review.views.WeComClient.send_review')
    def test_notification_transport_error_does_not_leak_webhook_key(self, send):
        import requests
        from review.models import AuditLog
        send.side_effect = requests.ConnectionError('failed https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=private-webhook-key')
        review = Review.objects.create(project=self.project, mr_iid=7, status=Review.Status.COMPLETED, summary='done')
        result = self.client.post(f'/api/reviews/{review.pk}/notify/')
        self.assertNotIn('private-webhook-key', str(result.json()))
        self.assertNotIn('private-webhook-key', str(list(AuditLog.objects.values_list('metadata', flat=True))))

    def test_overview_separates_blocking_comments_from_blocked_review_rate(self):
        from review.models import LineComment
        review = Review.objects.create(project=self.project, mr_iid=7, status=Review.Status.BLOCKED, duration_ms=2000, completed_at=timezone.now())
        for line in (1, 2, 3):
            LineComment.objects.create(review=review, file_path='a.py', new_line=line, level='P1')
        data = self.client.get('/api/overview/').json()
        self.assertEqual(data.get('blocked_reviews'), 1)
        self.assertEqual(data.get('average_duration_ms'), 2000)
        self.assertEqual(len(data.get('daily_reviews', [])), 14)
        self.assertEqual(data['daily_reviews'][-1], 1)

    def test_merge_attempt_does_not_turn_failed_review_into_completed_gate_candidate(self):
        from review.models import LineComment
        review = Review.objects.create(project=self.project, mr_iid=7, status=Review.Status.FAILED)
        LineComment.objects.create(review=review, file_path='a.py', new_line=1, level='P1')
        result = self.client.post(f'/api/reviews/{review.pk}/merge/')
        self.assertEqual(result.status_code, 409)
        review.refresh_from_db()
        self.assertEqual(review.status, Review.Status.FAILED)

    @patch('review.views.GitLabClient.create_discussion')
    def test_paused_project_blocks_manual_comment_publication(self, publish):
        from review.models import LineComment
        self.project.enabled = False
        self.project.save()
        review = Review.objects.create(project=self.project, mr_iid=7)
        comment = LineComment.objects.create(review=review, file_path='a.py', new_line=1)
        response = self.client.post(f'/api/reviews/{review.pk}/comments/{comment.pk}/publish/')
        self.assertEqual(response.status_code, 409)
        publish.assert_not_called()

    @override_settings(GITLAB_URL='https://gitlab.test', GITLAB_TOKEN='test-only')
    @patch('review.integrations.gitlab.requests.get')
    def test_failed_review_does_not_pass_dry_run(self, get):
        get.return_value.json.return_value = {
            'iid': 7, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
            'has_conflicts': False, 'detailed_merge_status': 'mergeable',
        }
        review = Review.objects.create(project=self.project, mr_iid=7, status=Review.Status.FAILED,
                                       summary='partial', summary_sync_status='synced', head_sha='a' * 40)
        result = self.client.post(f'/api/merge-gate/dry-run/?project={self.project.pk}', {'review_id': str(review.pk)}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()['passed'])
        self.assertFalse(result.json()['checks']['review_ready'])

    def test_model_timeout_can_be_saved_and_is_bounded(self):
        response = self.client.patch('/api/model-config/', {'request_timeout_s': 180}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['request_timeout_s'], 180)
        self.assertEqual(self.client.patch('/api/model-config/', {'request_timeout_s': 0}, format='json').status_code, 400)

    @override_settings(REVIEW_ALLOW_TEST_FALLBACK=False, GITLAB_URL='https://gitlab.example', GITLAB_TOKEN='test')
    @patch('review.integrations.gitlab.requests.get')
    def test_real_gitlab_diff_ignores_supplied_diff(self, get):
        get.return_value.json.return_value = {'changes': [{'old_path': 'old.py', 'new_path': 'new.py', 'diff': '@@ -1 +1 @@\n-old\n+real'}]}
        diff = GitLabClient(self.project).fetch_diff(7, {'diff': 'spoofed'})
        self.assertNotIn('spoofed', diff)
        self.assertIn('diff --git a/old.py b/new.py', diff)

    @override_settings(GITLAB_URL='https://gitlab.example', GITLAB_TOKEN='test')
    @patch('review.integrations.gitlab.requests.get')
    def test_truncated_diff_is_not_reviewed_as_complete(self, get):
        get.return_value.json.return_value = {'overflow': True, 'changes': []}
        with self.assertRaises(ValueError):
            GitLabClient(self.project).fetch_diff(7, {})

    @patch('review.integrations.wecom.requests.post')
    def test_long_chinese_summary_is_bounded_in_utf8_bytes(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {'errcode': 0}
        config = NotificationConfig(webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test')
        review = Review.objects.create(project=self.project, mr_iid=7, summary='中' * 3000, gitlab_url='https://gitlab.example/7')
        WeComClient(config).send_review(review)
        content = post.call_args.kwargs['json']['markdown']['content']
        self.assertLessEqual(len(content.encode('utf-8')), 3500)
        self.assertIn('https://gitlab.example/7', content)

    @patch('review.services.review_runner.GitLabClient.fetch_merge_request')
    def test_running_job_cannot_be_claimed_again(self, fetch):
        job = create_review_job(self.project, {'mr_iid': 7})
        job.status = ReviewJob.Status.RUNNING; job.save()
        run_review_job(job.id)
        fetch.assert_not_called()

    @patch('review.services.review_runner.GitLabClient.fetch_merge_request')
    def test_direct_run_honors_backoff(self, fetch):
        job = create_review_job(self.project, {'mr_iid': 7})
        job.next_attempt_at = timezone.now() + timedelta(minutes=1); job.save()
        run_review_job(job.id)
        fetch.assert_not_called()

    @patch('review.views.GitLabClient.merge')
    def test_client_cannot_claim_dry_run_passed(self, merge):
        ProjectPolicy.objects.filter(project=self.project).update(auto_merge_enabled=True, dry_run_required=True, dry_run_passed=False)
        review = Review.objects.create(project=self.project, mr_iid=7, status=Review.Status.COMPLETED, head_sha='sha')
        response = self.client.post(f'/api/reviews/{review.pk}/merge/', {'dry_run_passed': True}, format='json')
        self.assertEqual(response.status_code, 409)
        merge.assert_not_called()

    @override_settings(GITLAB_URL='https://gitlab.example', GITLAB_TOKEN='secret')
    @patch('review.integrations.gitlab.requests.put')
    def test_merge_pins_commit_and_reads_gitlab_state(self, put):
        put.return_value.json.return_value = {'state': 'merged'}
        review = Review.objects.create(project=self.project, mr_iid=7, head_sha='reviewed-sha')
        self.assertTrue(GitLabClient(self.project).merge(review)['merged'])
        self.assertEqual(put.call_args.kwargs['json']['sha'], 'reviewed-sha')

    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='audit', gitlab_project_path='team/audit', webhook_secret='secret')
        ProjectPolicy.objects.create(project=self.project)
        self.base = f'/api/projects/{self.project.pk}'
        self.event = {'object_kind': 'merge_request', 'project': {'path_with_namespace': 'team/audit'}, 'object_attributes': {'iid': 7, 'action': 'open'}, 'event_uuid': 'same-event'}

    def hook(self, data):
        return self.client.post('/api/webhooks/gitlab/', data, format='json', HTTP_X_GITLAB_TOKEN='secret')

    def test_reading_inherited_config_does_not_create_override(self):
        ModelConfig.objects.create(scope='global', base_url='https://models.example/v1', model='global-model', api_key='global-secret')
        response = self.client.get(self.base + '/model-config/')
        self.assertEqual(response.json()['model'], 'global-model')
        self.assertTrue(response.json()['inherited'])
        self.assertFalse(ModelConfig.objects.filter(project=self.project).exists())
        self.client.get(self.base + '/notification-config/')
        self.assertFalse(NotificationConfig.objects.filter(project=self.project).exists())

    def test_first_override_retains_inherited_secret(self):
        ModelConfig.objects.create(scope='global', base_url='https://models.example/v1', model='global-model', api_key='global-secret')
        response = self.client.patch(self.base + '/model-config/', {'model': 'project-model'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ModelConfig.objects.get(project=self.project).api_key, 'global-secret')

    def test_paused_project_cannot_trigger_manually_or_via_hook(self):
        self.project.enabled = False; self.project.save()
        self.assertEqual(self.client.post(self.base + '/trigger/', {'mr_iid': 7}, format='json').status_code, 409)
        self.assertEqual(self.hook(self.event).status_code, 409)
        self.assertFalse(ReviewJob.objects.exists())

    def test_non_mr_event_is_ignored(self):
        self.event['object_kind'] = 'issue'
        self.assertFalse(self.hook(self.event).json()['accepted'])
        self.assertFalse(ReviewJob.objects.exists())

    def test_bad_iid_is_rejected(self):
        self.event['object_attributes']['iid'] = 'not-a-number'
        self.assertEqual(self.hook(self.event).status_code, 400)
        self.assertFalse(ReviewJob.objects.exists())

    def test_command_inside_sentence_does_not_trigger(self):
        self.event.update(object_kind='note', merge_request={'iid': 7}, object_attributes={'noteable_type': 'MergeRequest', 'note': '请使用 /review 重新检查'})
        self.assertFalse(self.hook(self.event).json()['accepted'])
        self.assertFalse(ReviewJob.objects.exists())

    @override_settings(REVIEW_EXECUTE_INLINE=False)
    def test_duplicate_queued_webhook_does_not_execute_inline(self):
        self.hook(self.event)
        with override_settings(REVIEW_EXECUTE_INLINE=True), patch('review.webhooks.run_review_job') as run:
            response = self.hook(self.event)
        self.assertEqual(response.status_code, 202)
        run.assert_not_called()

    @patch('review.integrations.wecom.requests.post')
    def test_wecom_business_error_is_not_success(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {'errcode': 93000, 'errmsg': 'invalid webhook'}
        config = NotificationConfig(webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret')
        self.assertFalse(WeComClient(config).send_test()['ok'])
        review = Review.objects.create(project=self.project, mr_iid=7)
        self.assertFalse(WeComClient(config).send_review(review)['ok'])

    @patch('review.integrations.wecom.requests.post')
    def test_blocker_only_timing_does_not_send_passed_review(self, post):
        config = NotificationConfig(webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret', notify_suggestions=False, notify_merge_result=True)
        review = Review.objects.create(project=self.project, mr_iid=7)
        self.assertTrue(WeComClient(config).send_review(review)['skipped'])
        post.assert_not_called()


class TransactionBoundaryTests(TransactionTestCase):
    def test_gitlab_io_does_not_hold_database_transaction(self):
        project = Project.objects.create(name='test', gitlab_project_path='test/project')
        job = create_review_job(project, {'mr_iid': 1})
        observed = []
        def fail_fetch(*args):
            observed.append(connection.in_atomic_block)
            raise RuntimeError('offline')
        with patch('review.services.review_runner.GitLabClient.fetch_merge_request', side_effect=fail_fetch):
            run_review_job(job.id)
        self.assertEqual(observed, [False])
