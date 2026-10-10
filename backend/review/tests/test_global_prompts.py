from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from review.models import Project, ProjectPolicy
from review.services.review_runner import create_review_job, run_review_job
from review.tests.auth_helpers import admin_client


class GlobalPromptTests(TestCase):
    def setUp(self):
        self.client = admin_client()
        self.url = '/api/prompt-config/'

    def test_platform_prompts_can_be_saved_and_read_back(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['general_prompt'])
        response = self.client.patch(self.url, {'review_prompt': '只关注本次新增缺陷'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.url).data['review_prompt'], '只关注本次新增缺陷')

    def test_prompt_configuration_is_platform_admin_only(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)
        user = get_user_model().objects.create_user('prompt-member')
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(client.get(self.url).status_code, 403)
        self.assertEqual(client.patch(self.url, {'general_prompt': '越权修改'}, format='json').status_code, 403)

    def test_bad_fields_do_not_partially_update_prompts(self):
        baseline = self.client.get(self.url)
        self.assertEqual(baseline.status_code, 200)
        for field, value in [('general_prompt', '  '), ('review_prompt', 'x' * 20001), ('unexpected', 'value')]:
            response = self.client.patch(self.url, {field: value, 'style_prompt': '不应写入'}, format='json')
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.data)
            self.assertEqual(self.client.get(self.url).data['style_prompt'], baseline.data['style_prompt'])

    @patch('review.services.review_runner.WeComClient.send_review', return_value={'skipped': True})
    @patch('review.services.review_runner.GitLabClient.create_summary_note')
    @patch('review.services.review_runner.LLMClient.analyze', return_value={'summary': '中文结果', 'findings': []})
    def test_runner_uses_mode_and_enabled_global_prompts(self, analyze, _note, _notify):
        values = {
            'general_prompt': '全局通用要求', 'review_prompt': '全局审查要求',
            'improve_prompt': '全局改进要求', 'security_prompt': '全局安全要求',
            'reliability_prompt': '全局可靠性要求', 'style_prompt': '全局风格要求',
        }
        response = self.client.patch(self.url, values, format='json')
        self.assertEqual(response.status_code, 200)
        project = Project.objects.create(name='Prompt project', gitlab_project_path='team/prompts')
        ProjectPolicy.objects.create(project=project, summary_prompt='保留项目要求', reliability_checks=False, style_checks=True)
        for mode in ('review', 'improve'):
            job = create_review_job(project, {'mr_iid': 1}, kind=mode)
            review = run_review_job(job.id)
            self.assertFalse(review.error_message)
            prompt = analyze.call_args.args[1]
            self.assertIn('全局通用要求', prompt)
            self.assertIn(values[f'{mode}_prompt'], prompt)
            self.assertNotIn(values['improve_prompt' if mode == 'review' else 'review_prompt'], prompt)
            self.assertIn('全局安全要求', prompt)
            self.assertIn('全局风格要求', prompt)
            self.assertNotIn('全局可靠性要求', prompt)
            self.assertIn('保留项目要求', prompt)
