from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from review.models import AuditLog, LineComment, Project, ProjectPolicy, Review, ReviewJob
from review.services.gates import evaluate_gate
from review.services.review_runner import create_review_job, run_review_job, trigger_review


class ReviewRunnerTests(TestCase):
    @override_settings(REVIEW_EXECUTE_INLINE=False)
    def test_async_trigger_returns_its_queued_review_without_calling_integrations(self):
        with patch('review.services.review_runner.GitLabClient.fetch_merge_request') as fetch, patch('review.services.review_runner.LLMClient.analyze') as analyze:
            job, review = trigger_review(self.project, {
                'event_uuid': 'queued-detail', 'mr_iid': 370, 'title': '新排队记录',
                'branch': 'feature/queued', 'source': Review.Source.REVIEW,
            })
            fetch.assert_not_called()
            analyze.assert_not_called()
        self.assertIsNotNone(review)
        self.assertEqual(review.pk, job.review_id)
        self.assertEqual(review.status, Review.Status.QUEUED)
        self.assertEqual(review.title, '新排队记录')
        self.assertEqual(review.source, Review.Source.REVIEW)

    @override_settings(REVIEW_EXECUTE_INLINE=False)
    def test_deduplicated_queue_and_worker_keep_one_review_and_hydrate_actual_mr_metadata(self):
        payload = {'event_uuid': 'queued-once', 'mr_iid': 371, 'title': '排队时标题', 'branch': 'queued-branch'}
        job, queued = trigger_review(self.project, payload)
        repeated, repeated_review = trigger_review(self.project, payload)
        self.assertIsNotNone(queued)
        self.assertEqual(repeated.pk, job.pk)
        self.assertEqual(repeated_review.pk, queued.pk)
        with patch('review.services.review_runner.GitLabClient.fetch_merge_request', return_value={
            'iid': 371, 'title': 'GitLab 最新标题', 'source_branch': 'actual-branch',
            'web_url': 'https://gitlab.example/demo/-/merge_requests/371', 'diff_refs': {},
        }):
            completed = run_review_job(job.pk)
        self.assertEqual(completed.pk, queued.pk)
        self.assertEqual(Review.objects.filter(project=self.project, mr_iid=371).count(), 1)
        self.assertEqual(completed.title, 'GitLab 最新标题')
        self.assertEqual(completed.branch, 'actual-branch')
        self.assertEqual(completed.gitlab_url, 'https://gitlab.example/demo/-/merge_requests/371')
        self.assertEqual(ReviewJob.objects.get(pk=job.pk).status, ReviewJob.Status.SUCCEEDED)

    @patch('review.management.commands.process_review_queue.time.sleep', side_effect=KeyboardInterrupt)
    def test_watch_worker_can_poll_and_exit_cleanly(self, sleep):
        call_command('process_review_queue', '--watch', '--interval', '0.1')
        sleep.assert_called_once_with(0.1)

    @patch('review.management.commands.process_review_queue.reconcile_pending_merges', return_value=1)
    def test_queue_worker_reconciles_pending_merge_outcomes(self, reconcile):
        call_command('process_review_queue', '--once')
        reconcile.assert_called_once_with(limit=1)

    def setUp(self):
        self.project = Project.objects.create(
            name='cw-cr-demo',
            gitlab_project_path='demo/cw-cr-demo',
        )
        ProjectPolicy.objects.create(project=self.project)

    @override_settings(REVIEW_EXECUTE_INLINE=True)
    def test_test_runner_persists_chinese_summary_and_line_comments(self):
        job = create_review_job(
            self.project,
            {
                'event_uuid': 'event-357',
                'mr_iid': 357,
                'title': '自动接入代码审查平台',
                'branch': 'feature/auto-review',
                'diff': 'src/service/review.ts\nsrc/service/webhook.ts',
            },
            kind=ReviewJob.Kind.REVIEW,
        )

        review = run_review_job(job.id)

        self.assertEqual(review.status, Review.Status.BLOCKED, review.error_message)
        self.assertIn('测试评审结果', review.summary)
        self.assertIn('src/service/review.ts', review.diff_text)
        self.assertIn('src/service/review.ts', review.changed_files)
        self.assertGreaterEqual(review.comments.count(), 2)
        self.assertEqual(review.comments.filter(status=LineComment.Status.PUBLISHED).count(), review.comments.count())
        self.assertEqual(job.__class__.objects.get(pk=job.pk).status, ReviewJob.Status.SUCCEEDED)
        self.assertTrue(AuditLog.objects.filter(action='发送企业微信评审通知').exists())
        self.assertTrue(AuditLog.objects.exists())
        self.assertFalse(AuditLog.objects.exclude(project=self.project, actor='system').exists())

    @patch('review.services.review_runner.GitLabClient.create_summary_note')
    def test_runner_publishes_chinese_total_review_to_gitlab(self, create_summary_note):
        create_summary_note.return_value = {'id': 'summary-note-1', 'demo': True}
        job = create_review_job(self.project, {'event_uuid': 'event-summary-note', 'mr_iid': 369, 'title': '总评同步'})

        review = run_review_job(job.id)

        self.assertTrue(review.summary)
        create_summary_note.assert_called_once_with(review)

    def test_gate_passes_after_all_blockers_are_resolved(self):
        job = create_review_job(self.project, {'event_uuid': 'event-358', 'mr_iid': 358, 'title': '修复'})
        review = run_review_job(job.id)
        review.comments.filter(level__in=[LineComment.Level.P0, LineComment.Level.P1]).update(status=LineComment.Status.RESOLVED)

        result = evaluate_gate(review)

        self.assertTrue(result.passed)
        self.assertEqual(result.status, Review.GateStatus.PASSED)

    def test_policy_can_allow_p1_without_allowing_p0(self):
        review = Review.objects.create(project=self.project, mr_iid=361, title='P1 策略')
        LineComment.objects.create(
            review=review,
            file_path='src/service/review.ts',
            new_line=8,
            level=LineComment.Level.P1,
            title='P1 建议',
            body='建议补充上下文',
        )
        policy = ProjectPolicy.objects.get(project=self.project)
        policy.block_on_p1 = False
        policy.save(update_fields=('block_on_p1', 'updated_at'))

        result = evaluate_gate(review, policy)

        self.assertTrue(result.passed)

    @patch('review.services.review_runner.WeComClient.send_review', side_effect=RuntimeError('wecom unavailable'))
    def test_notification_failure_does_not_fail_review(self, _send_review):
        job = create_review_job(self.project, {'event_uuid': 'event-wecom-failure', 'mr_iid': 360, 'title': '通知失败'})
        review = run_review_job(job.id)

        self.assertEqual(review.status, Review.Status.BLOCKED)
        self.assertEqual(ReviewJob.objects.get(pk=job.pk).status, ReviewJob.Status.SUCCEEDED)
        self.assertTrue(AuditLog.objects.filter(action='企业微信通知失败').exists())

    @patch('review.services.review_runner.send_merge_result')
    @patch('review.services.review_runner.execute_auto_merge')
    def test_enabled_policy_runs_guarded_auto_merge_and_notifies_result(self, merge_result, execute_merge):
        execute_merge.return_value = {'status': 'merged', 'state': 'merged'}
        merge_result.return_value = {'status': 'done'}
        policy = ProjectPolicy.objects.get(project=self.project)
        policy.auto_merge_enabled = True
        policy.save(update_fields=('auto_merge_enabled', 'updated_at'))
        job = create_review_job(self.project, {'event_uuid': 'event-auto-merge-worker', 'mr_iid': 367, 'title': '自动合并'})

        review = run_review_job(job.id)

        execute_merge.assert_called_once()
        self.assertEqual(execute_merge.call_args.args[0].pk, job.pk)
        self.assertEqual(execute_merge.call_args.args[1].pk, review.pk)
        merge_result.assert_called_once()
        self.assertEqual(ReviewJob.objects.get(pk=job.pk).status, ReviewJob.Status.SUCCEEDED)
        self.assertTrue(AuditLog.objects.filter(action='自动合并结果').exists())

    @override_settings(REVIEW_RETRY_BACKOFF_SECONDS=0)
    @patch('review.services.review_runner.LLMClient.analyze', side_effect=RuntimeError('模型临时超时'))
    def test_transient_failure_is_requeued_until_attempt_limit(self, _analyze):
        job = create_review_job(self.project, {'event_uuid': 'event-retryable', 'mr_iid': 362, 'title': '可重试任务'})

        first_review = run_review_job(job.id)

        job.refresh_from_db()
        self.assertEqual(first_review.status, Review.Status.FAILED)
        self.assertEqual(job.status, ReviewJob.Status.QUEUED)
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.next_attempt_at)
        self.assertTrue(AuditLog.objects.filter(action='评审任务重试排队').exists())

        job.next_attempt_at = timezone.now() - timedelta(seconds=1)
        job.save(update_fields=('next_attempt_at', 'updated_at'))
        run_review_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.QUEUED)
        self.assertEqual(job.attempts, 2)

        job.next_attempt_at = timezone.now() - timedelta(seconds=1)
        job.save(update_fields=('next_attempt_at', 'updated_at'))
        run_review_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.FAILED)
        self.assertEqual(job.attempts, 3)
        self.assertIsNone(job.next_attempt_at)

    @override_settings(REVIEW_RETRY_BACKOFF_SECONDS=60)
    def test_queue_worker_skips_jobs_before_next_attempt(self):
        job = create_review_job(self.project, {'event_uuid': 'event-future-retry', 'mr_iid': 363, 'title': '延迟重试'})
        job.next_attempt_at = timezone.now() + timedelta(minutes=5)
        job.save(update_fields=('next_attempt_at', 'updated_at'))

        call_command('process_review_queue', '--once')

        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.QUEUED)
        self.assertEqual(job.attempts, 0)

    @override_settings(REVIEW_RETRY_BACKOFF_SECONDS=0)
    @patch('review.services.review_runner.GitLabClient.fetch_merge_request', side_effect=RuntimeError('GitLab 暂时不可用'))
    def test_failed_fetch_is_attached_to_job_for_retry_visibility(self, _fetch):
        job = create_review_job(self.project, {'event_uuid': 'event-fetch-failure', 'mr_iid': 364, 'title': 'GitLab 失败'})

        review = run_review_job(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.QUEUED)
        self.assertEqual(job.review_id, review.id)
        self.assertEqual(review.status, Review.Status.FAILED)

    @override_settings(REVIEW_RETRY_BACKOFF_SECONDS=0)
    @patch('review.services.review_runner.GitLabClient.create_discussion', side_effect=[RuntimeError('GitLab 暂时不可用'), {'id': 'discussion-retry'}])
    @patch('review.services.review_runner.LLMClient.analyze')
    def test_retry_reuses_open_line_comment_instead_of_duplicating_it(self, analyze, _discussion):
        analyze.return_value = {
            'summary': '中文总评',
            'findings': [{
                'file_path': 'src/retry.py',
                'new_line': 10,
                'level': 'P1',
                'title': '请保留错误上下文',
                'body': '不要吞掉异常。',
            }],
        }
        job = create_review_job(self.project, {'event_uuid': 'event-comment-retry', 'mr_iid': 365, 'title': '评论重试'})

        first_review = run_review_job(job.id)
        job.refresh_from_db()
        job.next_attempt_at = timezone.now() - timedelta(seconds=1)
        job.save(update_fields=('next_attempt_at', 'updated_at'))
        second_review = run_review_job(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED)
        self.assertEqual(first_review.id, second_review.id)
        self.assertEqual(second_review.comments.count(), 1)
        self.assertEqual(second_review.comments.first().status, LineComment.Status.PUBLISHED)
