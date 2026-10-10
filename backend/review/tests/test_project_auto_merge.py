from unittest.mock import patch

from django.test import TestCase, override_settings

from review.models import GitLabConfig, GlobalPromptConfig, LineComment, Project, ProjectPolicy, Review, ReviewJob
from review.services.auto_merge import execute_auto_merge
from review.services.mr_issues import prompt_fingerprint
from review.services.review_prompts import build_review_prompt
from review.tests.auth_helpers import admin_client


@override_settings(GITLAB_URL='', GITLAB_TOKEN='', REVIEW_ALLOW_TEST_FALLBACK=False)
class ProjectAutoMergeTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='持续自动合并', gitlab_project_path='qa/continuous')
        self.policy = ProjectPolicy.objects.create(project=self.project)
        GitLabConfig.objects.create(base_url='https://gitlab.test', api_token='test-only')
        self.url = f'/api/merge-gate/?project={self.project.pk}'
        self.policy_url = f'/api/projects/{self.project.pk}/policy/'
        self.check_url = f'/api/merge-gate/dry-run/?project={self.project.pk}'
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('No real HTTP'))
        network.start()
        self.addCleanup(network.stop)
        fetch = patch('review.services.auto_merge.GitLabClient.fetch_merge_request', side_effect=lambda iid, *args: {
            'iid': iid, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
            'has_conflicts': False, 'detailed_merge_status': 'mergeable',
        })
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)
        merge = patch('review.services.auto_merge.GitLabClient.merge', return_value={'state': 'merged'})
        self.merge = merge.start()
        self.addCleanup(merge.stop)

    def switch(self, enabled):
        return self.client.patch(self.url, {'auto_merge_enabled': enabled}, format='json')

    def ready_job(self, iid):
        self.policy.refresh_from_db()
        review = Review.objects.create(project=self.project, repository_path='qa/continuous', mr_iid=iid,
                                       status='completed', summary='完整评审完成', summary_sync_status='synced', head_sha='a' * 40)
        job = ReviewJob.objects.create(project=self.project, review=review, status='succeeded',
                                       idempotency_key=f'continuous-{iid}', analysis_result={
                                           '_issue_scope_fingerprint': prompt_fingerprint(build_review_prompt(self.policy, 'review')),
                                       })
        return review, job

    def test_can_enable_before_any_mr_or_manual_check_without_remote_side_effects(self):
        response = self.switch(True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['enabled'])
        self.assertEqual(response.data['scope'], 'project')
        self.assertFalse(response.data['dry_run_required'])
        self.assertTrue(response.data['automatic_validation_required'])
        self.assertTrue(self.client.get(self.url).data['enabled'])
        self.fetch.assert_not_called()
        self.merge.assert_not_called()
        self.assertFalse(Review.objects.exists())

    def test_one_activation_merges_two_future_mrs_without_consuming_switch(self):
        self.assertEqual(self.switch(True).status_code, 200)
        for iid in (1, 2):
            review, job = self.ready_job(iid)
            self.assertEqual(execute_auto_merge(job, review, self.policy)['status'], 'merged')
            self.assertTrue(self.client.get(self.url).data['enabled'])
        self.assertEqual(self.merge.call_count, 2)
        self.assertEqual([call.args[0].mr_iid for call in self.merge.call_args_list], [1, 2])

    def test_blocked_mr_does_not_disable_project_or_prevent_next_passing_mr(self):
        self.assertEqual(self.switch(True).status_code, 200)
        review, job = self.ready_job(1)
        LineComment.objects.create(review=review, file_path='a.py', new_line=1, level='P1',
                                   title='参数缺失', body='缺少校验', status='published')
        self.assertEqual(execute_auto_merge(job, review, self.policy)['status'], 'blocked')
        self.merge.assert_not_called()
        self.assertTrue(self.client.get(self.url).data['enabled'])
        later, next_job = self.ready_job(2)
        self.assertEqual(execute_auto_merge(next_job, later, self.policy)['status'], 'merged')
        self.merge.assert_called_once()

    def test_disabling_prevents_later_mr_merges_without_remote_requests(self):
        self.assertEqual(self.switch(True).status_code, 200)
        self.assertEqual(self.switch(False).status_code, 200)
        review, job = self.ready_job(1)
        self.assertEqual(execute_auto_merge(job, review, self.policy)['status'], 'skipped')
        self.assertFalse(self.client.get(self.url).data['enabled'])
        self.fetch.assert_not_called()
        self.merge.assert_not_called()

    def test_optional_failed_check_does_not_turn_project_switch_off(self):
        self.assertEqual(self.switch(True).status_code, 200)
        review, _ = self.ready_job(1)
        LineComment.objects.create(review=review, file_path='a.py', level='P0', title='严重问题', body='需修复')
        response = self.client.post(self.check_url, {'review_id': str(review.pk)}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['passed'])
        self.assertTrue(self.client.get(self.url).data['enabled'])
        self.merge.assert_not_called()

    def test_policy_edit_keeps_switch_on_but_stale_analysis_cannot_merge(self):
        self.policy.auto_merge_enabled = True
        self.policy.save()
        review, job = self.ready_job(1)
        response = self.client.patch(self.policy_url, {'security_prompt': '检查新增的认证绕过'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.auto_merge_enabled)
        self.assertEqual(execute_auto_merge(job, review, self.policy)['status'], 'blocked')
        self.merge.assert_not_called()
        fresh, fresh_job = self.ready_job(2)
        self.assertEqual(execute_auto_merge(fresh_job, fresh, self.policy)['status'], 'merged')

    def test_missing_analysis_scope_requires_new_complete_review(self):
        self.policy.auto_merge_enabled = True
        self.policy.save()
        review, job = self.ready_job(1)
        job.analysis_result = {}
        job.save()
        self.assertEqual(execute_auto_merge(job, review, self.policy)['status'], 'blocked')
        self.merge.assert_not_called()
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.auto_merge_enabled)

    def test_global_prompt_edit_blocks_old_scope_without_disabling_project(self):
        self.policy.auto_merge_enabled = True
        self.policy.save()
        review, job = self.ready_job(1)
        GlobalPromptConfig.objects.filter(pk=1).update(general_prompt='新增检查要求：检查本次变更中的资源泄漏')
        self.assertEqual(execute_auto_merge(job, review, self.policy)['status'], 'blocked')
        self.merge.assert_not_called()
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.auto_merge_enabled)

    def test_prompt_changed_during_gitlab_check_cannot_merge_old_review(self):
        self.switch(True)
        review, job = self.ready_job(1)

        def fetch_and_change(iid, *args):
            ProjectPolicy.objects.filter(pk=self.policy.pk).update(security_prompt='检查新增认证要求')
            return {'iid': iid, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
                    'has_conflicts': False, 'detailed_merge_status': 'mergeable'}

        self.fetch.side_effect = fetch_and_change
        self.assertEqual(execute_auto_merge(job, review, self.policy)['status'], 'blocked')
        self.merge.assert_not_called()
        self.assertTrue(self.client.get(self.url).data['enabled'])

    def test_threshold_changed_during_gitlab_check_cannot_use_old_assessment(self):
        self.switch(True)
        review, job = self.ready_job(1)
        LineComment.objects.create(review=review, file_path='a.py', new_line=1, level='P2',
                                   title='需要处理', body='新增边界问题', status='published')

        def fetch_and_change(iid, *args):
            ProjectPolicy.objects.filter(pk=self.policy.pk).update(block_on_p2=True)
            return {'iid': iid, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
                    'has_conflicts': False, 'detailed_merge_status': 'mergeable'}

        self.fetch.side_effect = fetch_and_change
        self.assertEqual(execute_auto_merge(job, review, self.policy)['status'], 'blocked')
        self.merge.assert_not_called()
        self.assertTrue(self.client.get(self.url).data['enabled'])
