from unittest.mock import Mock, patch
from urllib.parse import quote

import requests
from django.test import TestCase, override_settings

from review.integrations.gitlab import GitLabClient
from review.integrations.llm import LLMClient
from review.integrations.wecom import WeComClient
from review.models import GitLabConfig, LineComment, ModelConfig, NotificationConfig, Project, Review


class GitLabIntegrationTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name='remote-demo',
            gitlab_project_path='group/remote-demo',
            gitlab_url='https://gitlab.internal',
        )

    @override_settings(GITLAB_TOKEN='gitlab-token', GITLAB_URL='https://gitlab.internal')
    @patch('review.integrations.gitlab.requests.get')
    def test_fetch_diff_keeps_changed_source_for_model_context(self, get):
        response = Mock()
        response.json.return_value = {
            'changes': [
                {'new_path': 'src/review.py', 'diff': '@@ -1 +1 @@\n-old\n+new'},
            ],
        }
        get.return_value = response

        diff = GitLabClient(self.project).fetch_diff(7, {})

        self.assertIn('src/review.py', diff)
        self.assertIn('+new', diff)

    @override_settings(GITLAB_TOKEN='gitlab-token', GITLAB_URL='https://gitlab.internal')
    @patch('review.integrations.gitlab.requests.post')
    @patch('review.integrations.gitlab.requests.get')
    def test_create_discussion_posts_a_real_new_line_position(self, get, post):
        get.return_value.json.return_value = []
        get.return_value.headers = {}
        review = Review.objects.create(
            project=self.project,
            mr_iid=7,
            title='line comment',
            base_sha='base-sha',
            start_sha='start-sha',
            head_sha='head-sha',
        )
        comment = LineComment.objects.create(
            review=review,
            file_path='src/review.py',
            new_line=42,
            level='P1',
            title='请保留错误上下文',
            body='建议不要吞掉异常。',
        )
        response = Mock()
        response.json.return_value = {'id': 'discussion-1'}
        post.return_value = response

        result = GitLabClient(self.project).create_discussion(review, comment)

        self.assertEqual(result['id'], 'discussion-1')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['position']['new_path'], 'src/review.py')
        self.assertEqual(payload['position']['new_line'], 42)
        self.assertEqual(payload['position']['head_sha'], 'head-sha')

    @override_settings(GITLAB_TOKEN='gitlab-token', GITLAB_URL='https://gitlab.internal')
    @patch('review.integrations.gitlab.requests.post')
    @patch('review.integrations.gitlab.requests.get')
    def test_create_summary_note_posts_chinese_total_review_to_mr(self, get, post):
        get.return_value.json.return_value = []
        get.return_value.headers = {}
        review = Review.objects.create(
            project=self.project,
            mr_iid=8,
            title='summary note',
            summary='本次 MR 建议补充幂等校验。',
            gitlab_url='https://gitlab.internal/group/remote-demo/-/merge_requests/8',
        )
        response = Mock()
        response.json.return_value = {'id': 123}
        post.return_value = response

        result = GitLabClient(self.project).create_summary_note(review)

        self.assertEqual(result['id'], 123)
        payload = post.call_args.kwargs['json']
        self.assertIn('中文总评', payload['body'])
        self.assertIn('本次 MR 建议补充幂等校验。', payload['body'])

    @override_settings(GITLAB_TOKEN='gitlab-token', GITLAB_URL='https://gitlab.example.com')
    def test_project_without_url_falls_back_to_configured_gitlab_url(self):
        self.project.gitlab_url = ''

        client = GitLabClient(self.project)

        self.assertEqual(client.base_url, 'https://gitlab.example.com')
        self.assertTrue(client.remote_enabled)

    @override_settings(GITLAB_TOKEN='env-token', GITLAB_URL='https://env.gitlab')
    def test_database_gitlab_connection_config_overrides_environment(self):
        self.project.gitlab_url = ''
        GitLabConfig.objects.create(
            base_url='https://db.gitlab',
            api_token='db-token',
        )

        client = GitLabClient(self.project)

        self.assertEqual(client.base_url, 'https://db.gitlab')
        self.assertEqual(client.token, 'db-token')

    @override_settings(GITLAB_TOKEN='env-token', GITLAB_URL='https://env.gitlab')
    def test_project_cannot_send_global_token_to_another_host(self):
        GitLabConfig.objects.create(
            base_url='https://db.gitlab',
            api_token='db-token',
        )
        self.project.gitlab_url = 'https://project.gitlab'

        with self.assertRaisesRegex(ValueError, 'GitLab'):
            GitLabClient(self.project)

    @override_settings(GITLAB_TOKEN='env-token', GITLAB_URL='https://env.gitlab')
    @patch('review.integrations.gitlab.requests.get')
    def test_gitlab_connection_test_returns_safe_user_result(self, get):
        GitLabConfig.objects.create(
            base_url='https://db.gitlab',
            api_token='db-token',
        )
        response = Mock()
        response.json.return_value = {'id': 42, 'username': 'review-bot'}
        get.return_value = response

        from review.integrations.gitlab import test_gitlab_connection

        result = test_gitlab_connection()

        self.assertTrue(result['ok'])
        self.assertEqual(result['username'], 'review-bot')
        self.assertNotIn('db-token', str(result))
        self.assertEqual(get.call_args.kwargs['headers']['PRIVATE-TOKEN'], 'db-token')

    def test_full_gitlab_project_url_is_normalized_for_api_reference(self):
        self.project.gitlab_project_path = 'https://gitlab.example.com/group/remote-demo'

        project_ref = GitLabClient(self.project).project_ref

        self.assertEqual(project_ref, quote('group/remote-demo', safe=''))

    @override_settings(GITLAB_TOKEN='', GITLAB_URL='', REVIEW_ALLOW_TEST_FALLBACK=False)
    def test_gitlab_connection_reports_missing_configuration_without_demo_success(self):
        from review.integrations.gitlab import test_gitlab_connection

        result = test_gitlab_connection()

        self.assertFalse(result['ok'])
        self.assertFalse(result.get('demo', False))
        self.assertIn('GitLab', result['error'])


class LLMIntegrationTests(TestCase):
    @override_settings(CCSWITCH_BASE_URL='', CCSWITCH_API_KEY='', CCSWITCH_MODEL='', REVIEW_ALLOW_TEST_FALLBACK=False)
    def test_model_connection_reports_missing_configuration_without_demo_success(self):
        result = LLMClient(None).test_connection()

        self.assertFalse(result['ok'])
        self.assertFalse(result.get('demo', False))
        self.assertEqual(result['model_ids'], [])
        self.assertIn('模型网关', result['error'])

    @override_settings(
        CCSWITCH_BASE_URL='https://ccswitch.internal/v1',
        CCSWITCH_API_KEY='gateway-secret',
    )
    @patch('review.integrations.llm.requests.get')
    def test_model_connection_exposes_selectable_model_ids(self, get):
        response = Mock()
        response.json.return_value = {
            'data': [
                {'id': 'qwen3-coder-plus'},
                {'id': 'deepseek-v3.1'},
            ],
        }
        get.return_value = response

        result = LLMClient(None).test_connection()

        self.assertEqual(result['model_ids'], ['qwen3-coder-plus', 'deepseek-v3.1'])

    @override_settings(CCSWITCH_BASE_URL='https://ccswitch.internal/v1', REVIEW_ALLOW_TEST_FALLBACK=False)
    @patch('review.integrations.llm.requests.post')
    def test_analyze_calls_openai_compatible_gateway_and_parses_json(self, post):
        config = ModelConfig.objects.create(
            gateway_type='CCSwitch / OpenAI 兼容',
            base_url='https://ccswitch.internal/v1',
            api_key='secret',
            model='Qwen3-Coder',
        )
        response = Mock()
        response.json.return_value = {
            'choices': [{'message': {'content': '{"summary":"中文总评","findings":[]}'}}],
        }
        post.return_value = response

        result = LLMClient(config).analyze('diff --git a/a.py b/a.py', '请用中文输出总评')

        self.assertEqual(result['summary'], '中文总评')
        post.assert_called_once()
        self.assertIn('/chat/completions', post.call_args.args[0])

    @override_settings(
        CCSWITCH_BASE_URL='https://ccswitch.internal/v1',
        CCSWITCH_API_KEY='env-secret',
        CCSWITCH_MODEL='Env-Qwen',
    )
    @patch('review.integrations.llm.requests.post')
    def test_env_gateway_is_used_when_database_model_config_is_missing(self, post):
        response = Mock()
        response.json.return_value = {
            'choices': [{'message': {'content': '{"summary":"环境变量网关","findings":[]}'}}],
        }
        post.return_value = response

        result = LLMClient(None).analyze('diff --git a/a.py b/a.py', '请用中文输出总评')

        self.assertEqual(result['summary'], '环境变量网关')
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], 'https://ccswitch.internal/v1/chat/completions')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer env-secret')
        self.assertEqual(post.call_args.kwargs['json']['model'], 'Env-Qwen')

    @override_settings(CCSWITCH_BASE_URL='https://ccswitch.internal/v1')
    @patch('review.integrations.llm.requests.post')
    def test_builtin_llm_client_does_not_own_engine_selection(self, post):
        config = ModelConfig.objects.create(
            base_url='https://ccswitch.internal/v1',
            api_key='secret',
            model='Qwen3-Coder',
        )
        response = Mock()
        response.json.return_value = {'choices': [{'message': {'content': '{"summary":"中文","findings":[]}'}}]}
        post.return_value = response
        result = LLMClient(config).analyze('diff', '请用中文输出总评')
        self.assertEqual(result['engine'], 'builtin')
        post.assert_called_once()

    @override_settings(CCSWITCH_BASE_URL='https://ccswitch.internal/v1')
    @patch('review.integrations.llm.requests.post')
    def test_builtin_engine_is_reported_for_the_builtin_review_pipeline(self, post):
        config = ModelConfig.objects.create(
            base_url='https://ccswitch.internal/v1',
            api_key='secret',
            model='Qwen3-Coder',
        )
        response = Mock()
        response.json.return_value = {
            'choices': [{'message': {'content': '{"summary":"中文总评","findings":[]}'}}],
        }
        post.return_value = response

        result = LLMClient(config).analyze('diff', '请用中文输出总评')

        self.assertEqual(result['engine'], 'builtin')
        self.assertNotIn('builtin', post.call_args.kwargs['json']['messages'][0]['content'])

    @override_settings(
        CCSWITCH_BASE_URL='https://ccswitch.internal/v1',
        CCSWITCH_API_KEY='env-secret',
        CCSWITCH_MODEL='Env-Qwen',
        REVIEW_ALLOW_TEST_FALLBACK=False,
    )
    @patch('review.integrations.llm.requests.post', side_effect=requests.RequestException('网关不可用'))
    def test_remote_failure_can_be_strict_for_retry_handling(self, _post):
        with self.assertRaises(requests.RequestException):
            LLMClient(None).analyze('diff', '请用中文输出总评')

    @override_settings(CCSWITCH_BASE_URL='https://ccswitch.internal/v1')
    @patch('review.integrations.llm.requests.post')
    def test_remote_prompt_always_requires_chinese_summary(self, post):
        config = ModelConfig.objects.create(
            base_url='https://ccswitch.internal/v1',
            api_key='secret',
            model='Qwen3-Coder',
        )
        response = Mock()
        response.json.return_value = {
            'choices': [{'message': {'content': '{"summary":"中文总评","findings":[]}'}}],
        }
        post.return_value = response

        LLMClient(config).analyze('diff', 'Return the report in English')

        self.assertIn('必须使用中文', post.call_args.kwargs['json']['messages'][0]['content'])

    @override_settings(CCSWITCH_BASE_URL='https://ccswitch.internal/v1', REVIEW_ALLOW_TEST_FALLBACK=False)
    @patch('review.integrations.llm.requests.post')
    def test_remote_result_rejects_findings_without_real_line_positions(self, post):
        config = ModelConfig.objects.create(
            base_url='https://ccswitch.internal/v1',
            api_key='secret',
            model='review-model',
        )
        response = Mock()
        response.json.return_value = {
            'choices': [{'message': {'content': '{"summary":"中文总评","findings":[{"file_path":"src/a.py","title":"缺少校验","body":"请补充"}]}'}}],
        }
        post.return_value = response

        with self.assertRaises(ValueError):
            LLMClient(config).analyze('diff', '请用中文输出总评')


class WeComIntegrationTests(TestCase):
    @patch('review.integrations.wecom.requests.post')
    def test_review_notification_contains_structured_chinese_report(self, post):
        project = Project.objects.create(name='notify-demo', gitlab_project_path='group/notify-demo')
        review = Review.objects.create(
            project=project,
            mr_iid=7,
            title='通知测试',
            summary='本次 MR 建议补充幂等校验。',
            gitlab_url='https://gitlab.internal/group/notify-demo/-/merge_requests/7',
        )
        config = NotificationConfig.objects.create(
            webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test',
        )
        response = Mock()
        response.json.return_value = {'errcode': 0}
        post.return_value = response

        result = WeComClient(config).send_review(review)

        self.assertTrue(result['ok'])
        content = post.call_args.kwargs['json']['markdown']['content']
        self.assertIn('MR：通知测试', content)
        self.assertIn('模型说明和完整建议可在详情查看', content)

    @override_settings(WECOM_WEBHOOK_URL='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=env')
    @patch('review.integrations.wecom.requests.post')
    def test_env_webhook_is_used_when_database_notification_url_is_empty(self, post):
        config = NotificationConfig.objects.create(webhook_url='')
        response = Mock()
        response.json.return_value = {'errcode': 0}
        post.return_value = response

        result = WeComClient(config).send_test()

        self.assertTrue(result['ok'])
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=env')

    @patch('review.integrations.wecom.requests.post')
    def test_improve_notification_honors_suggestion_switch(self, post):
        project = Project.objects.create(name='suggestion-demo', gitlab_project_path='group/suggestion-demo')
        review = Review.objects.create(
            project=project,
            mr_iid=9,
            title='建议通知',
            source=Review.Source.IMPROVE,
            summary='建议抽取重试策略。',
        )
        config = NotificationConfig.objects.create(
            webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test',
            notify_suggestions=False,
        )

        result = WeComClient(config).send_review(review)

        self.assertTrue(result['skipped'])
        post.assert_not_called()
