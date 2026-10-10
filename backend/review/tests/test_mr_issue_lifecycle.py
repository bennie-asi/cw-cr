from copy import deepcopy
from uuid import uuid4
from unittest.mock import patch

from django.test import TestCase, override_settings

from review.models import LineComment, Project, ProjectPolicy, Review, ReviewEngineConfig, ReviewJob
from review.serializers import ReviewSerializer, ProjectSerializer
from review.integrations.wecom import WeComClient
from review.services.gates import evaluate_gate
from review.services.mr_issues import resolve_manually, enroll_legacy
from review.services.review_runner import create_review_job, run_review_job


DIFF = '''diff --git a/src/review.py b/src/review.py
--- a/src/review.py
+++ b/src/review.py
@@ -10,2 +10,3 @@
-old = params["user"]
+user = params.get("user")
+token = params["token"]
 return token
'''
CODE = 'token = params["token"]'
FINDING = {'file_path': 'src/review.py', 'new_line': 11, 'anchor_code': CODE,
           'level': 'P1', 'title': '缺少 token 时会触发异常',
           'body': f'`{CODE}` 在缺少键时抛出异常，需要处理缺失输入。'}


@override_settings(REVIEW_ALLOW_TEST_FALLBACK=False, REVIEW_MAX_RETRIES=1, REVIEW_EXECUTE_INLINE=False)
class MRIssueLifecycleTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='复审验收', gitlab_project_path='qa/demo')
        self.policy = ProjectPolicy.objects.create(project=self.project)
        ReviewEngineConfig.objects.update_or_create(pk=1, defaults={'default_engine': 'builtin'})
        self.head = 'c' * 40
        self.diff = DIFF
        self.findings = [FINDING]
        self.posts = 0
        self.patches = [
            patch('review.services.review_runner.GitLabClient.fetch_merge_request', side_effect=self.mr),
            patch('review.services.review_runner.GitLabClient.fetch_diff', side_effect=lambda *a, **kw: self.diff),
            patch('review.services.review_runner.LLMClient.analyze', side_effect=lambda *a: {'summary': '检查本次变更和已报告问题。', 'findings': deepcopy(self.findings)}),
            patch('review.services.review_runner.GitLabClient.create_discussion', side_effect=self.post),
            patch('review.services.review_runner.GitLabClient.resolve_discussion', return_value=True),
            patch('review.services.review_runner.GitLabClient.reopen_discussion', return_value=True, create=True),
            patch('review.services.review_runner.GitLabClient.create_summary_note', return_value={'id': 1}),
            patch('review.services.review_runner.WeComClient.send_review', return_value={'skipped': True}),
        ]
        self.mocks = [item.start() for item in self.patches]
        for item in self.patches:
            self.addCleanup(item.stop)

    def mr(self, iid, _payload):
        return {'iid': iid, 'title': '处理缺失输入', 'source_branch': 'fix/input', 'state': 'opened',
                'sha': self.head, 'draft': False, 'has_conflicts': False, 'detailed_merge_status': 'mergeable',
                'diff_refs': {'base_sha': 'a' * 40, 'start_sha': 'b' * 40, 'head_sha': self.head}}

    def post(self, _review, _comment):
        self.posts += 1
        return {'id': f'discussion-{self.posts}'}

    def run_review(self, *, kind=ReviewJob.Kind.REVIEW, iid=41, path=None):
        job = create_review_job(self.project, {'event_uuid': str(uuid4()), 'mr_iid': iid,
            'repository_path': path or self.project.gitlab_project_path,
            'source': Review.Source.IMPROVE if kind == ReviewJob.Kind.IMPROVE else Review.Source.REVIEW}, kind)
        result = run_review_job(job.pk)
        job.refresh_from_db()
        return result, job

    def test_repeated_issue_reuses_discussion_across_reviews_and_reworded_body(self):
        first, _ = self.run_review()
        self.findings = [{**FINDING, 'body': f'`{CODE}` 缺少 token 参数时会异常，请先校验。'}]
        second, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        self.assertEqual(self.posts, 1)
        self.assertEqual(first.comments.get().discussion_id, second.comments.get().discussion_id)
        self.assertEqual(first.comments.get().issue_id, second.comments.get().issue_id)
        self.assertEqual(second.blocking_comment_count, 1)
        self.assertEqual(second.recheck_result['repeated'], 1)

    def test_new_commit_complete_review_closes_fixed_issue_without_deleting_history(self):
        first, _ = self.run_review()
        old = first.comments.get()
        self.head = 'd' * 40
        self.diff = DIFF.replace('+token = params["token"]', '+token = params.get("token")')
        self.findings = []
        second, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        old.refresh_from_db()
        self.assertEqual(old.status, LineComment.Status.RESOLVED)
        self.mocks[4].assert_called_once()
        self.assertEqual(second.status, Review.Status.COMPLETED)
        self.assertEqual(first.blocking_comment_count, 0)
        self.assertEqual(ReviewSerializer(first).data['blocking_comment_count'], 0)
        self.assertEqual(second.recheck_result['resolved'], 1)
        self.assertTrue(LineComment.objects.filter(pk=old.pk).exists())

    def test_unique_code_anchor_moves_without_creating_a_new_issue_or_discussion(self):
        first, _ = self.run_review()
        self.head = 'd' * 40
        self.diff = DIFF.replace('+10,3', '+10,4').replace('+token', '+log = True\n+token')
        self.findings = [{**FINDING, 'new_line': 12}]
        second, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        self.assertEqual(self.posts, 1)
        self.assertEqual(second.comments.get().new_line, 12)
        self.assertEqual(first.comments.get().issue_id, second.comments.get().issue_id)

    def test_duplicate_findings_in_one_result_count_once(self):
        self.findings = [FINDING, {**FINDING, 'body': f'`{CODE}` 在输入缺失时存在同一个异常问题。'}]
        review, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, review.error_message)
        self.assertEqual(review.comment_count, 1)
        self.assertEqual(review.blocking_comment_count, 1)
        self.assertEqual(self.posts, 1)

    def test_distinct_root_causes_on_same_line_are_not_folded(self):
        self.findings = [FINDING, {**FINDING, 'title': '未验证凭证来源', 'body': f'`{CODE}` 使用不可信凭证来源，需验证调用者。'}]
        review, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, review.error_message)
        self.assertEqual(review.comment_count, 2)
        self.assertEqual(self.posts, 2)

    def test_same_number_in_other_repository_or_mr_is_not_folded(self):
        self.project.repositories.create(path='qa/other')
        first, _ = self.run_review()
        other_repo, _ = self.run_review(path='qa/other')
        other_mr, _ = self.run_review(iid=42)
        self.assertEqual(self.posts, 3)
        self.assertNotEqual(first.comments.get().issue_id, other_repo.comments.get().issue_id)
        self.assertNotEqual(first.comments.get().issue_id, other_mr.comments.get().issue_id)

    def test_same_sha_omission_preserves_old_blocker(self):
        first, _ = self.run_review()
        self.findings = []
        second, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        self.assertEqual(second.blocking_comment_count, 1)
        self.assertEqual(first.blocking_comment_count, 1)
        self.mocks[4].assert_not_called()
        self.assertEqual(second.recheck_result['retained'], 1)

    def test_improve_omission_does_not_clear_previous_blocker(self):
        first, _ = self.run_review()
        self.head = 'd' * 40
        self.findings = []
        second, job = self.run_review(kind=ReviewJob.Kind.IMPROVE)
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        self.assertEqual(second.blocking_comment_count, 1)
        self.assertEqual(first.blocking_comment_count, 1)
        self.mocks[4].assert_not_called()

    def test_changed_prompt_scope_cannot_clear_a_prior_blocker_by_omission(self):
        first, _ = self.run_review()
        self.policy.security_checks = False
        self.policy.save()
        self.head = 'd' * 40
        self.findings = []
        second, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        self.assertEqual(first.blocking_comment_count, 1)
        self.assertEqual(second.blocking_comment_count, 1)
        self.mocks[4].assert_not_called()

    def test_analysis_failure_preserves_old_issue(self):
        first, _ = self.run_review()
        self.head = 'd' * 40
        self.mocks[2].side_effect = RuntimeError('模型超时')
        failed, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.FAILED)
        self.assertEqual(first.blocking_comment_count, 1)
        self.mocks[4].assert_not_called()

    def test_resolution_failure_preserves_local_blocker_and_stops_pass(self):
        first, _ = self.run_review()
        self.head = 'd' * 40
        self.findings = []
        self.mocks[4].side_effect = RuntimeError('GitLab 503')
        failed, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.FAILED)
        self.assertEqual(first.blocking_comment_count, 1)
        self.assertEqual(failed.status, Review.Status.FAILED)

    def test_manual_resolution_applies_to_all_occurrences_and_same_head_stays_resolved(self):
        from review.integrations.gitlab import GitLabClient
        first, _ = self.run_review()
        second, _ = self.run_review()
        resolve_manually(second, second.comments.get(), GitLabClient(self.project))
        self.assertEqual(first.blocking_comment_count, 0)
        self.assertEqual(second.blocking_comment_count, 0)
        repeated, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, repeated.error_message)
        self.assertEqual(repeated.blocking_comment_count, 0)
        self.assertEqual(self.posts, 1)
        self.mocks[5].assert_not_called()

    def test_new_commit_recurring_issue_reopens_original_discussion(self):
        from review.integrations.gitlab import GitLabClient
        first, _ = self.run_review()
        resolve_manually(first, first.comments.get(), GitLabClient(self.project))
        self.head = 'd' * 40
        second, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        self.assertEqual(second.blocking_comment_count, 1)
        self.assertEqual(first.blocking_comment_count, 1)
        self.assertEqual(first.comments.get().issue_id, second.comments.get().issue_id)
        self.assertEqual(self.posts, 1)
        self.mocks[5].assert_called_once()

    def test_same_anchor_at_two_locations_is_not_folded(self):
        self.diff = DIFF.replace('+10,3', '+10,4').replace('+token = params["token"]', '+token = params["token"]\n+token = params["token"]')
        self.findings = [FINDING, {**FINDING, 'new_line': 12}]
        review, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, review.error_message)
        self.assertEqual(review.comment_count, 2)
        self.assertEqual(self.posts, 2)

    def test_older_failed_job_cannot_overwrite_newer_successful_review(self):
        first, _ = self.run_review()
        self.head, self.findings = 'd' * 40, []
        self.mocks[4].side_effect = RuntimeError('GitLab 503')
        failed, old_job = self.run_review()
        self.mocks[4].side_effect = None
        self.findings = [FINDING]
        latest, _ = self.run_review()
        old_job.status, old_job.attempts = ReviewJob.Status.QUEUED, 0
        old_job.save()
        result = run_review_job(old_job.pk)
        old_job.refresh_from_db()
        self.assertEqual(old_job.status, ReviewJob.Status.FAILED)
        self.assertIn('更新的复审', result.error_message)
        self.assertEqual(latest.blocking_comment_count, 1)
        self.assertEqual(first.blocking_comment_count, 1)

    def test_counts_api_and_notification_deduplicate_legacy_occurrences(self):
        first, _ = self.run_review()
        original = first.comments.get()
        original.pk = None
        original.save()
        second, _ = self.run_review()
        self.assertEqual(first.comment_count, 1)
        self.assertEqual(first.blocking_comment_count, 1)
        self.assertIn('还有 1 个', evaluate_gate(first).reason)
        self.assertEqual(ProjectSerializer(self.project).get_blockers(self.project), 1)
        self.assertEqual(len(ReviewSerializer(first).data['comments']), 1)
        self.assertEqual(WeComClient.review_content(first).count('缺少 token 时会触发异常'), 1)
        self.assertEqual(second.comment_count, 1)

    def test_configuration_changed_during_analysis_cannot_clear_old_issue(self):
        first, _ = self.run_review()
        self.head = 'd' * 40
        def analyze(*_args):
            ProjectPolicy.objects.filter(pk=self.policy.pk).update(security_checks=False)
            return {'summary': '本轮未报告新的问题。', 'findings': []}
        self.mocks[2].side_effect = analyze
        second, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        self.assertEqual(first.blocking_comment_count, 1)
        self.assertEqual(second.blocking_comment_count, 1)
        self.mocks[4].assert_not_called()

    def test_manual_resolution_during_same_head_review_is_not_overwritten(self):
        from review.integrations.gitlab import GitLabClient
        first, _ = self.run_review()
        calls = 0
        def fetch(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                resolve_manually(first, first.comments.get(), GitLabClient(self.project))
            return self.mr(*args)
        self.mocks[0].side_effect = fetch
        second, job = self.run_review()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, second.error_message)
        self.assertEqual(first.blocking_comment_count, 0)
        self.assertEqual(second.blocking_comment_count, 0)

    def test_legacy_duplicate_rows_honor_same_head_manual_resolution(self):
        first, _ = self.run_review()
        original = first.comments.get()
        original.issue = None
        original.status = LineComment.Status.RESOLVED
        original.save()
        original.pk = None
        original.status = LineComment.Status.PUBLISHED
        original.save()
        first.comments.update(issue=None)
        first.project.issues.all().delete()
        enroll_legacy(first)
        self.assertEqual(first.comment_count, 1)
        self.assertEqual(first.blocking_comment_count, 0)

    def test_overview_and_project_api_show_one_current_issue_across_reviews(self):
        from review.tests.auth_helpers import admin_client
        first, _ = self.run_review()
        second, _ = self.run_review()
        client = admin_client()
        response = client.get('/api/overview/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['blockers'], 1)
        self.assertEqual(response.data['recent_severe_count'], 1)
        self.assertEqual(len(response.data['recent_severe_issues']), 1)
        self.assertEqual(response.data['reviews'], 2)
        response = client.get(f'/api/projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['blockers'], 1)

    def test_retry_of_legacy_review_with_duplicate_occurrences_is_idempotent(self):
        first, job = self.run_review()
        original = first.comments.get()
        original.pk = None
        original.save()
        first.comments.update(issue=None)
        first.project.issues.all().delete()
        first.recheck_result = {}
        first.save(update_fields=('recheck_result',))
        job.status, job.attempts = ReviewJob.Status.QUEUED, 0
        job.save()
        repeated = run_review_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, ReviewJob.Status.SUCCEEDED, repeated.error_message)
        self.assertEqual(repeated.comment_count, 1)
        self.assertEqual(self.posts, 1)
