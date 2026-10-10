from contextlib import contextmanager
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta

from review.integrations.errors import IntegrationNotConfiguredError
from review.integrations.wecom import WeComClient
from review.models import GitLabConfig, NotificationConfig, Project, ProjectPolicy, Review, ReviewJob
from review.services.auto_merge import _persist_merge, execute_auto_merge, reconcile_pending_merges, send_merge_result
from review.services.mr_issues import prompt_fingerprint
from review.services.review_prompts import build_review_prompt
from review.tests.auth_helpers import admin_client


@override_settings(GITLAB_URL='', GITLAB_TOKEN='', REVIEW_ALLOW_TEST_FALLBACK=False)
class AutoMergeServiceTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name='auto-merge-demo',
            gitlab_project_path='team/auto-merge-demo',
            gitlab_url='https://gitlab.example',
        )
        self.policy = ProjectPolicy.objects.create(
            project=self.project,
            auto_merge_enabled=True,
            dry_run_required=False,
        )
        self.review = Review.objects.create(
            project=self.project,
            mr_iid=357,
            status=Review.Status.COMPLETED,
            summary='中文总评',
            summary_sync_status='synced',
            head_sha='a' * 40,
        )
        self.job = ReviewJob.objects.create(
            project=self.project,
            review=self.review,
            kind=ReviewJob.Kind.REVIEW,
            status=ReviewJob.Status.SUCCEEDED,
            idempotency_key='auto-merge-job',
            analysis_result={'_issue_scope_fingerprint': prompt_fingerprint(build_review_prompt(self.policy, 'review'))},
        )
        GitLabConfig.objects.create(base_url='https://gitlab.example', api_token='gitlab-token')
        self.remote_mr = {
            'iid': 357,
            'sha': 'a' * 40,
            'state': 'opened',
            'draft': False,
            'work_in_progress': False,
            'has_conflicts': False,
            'detailed_merge_status': 'mergeable',
            'web_url': 'https://gitlab.example/team/auto-merge-demo/-/merge_requests/357',
        }

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_disabled_or_improve_job_skips_without_remote_merge(self, fetch, merge):
        self.policy.auto_merge_enabled = False
        self.policy.save(update_fields=('auto_merge_enabled', 'updated_at'))

        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'skipped')
        fetch.assert_not_called()
        merge.assert_not_called()

        self.policy.auto_merge_enabled = True
        self.policy.save(update_fields=('auto_merge_enabled', 'updated_at'))
        self.job.kind = ReviewJob.Kind.IMPROVE
        self.job.save(update_fields=('kind', 'updated_at'))
        result = execute_auto_merge(self.job, self.review, self.policy)
        self.assertEqual(result['status'], 'skipped')
        fetch.assert_not_called()
        merge.assert_not_called()

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_failed_queued_or_paused_review_is_blocked_without_merge(self, fetch, merge):
        for status in (Review.Status.FAILED, Review.Status.QUEUED):
            self.review.status = status
            self.review.save(update_fields=('status', 'updated_at'))
            result = execute_auto_merge(self.job, self.review, self.policy)
            self.assertEqual(result['status'], 'blocked')
        self.project.enabled = False
        self.project.save(update_fields=('enabled', 'updated_at'))
        self.review.status = Review.Status.COMPLETED
        self.review.save(update_fields=('status', 'updated_at'))
        result = execute_auto_merge(self.job, self.review, self.policy)
        self.assertEqual(result['status'], 'blocked')
        fetch.assert_not_called()
        merge.assert_not_called()

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_live_sha_mismatch_blocks_and_persists_assessment(self, fetch, merge):
        fetch.return_value = {**self.remote_mr, 'sha': 'b' * 40}

        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'blocked')
        self.assertIn('SHA', result['reason'])
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'blocked')
        fetch.assert_called_once()
        merge.assert_not_called()

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.store_assessment')
    @patch('review.services.auto_merge.evaluate_live')
    def test_gate_disabled_during_live_validation_blocks_before_put(self, evaluate, store, merge):
        evaluate.return_value = {
            'project_id': str(self.project.pk), 'review_id': str(self.review.pk),
            'mr_iid': self.review.mr_iid, 'reviewed_sha': self.review.head_sha,
            'current_sha': self.review.head_sha, 'passed': True, 'reason': '门禁检查通过',
        }

        def disable_after_assessment(policy, review, result):
            ProjectPolicy.objects.filter(pk=policy.pk).update(auto_merge_enabled=False)
            return result

        store.side_effect = disable_after_assessment
        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'blocked')
        self.assertIn('已关闭', result['reason'])
        merge.assert_not_called()

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.store_assessment')
    @patch('review.services.auto_merge.evaluate_live')
    def test_project_paused_during_live_validation_blocks_before_put(self, evaluate, store, merge):
        evaluate.return_value = {
            'project_id': str(self.project.pk), 'review_id': str(self.review.pk),
            'mr_iid': self.review.mr_iid, 'reviewed_sha': self.review.head_sha,
            'current_sha': self.review.head_sha, 'passed': True, 'reason': '门禁检查通过',
        }

        def pause_after_assessment(policy, review, result):
            Project.objects.filter(pk=self.project.pk).update(enabled=False)
            return result

        store.side_effect = pause_after_assessment
        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'blocked')
        self.assertIn('暂停', result['reason'])
        merge.assert_not_called()

    @patch('review.services.auto_merge.send_merge_result')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_queue_reconciles_pending_result_to_merged_without_put(self, fetch, notify):
        self.job.merge_status = 'pending'
        self.job.merge_notification_state = 'done'
        self.job.merge_next_check_at = timezone.now() - timedelta(seconds=1)
        self.job.merge_result = {'state': 'opened'}
        self.job.save(update_fields=('merge_status', 'merge_notification_state', 'merge_next_check_at', 'merge_result', 'updated_at'))
        fetch.return_value = {**self.remote_mr, 'state': 'merged'}

        count = reconcile_pending_merges()

        self.assertEqual(count, 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'merged')
        self.assertIsNone(self.job.merge_next_check_at)
        notify.assert_called_once()

    @patch('review.services.auto_merge.execute_auto_merge', side_effect=RuntimeError('reconcile failed'))
    def test_queue_reconcile_failure_is_isolated_from_worker(self, _execute):
        self.job.merge_status = 'pending'
        self.job.merge_next_check_at = timezone.now() - timedelta(seconds=1)
        self.job.save(update_fields=('merge_status', 'merge_next_check_at', 'updated_at'))

        self.assertEqual(reconcile_pending_merges(), 1)

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_queue_reconciles_paused_project_to_terminal_blocked_without_put(self, fetch, merge):
        self.project.enabled = False
        self.project.save(update_fields=('enabled', 'updated_at'))
        self.job.merge_status = 'pending'
        self.job.merge_next_check_at = timezone.now() - timedelta(seconds=1)
        self.job.save(update_fields=('merge_status', 'merge_next_check_at', 'updated_at'))
        fetch.return_value = self.remote_mr

        self.assertEqual(reconcile_pending_merges(), 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'blocked')
        self.assertIsNone(self.job.merge_next_check_at)
        merge.assert_not_called()

    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request', side_effect=RuntimeError('gitlab unavailable'))
    def test_queue_reconcile_paused_fetch_error_becomes_terminal_blocked(self, fetch):
        self.project.enabled = False
        self.project.save(update_fields=('enabled', 'updated_at'))
        self.job.merge_status = 'pending'
        self.job.merge_next_check_at = timezone.now() - timedelta(seconds=1)
        self.job.save(update_fields=('merge_status', 'merge_next_check_at', 'updated_at'))

        self.assertEqual(reconcile_pending_merges(), 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'blocked')
        self.assertIn('人工核对', self.job.merge_error)
        fetch.assert_called_once()

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_queue_reconciles_disabled_gate_to_merged_without_put(self, fetch, merge):
        self.policy.auto_merge_enabled = False
        self.policy.save(update_fields=('auto_merge_enabled', 'updated_at'))
        self.job.merge_status = 'unknown'
        self.job.merge_next_check_at = timezone.now() - timedelta(seconds=1)
        self.job.save(update_fields=('merge_status', 'merge_next_check_at', 'updated_at'))
        fetch.return_value = {**self.remote_mr, 'state': 'merged'}

        self.assertEqual(reconcile_pending_merges(), 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'merged')
        self.assertIsNone(self.job.merge_next_check_at)
        merge.assert_not_called()

    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_queue_reconcile_disabled_gate_host_mismatch_becomes_terminal_blocked(self, fetch):
        self.policy.auto_merge_enabled = False
        self.policy.save(update_fields=('auto_merge_enabled', 'updated_at'))
        self.project.gitlab_url = 'https://another-gitlab.example'
        self.project.save(update_fields=('gitlab_url', 'updated_at'))
        self.job.merge_status = 'unknown'
        self.job.merge_next_check_at = timezone.now() - timedelta(seconds=1)
        self.job.save(update_fields=('merge_status', 'merge_next_check_at', 'updated_at'))

        self.assertEqual(reconcile_pending_merges(), 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'blocked')
        self.assertIn('人工核对', self.job.merge_error)
        fetch.assert_not_called()

    @patch('review.services.auto_merge.send_merge_result')
    @patch('review.services.auto_merge.resource_lock')
    def test_queue_reconcile_rechecks_status_after_disabled_lock(self, lock, notify):
        @contextmanager
        def mutate_lock(_key):
            ReviewJob.objects.filter(pk=self.job.pk).update(
                merge_status='merged', merge_result={'state': 'merged'},
            )
            yield

        lock.side_effect = mutate_lock
        self.project.enabled = False
        self.project.save(update_fields=('enabled', 'updated_at'))
        self.job.merge_status = 'pending'
        self.job.merge_next_check_at = timezone.now() - timedelta(seconds=1)
        self.job.save(update_fields=('merge_status', 'merge_next_check_at', 'updated_at'))

        self.assertEqual(reconcile_pending_merges(), 1)
        notify.assert_called_once()

    @patch('review.services.auto_merge.ProjectPolicy.objects.filter')
    def test_queue_skips_orphaned_policy_without_crashing(self, policy_filter):
        self.job.merge_status = 'pending'
        self.job.merge_next_check_at = timezone.now() - timedelta(seconds=1)
        self.job.save(update_fields=('merge_status', 'merge_next_check_at', 'updated_at'))
        policy_filter.return_value.first.return_value = None

        self.assertEqual(reconcile_pending_merges(), 0)

    def test_project_policy_mismatch_blocks_without_fetch(self):
        other = Project.objects.create(name='other-auto-merge', gitlab_project_path='team/other-auto-merge')
        other_policy = ProjectPolicy.objects.create(project=other, auto_merge_enabled=True)

        with patch('review.services.auto_merge.GitLabClient.fetch_merge_request') as fetch:
            result = execute_auto_merge(self.job, self.review, other_policy)

        self.assertEqual(result['status'], 'blocked')
        fetch.assert_not_called()

    def test_first_pending_persist_does_not_increment_reconcile_attempts(self):
        self.job.refresh_from_db()

        _persist_merge(self.job, 'pending')

        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_reconcile_attempts, 0)
        self.assertIsNotNone(self.job.merge_next_check_at)

    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_already_merged_short_circuits_without_gitlab(self, fetch):
        self.job.merge_status = 'merged'
        self.job.merge_result = {'state': 'merged'}
        self.job.save(update_fields=('merge_status', 'merge_result', 'updated_at'))

        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'merged')
        fetch.assert_not_called()

    @patch('review.services.auto_merge.GitLabClient')
    def test_gitlab_client_constructor_error_is_persisted(self, client):
        client.side_effect = ValueError('project GitLab host mismatch')

        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(ReviewJob.objects.get(pk=self.job.pk).merge_status, 'blocked')

    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    @patch('review.services.auto_merge.resource_lock')
    def test_merge_status_rechecked_after_lock_is_acquired(self, lock, fetch):
        @contextmanager
        def mutate_lock(_key):
            ReviewJob.objects.filter(pk=self.job.pk).update(
                merge_status='merged', merge_result={'state': 'merged'},
            )
            yield

        lock.side_effect = mutate_lock
        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'merged')
        fetch.assert_not_called()

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_merged_success_is_persisted_once(self, fetch, merge):
        fetch.return_value = self.remote_mr
        merge.return_value = {'state': 'merged', 'merged': True, 'merged_by': {'username': 'bot'}}

        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'merged')
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'merged')
        self.assertEqual(self.job.merge_attempts, 1)
        self.assertEqual(self.job.merge_result['state'], 'merged')
        merge.assert_called_once()

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_accepted_not_merged_is_reconciled_without_duplicate_put(self, fetch, merge):
        fetch.side_effect = [self.remote_mr, {**self.remote_mr, 'state': 'opened'}]
        merge.return_value = {'state': 'opened', 'merged': False, 'merge_when_pipeline_succeeds': True}

        first = execute_auto_merge(self.job, self.review, self.policy)
        second = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(first['status'], 'pending')
        self.assertEqual(second['status'], 'unknown')
        self.assertEqual(merge.call_count, 1)
        self.assertEqual(self.job.__class__.objects.get(pk=self.job.pk).merge_attempts, 1)

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_transport_error_reconciles_merged_without_retry(self, fetch, merge):
        fetch.side_effect = [self.remote_mr, {**self.remote_mr, 'state': 'merged'}]
        merge.side_effect = TimeoutError('GitLab timeout')

        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'merged')
        self.assertEqual(merge.call_count, 1)
        self.assertEqual(self.job.__class__.objects.get(pk=self.job.pk).merge_status, 'merged')

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.GitLabClient.fetch_merge_request')
    def test_transport_error_open_is_unknown_and_not_retried(self, fetch, merge):
        fetch.side_effect = [self.remote_mr, {**self.remote_mr, 'state': 'opened'}]
        merge.side_effect = TimeoutError('GitLab timeout')

        result = execute_auto_merge(self.job, self.review, self.policy)
        second = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(second['status'], 'unknown')
        self.assertEqual(merge.call_count, 1)

    @patch('review.services.auto_merge.GitLabClient.merge')
    @patch('review.services.auto_merge.resource_lock')
    def test_busy_merge_lock_does_not_call_gitlab(self, lock, merge):
        from review.services.execution_locks import ResourceBusy

        lock.side_effect = ResourceBusy('busy')
        result = execute_auto_merge(self.job, self.review, self.policy)

        self.assertEqual(result['status'], 'busy')
        merge.assert_not_called()


class MergeNotificationTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name='notify-merge',
            gitlab_project_path='team/notify-merge',
            notification_inherit=False,
        )
        self.review = Review.objects.create(project=self.project, mr_iid=357, summary='中文总评')
        self.job = ReviewJob.objects.create(
            project=self.project,
            review=self.review,
            idempotency_key='merge-notification-job',
            merge_status='merged',
            merge_result={'status': 'merged', 'state': 'merged'},
        )
        self.config = NotificationConfig.objects.create(
            project=self.project,
            scope=NotificationConfig.Scope.PROJECT,
            webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test',
            notify_merge_result=True,
        )

    @patch('review.integrations.wecom.requests.post')
    def test_wecom_errcode_zero_is_done_and_idempotent(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 0}
        post.return_value = response

        first = send_merge_result(self.job, self.review)
        second = send_merge_result(self.job, self.review)

        self.assertEqual(first['status'], 'done')
        self.assertEqual(second['status'], 'done')
        self.assertEqual(post.call_count, 1)

    @patch('review.services.auto_merge.resource_lock')
    @patch('review.integrations.wecom.requests.post')
    def test_worker_notification_uses_shared_review_lock(self, post, lock):
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 0}
        post.return_value = response

        result = send_merge_result(self.job, self.review)

        self.assertEqual(result['status'], 'done')
        lock.assert_called_once_with(f'merge-notification:{self.review.pk}')

    @patch('review.integrations.wecom.requests.post')
    def test_notification_sending_is_marked_unknown_without_resend(self, post):
        self.job.merge_notification_state = 'sending'
        self.job.save(update_fields=('merge_notification_state', 'updated_at'))

        result = send_merge_result(self.job, self.review)

        self.assertEqual(result['status'], 'unknown')
        post.assert_not_called()

    @patch('review.services.auto_merge.WeComClient.send_merge_result')
    def test_notification_does_not_mark_old_state_done_when_merge_changes_during_send(self, send):
        self.job.merge_status = 'pending'
        self.job.merge_result = {'state': 'opened'}
        self.job.merge_notification_state = 'not_requested'
        self.job.save(update_fields=('merge_status', 'merge_result', 'merge_notification_state', 'updated_at'))

        def merge_while_sending(review, result, status):
            ReviewJob.objects.filter(pk=self.job.pk).update(
                merge_status='merged', merge_result={'state': 'merged'},
            )
            return {'ok': True}

        send.side_effect = merge_while_sending

        result = send_merge_result(self.job, self.review)

        self.assertEqual(result['status'], 'unknown')
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'merged')
        self.assertEqual(self.job.merge_notification_state, 'not_requested')

    @patch('review.services.auto_merge.WeComClient.send_merge_result')
    def test_notification_exception_does_not_mark_old_state_unknown_when_merge_changes(self, send):
        self.job.merge_status = 'pending'
        self.job.merge_result = {'state': 'opened'}
        self.job.merge_notification_state = 'not_requested'
        self.job.save(update_fields=('merge_status', 'merge_result', 'merge_notification_state', 'updated_at'))

        def merge_then_fail(review, result, status):
            ReviewJob.objects.filter(pk=self.job.pk).update(
                merge_status='merged', merge_result={'state': 'merged'},
            )
            raise RuntimeError('wecom failed after state changed')

        send.side_effect = merge_then_fail

        result = send_merge_result(self.job, self.review)

        self.assertEqual(result['status'], 'unknown')
        self.job.refresh_from_db()
        self.assertEqual(self.job.merge_status, 'merged')
        self.assertEqual(self.job.merge_notification_state, 'not_requested')

    @patch('review.services.auto_merge.WeComClient.send_merge_result', side_effect=RuntimeError('wecom down'))
    def test_notification_exception_is_unknown(self, _send):
        result = send_merge_result(self.job, self.review)

        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(ReviewJob.objects.get(pk=self.job.pk).merge_notification_state, 'unknown')

    @patch('review.integrations.wecom.requests.post')
    def test_disabled_or_merge_switch_off_notifications_are_skipped(self, post):
        self.config.enabled = False
        self.config.save(update_fields=('enabled', 'updated_at'))
        self.assertEqual(send_merge_result(self.job, self.review)['status'], 'done')
        self.job.merge_notification_state = 'not_requested'
        self.job.save(update_fields=('merge_notification_state', 'updated_at'))
        self.config.enabled = True
        self.config.notify_merge_result = False
        self.config.save(update_fields=('enabled', 'notify_merge_result', 'updated_at'))
        self.assertEqual(send_merge_result(self.job, self.review)['status'], 'done')
        post.assert_not_called()

    @override_settings(REVIEW_ALLOW_TEST_FALLBACK=False, WECOM_WEBHOOK_URL='')
    def test_missing_merge_webhook_is_not_reported_as_delivered(self):
        self.config.webhook_url = ''
        self.config.save(update_fields=('webhook_url', 'updated_at'))
        with self.assertRaises(IntegrationNotConfiguredError):
            WeComClient(self.config).send_merge_result(self.review, self.job.merge_result, self.job.merge_status)

    @override_settings(REVIEW_ALLOW_TEST_FALLBACK=True, WECOM_WEBHOOK_URL='')
    def test_unconfigured_merge_webhook_is_explicitly_skipped_in_local_mode(self):
        self.config.webhook_url = ''
        self.config.save(update_fields=('webhook_url', 'updated_at'))
        result = WeComClient(self.config).send_merge_result(self.review, self.job.merge_result, self.job.merge_status)
        self.assertTrue(result.get('skipped') or result.get('configured') is False)

    @patch('review.integrations.wecom.requests.post')
    def test_merge_card_includes_reason_and_mr_link(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 0}
        post.return_value = response
        self.review.gitlab_url = 'https://gitlab.example/team/notify-merge/-/merge_requests/357'
        self.review.save(update_fields=('gitlab_url', 'updated_at'))

        result = WeComClient(self.config).send_merge_result(
            self.review,
            {'reason': '门禁检查未通过'},
            'blocked',
        )

        self.assertTrue(result['ok'])
        content = post.call_args.kwargs['json']['markdown']['content']
        self.assertIn('门禁检查未通过', content)
        self.assertIn('https://gitlab.example', content)

    @patch('review.integrations.wecom.requests.post')
    def test_inherited_global_notification_is_used(self, post):
        self.project.notification_inherit = True
        self.project.save(update_fields=('notification_inherit', 'updated_at'))
        NotificationConfig.objects.create(
            scope=NotificationConfig.Scope.GLOBAL,
            project=None,
            webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=global',
        )
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 0}
        post.return_value = response

        result = send_merge_result(self.job, self.review)

        self.assertEqual(result['status'], 'done')
        self.assertIn('global', post.call_args.args[0])

    @patch('review.integrations.wecom.requests.post')
    def test_project_override_without_config_falls_back_to_global_notification(self, post):
        self.config.delete()
        global_config = NotificationConfig.objects.create(
            scope=NotificationConfig.Scope.GLOBAL,
            project=None,
            webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=global-fallback',
        )
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 0}
        post.return_value = response

        result = send_merge_result(self.job, self.review)

        self.assertEqual(result['status'], 'done')
        self.assertEqual(post.call_args.args[0], global_config.webhook_url)

    @patch('review.integrations.wecom.requests.post')
    def test_wecom_business_error_is_failed_and_idempotent(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 93000, 'errmsg': 'invalid webhook'}
        post.return_value = response

        first = send_merge_result(self.job, self.review)
        second = send_merge_result(self.job, self.review)

        self.assertEqual(first['status'], 'failed')
        self.assertEqual(second['status'], 'failed')
        self.assertEqual(post.call_count, 1)


class MergeNotificationApiTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(
            name='notify-merge-api',
            gitlab_project_path='team/notify-merge-api',
            notification_inherit=False,
        )
        ProjectPolicy.objects.create(project=self.project)
        self.review = Review.objects.create(project=self.project, mr_iid=358, summary='中文总评')
        self.job = ReviewJob.objects.create(
            project=self.project,
            review=self.review,
            idempotency_key='merge-notification-api-job',
            merge_status='merged',
            merge_notification_state='failed',
            merge_result={'state': 'merged'},
        )
        NotificationConfig.objects.create(
            project=self.project,
            scope=NotificationConfig.Scope.PROJECT,
            webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test',
        )

    @patch('review.integrations.wecom.requests.post')
    def test_failed_merge_notification_can_be_retried_from_review_detail(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 0}
        post.return_value = response

        result = self.client.post(f'/api/reviews/{self.review.pk}/merge-notify/')

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['status'], 'done')
        self.assertEqual(ReviewJob.objects.get(pk=self.job.pk).merge_notification_state, 'done')
        self.assertTrue(result.json().get('ok'))
