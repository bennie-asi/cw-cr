from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase, override_settings

from review.models import LineComment, Project, ProjectPolicy, ReviewJob
from review.services.review_runner import create_review_job, run_review_job
from review.serializers import ReviewSerializer


@override_settings(REVIEW_RETRY_BACKOFF_SECONDS=0)
class RetrySnapshotTests(TestCase):
    def test_model_suggestion_is_persisted_for_manual_and_automatic_retry(self):
        suggestion = {'start_line': 1, 'end_line': 1, 'original_code': 'a', 'replacement_code': 'fixed'}
        self.result['findings'][0]['suggestion'] = suggestion
        review = run_review_job(self.job.id)
        comment = review.comments.get(new_line=1)
        self.assertEqual(getattr(comment, 'suggestion', None), suggestion)

    def setUp(self):
        self.project = Project.objects.create(name='retry', gitlab_project_path='team/retry')
        ProjectPolicy.objects.create(project=self.project)
        self.mr = {'title': 'Review', 'diff_refs': {'base_sha': 'base', 'start_sha': 'start', 'head_sha': 'head'}}
        self.result = {'summary': '固定总评', 'findings': [
            {'file_path': 'a.py', 'new_line': i, 'title': f'问题{i}', 'body': '说明', 'level': 'P1'}
            for i in (1, 2)
        ]}
        self.patches = [
            patch('review.services.review_runner.GitLabClient.fetch_merge_request', return_value=self.mr),
            patch('review.services.review_runner.GitLabClient.fetch_diff', return_value='diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1,2 @@\n+a\n+b'),
            patch('review.services.review_runner.LLMClient.analyze', return_value=self.result),
            patch('review.services.review_runner.GitLabClient.create_discussion'),
            patch('review.services.review_runner.GitLabClient.create_summary_note', return_value={'id': 'summary'}),
            patch('review.services.review_runner.WeComClient.send_review', return_value={'ok': True}),
        ]
        self.fetch, self.diff, self.analyze, self.publish, _, self.notify = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.publish.side_effect = [{'id': 'first'}, RuntimeError('temporary failure'), {'id': 'second'}]
        self.job = create_review_job(self.project, {'mr_iid': 1})

    def test_retry_uses_persisted_result_not_new_model_wording(self):
        review = run_review_job(self.job.id)
        changed = deepcopy(self.result)
        changed['findings'][0]['body'] = '模型第二次生成不同措辞'
        self.analyze.return_value = changed
        self.publish.side_effect = None
        self.publish.return_value = {'id': 'second'}
        review = run_review_job(self.job.id)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ReviewJob.Status.SUCCEEDED)
        self.assertEqual(self.analyze.call_count, 1)
        self.assertEqual(self.diff.call_count, 1)
        self.assertEqual(review.comments.count(), 2)
        self.assertEqual(review.summary, '固定总评')

    def test_publication_failure_preserves_generated_summary_and_actual_failed_comment(self):
        self.job.max_attempts = 1
        self.job.save()
        review = run_review_job(self.job.id)
        self.job.refresh_from_db()
        self.assertEqual(review.summary, '固定总评')
        self.assertEqual(review.comments.get(new_line=2).status, LineComment.Status.FAILED)
        data = ReviewSerializer(review).data
        self.assertEqual(data['generation_state'], 'completed')
        self.assertEqual(data['publication_state'], 'failed')
        self.assertEqual(self.analyze.call_count, 1)

    def test_changed_commit_stops_retry_before_publication(self):
        run_review_job(self.job.id)
        self.fetch.return_value = {**self.mr, 'diff_refs': {**self.mr['diff_refs'], 'head_sha': 'new-head'}}
        self.publish.reset_mock(side_effect=True)
        self.publish.return_value = {'id': 'unexpected'}
        review = run_review_job(self.job.id)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ReviewJob.Status.FAILED)
        self.assertIn('提交', review.error_message)
        self.publish.assert_not_called()

    def test_change_during_analysis_prevents_any_publication(self):
        changed = {**self.mr, 'diff_refs': {**self.mr['diff_refs'], 'head_sha': 'new-head'}}
        self.fetch.side_effect = [self.mr, changed]
        run_review_job(self.job.id)
        self.publish.assert_not_called()

    def test_retry_after_fetch_failure_populates_actual_commit_refs(self):
        self.fetch.side_effect = RuntimeError('offline')
        run_review_job(self.job.id)
        self.fetch.side_effect = None
        self.publish.side_effect = None
        self.publish.return_value = {'id': 'discussion'}
        review = run_review_job(self.job.id)
        self.assertEqual(review.head_sha, 'head')

    def test_retry_does_not_republish_a_resolved_discussion(self):
        review = run_review_job(self.job.id)
        comment = review.comments.get(new_line=1)
        comment.status = LineComment.Status.RESOLVED
        comment.save()
        self.publish.reset_mock(side_effect=True)
        self.publish.return_value = {'id': 'second'}
        run_review_job(self.job.id)
        comment.refresh_from_db()
        self.assertEqual(comment.status, LineComment.Status.RESOLVED)
        self.assertEqual(self.publish.call_count, 1)

    def test_recovered_remote_resolved_comment_keeps_resolved_state(self):
        self.publish.side_effect = None
        self.publish.return_value = {'id': 'recovered', 'resolved': True}
        review = run_review_job(self.job.id)
        self.assertEqual(review.comments.filter(status=LineComment.Status.RESOLVED).count(), 2)

    def test_worker_recovers_abandoned_running_job(self):
        self.job.status = ReviewJob.Status.RUNNING
        self.job.attempts = 1
        self.job.save()
        self.publish.side_effect = None
        self.publish.return_value = {'id': 'published'}
        run_review_job(self.job.pk, recover=True)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ReviewJob.Status.SUCCEEDED)
        self.assertEqual(self.job.attempts, 2)

    def test_worker_does_not_take_over_live_job(self):
        from review.services.execution_locks import resource_lock
        with resource_lock(f'job:{self.job.pk}'):
            run_review_job(self.job.pk, recover=True)
        self.fetch.assert_not_called()
        self.job.refresh_from_db()
        self.assertEqual(self.job.attempts, 0)

    def test_uncertain_notification_is_not_automatically_sent_again(self):
        self.job.notification_state = 'sending'
        self.job.save()
        self.publish.side_effect = None
        self.publish.return_value = {'id': 'published'}
        run_review_job(self.job.pk)
        self.notify.assert_not_called()
        self.job.refresh_from_db()
        self.assertEqual(self.job.notification_state, 'unknown')
