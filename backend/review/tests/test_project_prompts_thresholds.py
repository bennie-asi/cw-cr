from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from review.integrations.wecom import WeComClient
from review.models import GlobalPromptConfig, LineComment, Project, ProjectMembership, ProjectPolicy, Review
from review.serializers import LineCommentSerializer, ReviewSerializer
from review.services.gate_assessment import assess_review
from review.services.gate_control import fingerprint
from review.services.gates import evaluate_gate
from review.services.review_prompts import build_review_prompt
from review.tests.auth_helpers import admin_client


class ProjectPromptsThresholdTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.project = Project.objects.create(name='专项策略', gitlab_project_path='qa/project-prompts')
        self.policy = ProjectPolicy.objects.create(project=self.project, style_checks=True)
        self.url = f'/api/projects/{self.project.pk}/policy/'
        self.global_config = GlobalPromptConfig.objects.create(
            security_prompt='平台安全要求', reliability_prompt='平台可靠性要求', style_prompt='平台风格要求',
        )
        self.review = Review.objects.create(
            project=self.project, mr_iid=42, status='completed', summary='中文评审',
            summary_sync_status='synced', head_sha='a' * 40,
        )
        self.remote = {'iid': 42, 'sha': 'a' * 40, 'state': 'opened', 'draft': False,
                       'has_conflicts': False, 'detailed_merge_status': 'mergeable'}

    def patch(self, payload):
        response = self.client.patch(self.url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.policy.refresh_from_db()
        return response

    def comment(self, level):
        return LineComment.objects.create(review=self.review, file_path='main.py', new_line=1,
                                          title=f'{level} 问题', body='中文说明', level=level)

    def test_three_project_prompts_save_and_effective_values_are_isolated(self):
        values = dict(security_prompt='项目安全要求', reliability_prompt='项目可靠性要求', style_prompt='项目风格要求')
        response = self.patch(values)
        self.assertEqual(response.data['effective_prompts'], values)
        self.assertEqual(response.data['default_prompts']['security_prompt'], '平台安全要求')
        self.assertEqual(self.client.get(self.url).data['effective_prompts'], values)
        for mode in ('review', 'improve'):
            prompt = build_review_prompt(self.policy, mode)
            for value in values.values():
                self.assertIn(value, prompt)
            for value in ('平台安全要求', '平台可靠性要求', '平台风格要求'):
                self.assertNotIn(value, prompt)
            self.assertIn(self.global_config.general_prompt, prompt)
            self.assertIn(getattr(self.global_config, f'{mode}_prompt'), prompt)
        other = Project.objects.create(name='其他项目', gitlab_project_path='qa/other-prompts')
        other_policy = ProjectPolicy.objects.create(project=other)
        self.assertIn('平台安全要求', build_review_prompt(other_policy))
        self.assertNotIn('项目安全要求', build_review_prompt(other_policy))

    def test_clear_override_inherits_latest_platform_prompt_without_erasing_other_fields(self):
        self.patch({'security_prompt': '原项目安全', 'reliability_prompt': '保留项目可靠性'})
        self.patch({'security_prompt': ''})
        self.global_config.security_prompt = '更新后的平台安全'
        self.global_config.save()
        data = self.client.get(self.url).data
        self.assertEqual(data['security_prompt'], '')
        self.assertEqual(data['effective_prompts']['security_prompt'], '更新后的平台安全')
        self.assertEqual(data['reliability_prompt'], '保留项目可靠性')
        self.patch({'security_checks': False})
        self.assertNotIn('更新后的平台安全', build_review_prompt(self.policy))

    def test_invalid_prompt_values_do_not_partially_write_policy(self):
        for field in ('security_prompt', 'reliability_prompt', 'style_prompt'):
            for value in ('x' * 20001, '  \n', None, 12, True, [], {}):
                with self.subTest(field=field, value_type=type(value).__name__):
                    response = self.client.patch(self.url, {field: value, 'command_review': False}, format='json')
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(field, response.data)
                    self.policy.refresh_from_db()
                    self.assertTrue(self.policy.command_review)

    def test_member_can_read_effective_prompts_but_cannot_modify(self):
        user = get_user_model().objects.create_user('specialty-member')
        ProjectMembership.objects.create(project=self.project, user=user, role='member')
        client = APIClient()
        client.force_authenticate(user)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['effective_prompts']['security_prompt'], '平台安全要求')
        self.assertEqual(client.patch(self.url, {'security_prompt': '越权'}, format='json').status_code, 403)

    def test_three_thresholds_share_counts_flags_notifications_and_live_gate(self):
        comments = [self.comment(level) for level in ('P0', 'P1', 'P2', 'UNRATED')]
        for p1, p2, count, levels in (
            (False, False, 2, ['P0', 'UNRATED']),
            (True, False, 3, ['P0', 'P1', 'UNRATED']),
            (True, True, 4, ['P0', 'P1', 'P2', 'UNRATED']),
        ):
            with self.subTest(p1=p1, p2=p2):
                self.patch({'block_on_p1': p1, 'block_on_p2': p2})
                fresh = Review.objects.get(pk=self.review.pk)
                prefetched = Review.objects.prefetch_related('comments').get(pk=self.review.pk)
                self.assertEqual(fresh.blocking_comment_count, count)
                self.assertEqual(prefetched.blocking_comment_count, count)
                assessment = assess_review(fresh, self.policy, self.remote)
                self.assertEqual(assessment['blocking_levels'], levels)
                self.assertEqual(assessment['blocking_count'], count)
                self.assertFalse(assessment['passed'])
                self.assertFalse(evaluate_gate(fresh, self.policy).passed)
                data = ReviewSerializer(fresh).data
                self.assertEqual(data['blocking_comment_count'], count)
                self.assertEqual(data['status_label'], '有阻断')
                self.assertEqual(data['gate_status'], 'blocked')
                self.assertEqual(data['gate_status_label'], '阻断')
                for comment in comments:
                    flag = LineCommentSerializer(LineComment.objects.get(pk=comment.pk)).data['is_blocking']
                    self.assertEqual(flag, comment.level in levels)
                self.assertIn(f'阻断问题：{count} 条', WeComClient.review_content(fresh))
        LineComment.objects.filter(review=self.review).update(status='resolved')
        fresh = Review.objects.get(pk=self.review.pk)
        self.assertEqual(fresh.blocking_comment_count, 0)
        self.assertTrue(assess_review(fresh, self.policy, self.remote)['passed'])

    def test_relaxed_p1_is_advisory_and_strict_p2_is_blocking(self):
        p1 = self.comment('P1')
        self.patch({'block_on_p1': False, 'block_on_p2': False})
        fresh = Review.objects.get(pk=self.review.pk)
        self.assertTrue(evaluate_gate(fresh, self.policy).passed)
        self.assertEqual(fresh.blocking_comment_count, 0)
        self.assertEqual(ReviewSerializer(fresh).data['status_label'], '已完成')
        self.assertEqual(ReviewSerializer(fresh).data['gate_status'], 'passed')
        self.assertIn('阻断问题：0 条', WeComClient.review_content(fresh))
        p1.status = 'resolved'
        p1.save()
        self.comment('P2')
        self.patch({'block_on_p1': True, 'block_on_p2': True})
        self.assertFalse(evaluate_gate(Review.objects.get(pk=fresh.pk), self.policy).passed)

    def test_strict_p2_requires_p1_and_rejects_atomic_partial_write(self):
        response = self.client.patch(self.url, {'block_on_p1': False, 'block_on_p2': True, 'command_review': False}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('block_on_p2', response.data)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.command_review)

    def test_policy_changes_invalidate_preview_but_keep_project_auto_merge_switch(self):
        for field, value in (('security_prompt', '新安全要求'), ('reliability_prompt', '新可靠性要求'),
                             ('style_prompt', '新风格要求'), ('block_on_p2', True)):
            self.policy.auto_merge_enabled = True
            self.policy.dry_run_passed = True
            self.policy.dry_run_assessment = {'passed': True}
            self.policy.save()
            before = fingerprint(self.policy)
            self.patch({field: value})
            self.assertNotEqual(fingerprint(self.policy), before)
            self.assertTrue(self.policy.auto_merge_enabled)
            self.assertFalse(self.policy.dry_run_passed)
            self.assertEqual(self.policy.dry_run_assessment, {})

    def test_overview_current_blockers_and_recent_issues_follow_project_threshold(self):
        self.comment('P1')
        p2 = self.comment('P2')
        for p1_flag, p2_flag, count in ((False, False, 0), (True, False, 1), (True, True, 2)):
            self.patch({'block_on_p1': p1_flag, 'block_on_p2': p2_flag})
            response = self.client.get('/api/overview/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['blockers'], count)
            self.assertEqual(response.data['recent_severe_count'], count)
            self.assertEqual(response.data['blocked_reviews'], int(count > 0))
            self.assertEqual(response.data['completed'], int(count == 0))
        p2.status = 'resolved'
        p2.save()
        self.assertEqual(self.client.get('/api/overview/').data['recent_severe_count'], 1)
