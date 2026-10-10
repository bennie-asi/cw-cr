from __future__ import annotations
from review.integrations.errors import safe_error

import json
import re

import requests
from django.conf import settings

from review.integrations.errors import IntegrationNotConfiguredError
from review.models import ModelConfig


TEST_FINDINGS = [
    {
        'file_path': 'src/service/review.ts',
        'new_line': 118,
        'level': 'P1',
        'title': '异常路径会吞掉上下文',
        'body': '这里直接返回空数组会让上层误判为“没有问题”。建议保留 provider 的错误信息并进入重试队列。',
    },
    {
        'file_path': 'src/service/webhook.ts',
        'new_line': 64,
        'level': 'P1',
        'title': '缺少幂等键校验',
        'body': 'GitLab 重试 webhook 时可能重复创建评审任务，建议使用 event_uuid 做去重。',
    },
    {
        'file_path': 'src/types/review.ts',
        'new_line': 12,
        'level': 'P2',
        'title': '可以抽取重试策略',
        'body': '当前重试间隔写死在调用方，后续可复用统一的指数退避策略。',
    },
]


def _model_ids(models: object) -> list[str]:
    if not isinstance(models, list):
        return []
    values: list[str] = []
    for item in models:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = str(item.get('id') or item.get('name') or item.get('model') or '').strip()
        else:
            value = ''
        if value and value not in values:
            values.append(value)
    return values


def _gateway_auth_error(response) -> str:
    """Translate CCSwitch provider-auth failures without exposing payloads."""
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return ''
    error = payload.get('error') if isinstance(payload, dict) else None
    if not isinstance(error, dict) or error.get('code') != 'cc_switch_auth_error':
        return ''
    provider = str(error.get('provider') or '').strip()
    suffix = f'（{provider}）' if provider else ''
    return f'CCSwitch 上游账号认证失败{suffix}，请在 CCSwitch 中重新登录对应上游。'


class LLMClient:
    def __init__(self, config: ModelConfig | None = None, timeout: float | None = None):
        self.config = config
        configured_timeout = config.request_timeout_s if config else settings.LLM_REQUEST_TIMEOUT
        try:
            configured_timeout = max(10, min(600, float(configured_timeout)))
        except (TypeError, ValueError):
            configured_timeout = 120
        self.timeout = timeout if timeout is not None else configured_timeout

    @property
    def base_url(self) -> str:
        value = self.config.base_url if self.config and self.config.base_url else settings.CCSWITCH_BASE_URL
        return value.rstrip('/')

    @property
    def api_key(self) -> str:
        if self.config and self.config.api_key:
            return self.config.api_key
        # Environment credentials belong to that gateway, not every project
        # URL. This also protects older custom configurations with empty keys.
        if self.base_url != settings.CCSWITCH_BASE_URL.rstrip('/'):
            return ''
        return settings.CCSWITCH_API_KEY

    @property
    def model(self) -> str:
        return self.config.model if self.config and self.config.model else settings.CCSWITCH_MODEL

    @property
    def engine(self) -> str:
        return 'builtin'

    @property
    def enabled(self) -> bool:
        return self.config is None or self.config.enabled

    @property
    def connection_enabled(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key)

    @property
    def remote_enabled(self) -> bool:
        return bool(self.connection_enabled and self.model)

    def analyze(self, diff: str, prompt: str, mode: str = 'review') -> dict:
        # Fail closed if an unsupported engine was written outside the API.
        _ = self.engine
        if not self.remote_enabled:
            if settings.REVIEW_ALLOW_TEST_FALLBACK:
                return self._test_fallback_result(mode)
            raise IntegrationNotConfiguredError(
                '模型网关未配置完整连接信息，请在模型与引擎页面填写 Base URL、API Key 和模型 ID'
            )
        try:
            return self._remote_result(diff, prompt, mode)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                raise
            return self._test_fallback_result(mode)

    def test_connection(self) -> dict:
        engine = self.engine
        if not self.connection_enabled:
            return {
                'ok': False,
                'model': self.model,
                'engine': engine,
                'model_ids': [],
                'error': '模型网关未配置完整连接信息（需要 Base URL 和 API Key）',
            }
        try:
            response = requests.get(
                f'{self.base_url}/models',
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            models = payload.get('data', payload.get('models', [])) if isinstance(payload, dict) else []
            return {
                'ok': True,
                'model': self.model,
                'engine': engine,
                'models': models,
                'model_ids': _model_ids(models),
            }
        except requests.HTTPError as exc:
            auth_error = _gateway_auth_error(exc.response)
            return {'ok': False, 'model': self.model, 'engine': engine, 'model_ids': [], 'error': auth_error or safe_error(exc)}
        except (requests.RequestException, ValueError, TypeError) as exc:
            return {'ok': False, 'model': self.model, 'engine': engine, 'model_ids': [], 'error': safe_error(exc)}

    def test_inference(self) -> dict:
        """Probe generation with synthetic input; never send repository content."""
        _ = self.engine
        result = {'ok': False, 'probe': True, 'model': self.model}
        if not self.remote_enabled:
            return {**result, 'error': '验证模型调用需要 Base URL、API Key 和模型 ID'}
        try:
            self._remote_result('diff --git a/probe.txt b/probe.txt\n+++ b/probe.txt\n@@ -0,0 +1 @@\n+连接测试', '这是一条连接测试，请返回 summary 为连接成功、findings 为空数组的 JSON。', 'review')
            return {**result, 'ok': True}
        except requests.HTTPError as exc:
            response = exc.response
            code = response.status_code if response is not None else None
            auth_error = _gateway_auth_error(response) if response is not None else ''
            if auth_error:
                return {**result, 'status_code': code, 'error': auth_error}
            unavailable = response is not None and 'no available accounts' in response.text.lower()
            if code == 429:
                return {**result, 'status_code': code, 'error': '模型网关限流或额度不足（HTTP 429），请检查上游配额后再试。'}
            return {**result, 'status_code': code, 'error': '模型网关没有可用上游账号（No available accounts），请检查网关账号池。' if unavailable else f'模型调用失败（HTTP {code}），请检查模型权限和网关状态。'}
        except requests.RequestException:
            return {**result, 'error': '模型调用连接失败或超时，请检查网关网络。'}
        except (ValueError, KeyError, TypeError, IndexError):
            return {**result, 'error': '模型响应不符合评审 JSON 格式。'}

    def _remote_result(self, diff: str, prompt: str, mode: str) -> dict:
        from review.services.finding_locations import numbered_diff
        diff = numbered_diff(diff)
        schema = '仅输出 JSON 对象，不要 Markdown。结构为 {"summary":"中文总评","findings":[{"file_path":"真实文件路径","new_line":1,"anchor_code":"该行完整原代码","level":"P0/P1/P2","title":"中文标题","body":"中文问题与建议"}]}。删除行使用 old_line 替代 new_line。没有问题时 findings 为空数组。只报告 diff 支持的问题，不要编造。'
        schema += ('每条问题必须提供 anchor_code，逐字复制锚点所在侧的一整行原代码（保留缩进，不含行号或 diff 前缀）。'
                   'new_line/old_line 从该行的 new=/old= 标注读取，不要使用 diff 显示序号。'
                   'anchor_code 是问题涉及的原代码，不是建议替换代码，不能使用省略号。')
        schema += ('能够给出确定修复时，优先为 finding 增加 suggestion 对象：'
                   '{"start_line":新文件起始行整数,"end_line":新文件结束行整数,'
                   '"original_code":"范围内完整原代码","replacement_code":"完整替换代码"}。'
                   '行号是文件实际行号，不是 diff 展示序号。new_line 必须在替换范围内；'
                   '只替换同一文件同一 hunk 展示的连续新侧行，锚点前后各最多100行。'
                   'original_code 保留每行缩进和空行，不含 diff 的加号/空格前缀；用换行连接，不额外添加末尾换行。'
                   'replacement_code 必须完整可替换，不得省略代码或用省略号占位。'
                   '删除侧、跨文件修改、不能确定正确修复时 suggestion 为 null。'
                   'body 只写中文解释，不要在 body 中自行输出 suggestion 代码块。')
        if self.config and self.config.gateway_type == 'Anthropic 兼容':
            response = requests.post(
                f'{self.base_url}/messages',
                headers={'x-api-key': self.api_key, 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'},
                json={
                    'model': self.model, 'max_tokens': 8192,
                    'system': f'总评、问题标题和建议必须使用中文。\n{schema}\n{prompt}',
                    'messages': [{'role': 'user', 'content': f'请审查代码变更，模式：{mode}\n\n{diff}'}],
                }, timeout=self.timeout,
            )
            response.raise_for_status()
            content = '\n'.join(block.get('text', '') for block in response.json().get('content', []) if block.get('type') == 'text')
            return self._parse_result(content)
        response = requests.post(
            f'{self.base_url}/chat/completions',
            headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'},
            json={
                'model': self.model,
                'temperature': 0.1,
                'messages': [
                    {'role': 'system', 'content': f'无论项目自定义提示词如何变化，summary、行级评论标题和正文都必须使用中文；每条 finding 必须包含真实的 file_path 和 new_line 或 old_line，不能只输出总报告。\n\n{prompt}\n\n请保留结构化 findings 与中文 summary 输出。'},
                    {'role': 'user', 'content': f'{schema}\n请审查以下代码变更，模式：{mode}\n\n{diff}'},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        return self._parse_result(content)

    def _parse_result(self, content: str) -> dict:
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content.strip())
        result = json.loads(content)
        if 'findings' not in result or 'summary' not in result:
            raise ValueError('LLM response missing findings or summary')
        if not isinstance(result['findings'], list):
            raise ValueError('LLM findings must be a list')
        normalized_findings = []
        for index, finding in enumerate(result['findings']):
            if not isinstance(finding, dict):
                raise ValueError(f'LLM finding {index} must be an object')
            finding = dict(finding)
            if finding.get('new_line') is None and finding.get('old_line') is None and finding.get('line') is not None:
                finding['new_line'] = finding['line']
            file_path = str(finding.get('file_path') or '').strip()
            line = finding.get('new_line') if finding.get('new_line') is not None else finding.get('old_line')
            try:
                if type(line) is not int and not (isinstance(line, str) and re.fullmatch(r'[1-9]\d*', line)):
                    raise ValueError('line must be an integer')
                line = int(line)
            except (TypeError, ValueError):
                line = 0
            if not file_path or line <= 0:
                raise ValueError(f'LLM finding {index} must include file_path and a positive new_line or old_line')
            finding['file_path'] = file_path
            if finding.get('new_line') is not None:
                finding['new_line'] = line
            else:
                finding['old_line'] = line
            normalized_findings.append(finding)
        result['findings'] = normalized_findings
        result['engine'] = self.engine
        return result

    def _test_fallback_result(self, mode: str) -> dict:
        """Return deterministic data only for the isolated Django test suite."""
        findings = TEST_FINDINGS if mode == 'review' else TEST_FINDINGS[1:]
        return {
            'summary': (
                '测试评审结果用于验证行级评论、总评和门禁数据链路。\n\n'
                f'测试回退引擎生成了 {len(findings)} 条固定建议。'
            ),
            'findings': findings,
            'engine': f'test-fallback-{self.engine}',
        }
