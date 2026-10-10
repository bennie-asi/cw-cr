from unittest.mock import Mock, patch
import requests
from django.test import TestCase, override_settings
from review.integrations.llm import LLMClient
from review.models import ModelConfig
from review.tests.auth_helpers import admin_client


@override_settings(CCSWITCH_BASE_URL='', CCSWITCH_API_KEY='', CCSWITCH_MODEL='')
class ModelProbeTests(TestCase):
    @patch('review.integrations.llm.requests.post')
    def test_anthropic_proxy_uses_messages_and_parses_text_blocks(self, post):
        self.config.gateway_type = 'Anthropic 兼容'
        post.return_value.json.return_value = {'content': [{'type': 'text', 'text': '{"summary":"连接成功","findings":[]}'}]}
        result = LLMClient(self.config).test_inference()
        self.assertTrue(result['ok'])
        self.assertTrue(post.call_args.args[0].endswith('/messages'))
        self.assertEqual(post.call_args.kwargs['headers']['x-api-key'], self.config.api_key)
        self.assertNotIn('Authorization', post.call_args.kwargs['headers'])

    def setUp(self):
        self.client = admin_client()
        self.config = ModelConfig.objects.create(base_url='https://gateway.example/v1', api_key='never-expose-me', model='test-model')

    @patch('review.integrations.llm.requests.post')
    def test_success_requires_generation_not_just_a_model_list(self, post):
        post.return_value = Mock()
        post.return_value.json.return_value = {'choices': [{'message': {'content': '{"summary":"连接成功","findings":[]}'}}]}
        response = self.client.post('/api/model-config/test/', data={'probe': True}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['probe'])
        self.assertTrue(post.call_args.args[0].endswith('/chat/completions'))

    @patch('review.integrations.llm.requests.post')
    def test_unavailable_pool_is_reported_without_secrets(self, post):
        response = requests.Response(); response.status_code = 503
        response._content = b'{"error":{"message":"No available accounts"}}'
        post.side_effect = requests.HTTPError(response=response)
        result = LLMClient(self.config).test_inference()
        self.assertFalse(result['ok'])
        self.assertIn('No available accounts', result['error'])
        self.assertNotIn('never-expose-me', str(result))

    @patch('review.integrations.llm.requests.post')
    def test_ccswitch_upstream_auth_error_is_actionable_without_secrets(self, post):
        response = requests.Response(); response.status_code = 401
        response._content = b'{"error":{"code":"cc_switch_auth_error","provider":"OpenAI Official -0418","message":"re-login required"}}'
        post.side_effect = requests.HTTPError(response=response)

        result = LLMClient(self.config).test_inference()

        self.assertFalse(result['ok'])
        self.assertIn('重新登录', result['error'])
        self.assertIn('OpenAI Official -0418', result['error'])
        self.assertNotIn('never-expose-me', str(result))

    @patch('review.integrations.llm.requests.post')
    def test_missing_configuration_does_not_make_request(self, post):
        self.assertFalse(LLMClient(None).test_inference()['ok'])
        post.assert_not_called()
