from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone

from django.conf import settings

from review.engines.adapters import ADAPTERS
from review.engines.base import ReviewContext
from review.engines.snapshot import create_probe_snapshot
from review.integrations.errors import safe_error
from review.integrations.llm import LLMClient
from review.models import ModelConfig, Project, ProjectPolicy, ReviewEngine, ReviewEngineConfig
from review.services.diff_positions import ground_suggestion_originals, validate_findings, validate_suggestions


ENGINE_LABELS = {
    ReviewEngine.BUILTIN: '智审台内置评审',
    ReviewEngine.PR_AGENT: 'PR-Agent',
    ReviewEngine.OPEN_CODE_REVIEW: 'OpenCodeReview',
    ReviewEngine.PI_AGENT: 'Pi Agent',
}
ENGINE_CAPABILITIES = {
    ReviewEngine.BUILTIN: ['行级评论', '中文总评', '建议替换'],
    ReviewEngine.PR_AGENT: ['结构化行级问题', '改进建议', '智审台统一发布'],
    ReviewEngine.OPEN_CODE_REVIEW: ['结构化行级问题', '代码替换建议', '智审台统一发布'],
    ReviewEngine.PI_AGENT: ['只读代码上下文', '结构化行级问题', '建议替换', '智审台统一发布'],
}
_RUNTIME_CACHE: dict[str, tuple[float, dict]] = {}
_RUNTIME_TTL_SECONDS = 30


def resolve_model_config(project: Project | None = None) -> ModelConfig | None:
    if project is not None and not project.model_inherit:
        return ModelConfig.objects.filter(project=project).first()
    return ModelConfig.objects.filter(scope=ModelConfig.Scope.GLOBAL, project__isnull=True).first()


def get_platform_engine_config() -> ReviewEngineConfig:
    config, _ = ReviewEngineConfig.objects.get_or_create(pk=1)
    return config


def effective_engine(project: Project | None = None) -> str:
    if project is not None and not project.engine_inherit:
        policy = ProjectPolicy.objects.filter(project=project).first()
        return policy.engine if policy else ReviewEngine.BUILTIN
    return get_platform_engine_config().default_engine


def _verification_key(engine: str) -> str:
    return f'{engine}|global'


def model_connection_fingerprint(config: ModelConfig | None) -> str:
    client = LLMClient(config)
    values = '\0'.join((
        client.base_url,
        client.model,
        client.config.gateway_type if client.config else 'environment',
        str(client.timeout),
        client.api_key,
    ))
    return hmac.new(str(settings.SECRET_KEY).encode(), values.encode(), hashlib.sha256).hexdigest()


def runtime_status(engine: str, refresh: bool = False) -> dict:
    if engine == ReviewEngine.BUILTIN:
        return {'installed': True, 'version': 'builtin', 'error': ''}
    cached = _RUNTIME_CACHE.get(engine)
    now = time.monotonic()
    if cached and not refresh and now - cached[0] < _RUNTIME_TTL_SECONDS:
        return dict(cached[1])
    adapter = ADAPTERS.get(engine)
    if adapter is None:
        result = {'installed': False, 'version': '', 'error': '未知的评审引擎'}
    else:
        try:
            version = adapter.runtime_version()
            result = {'installed': True, 'version': version, 'error': ''}
        except Exception as exc:
            result = {'installed': False, 'version': '', 'error': safe_error(exc)}
    _RUNTIME_CACHE[engine] = (now, result)
    return dict(result)


def verification_status(engine: str, project: Project | None = None, model_config: ModelConfig | None = None) -> dict:
    runtime = runtime_status(engine)
    if engine == ReviewEngine.BUILTIN:
        return {
            **runtime, 'verified': True, 'selectable': True,
            'status': 'ready', 'reason': '内置引擎可用', 'verified_at': None,
        }
    if not runtime['installed']:
        return {
            **runtime, 'verified': False, 'selectable': False,
            'status': 'not-installed', 'reason': runtime['error'] or '评审引擎运行时未安装', 'verified_at': None,
        }
    # Engine approval belongs to the platform. Projects may override their
    # model connection, but that does not create an independent engine catalog.
    model_config = resolve_model_config()
    client = LLMClient(model_config)
    if not client.remote_enabled:
        return {
            **runtime, 'verified': False, 'selectable': False,
            'status': 'model-not-configured', 'reason': '请先配置可调用的模型连接', 'verified_at': None,
        }
    config = get_platform_engine_config()
    record = config.verified_engines.get(_verification_key(engine))
    is_current = isinstance(record, dict) and (
        record.get('engine_version') == runtime['version']
        and record.get('model_fingerprint') == model_connection_fingerprint(model_config)
    )
    return {
        **runtime,
        'verified': bool(is_current),
        'selectable': bool(is_current),
        'status': 'ready' if is_current else 'unverified',
        'reason': '' if is_current else (
            '模型连接或运行时已变更，请平台管理员在平台模型与引擎页面重新验证'
            if record else '请平台管理员在平台模型与引擎页面运行验证'
        ),
        'verified_at': record.get('verified_at') if is_current else None,
    }


def is_engine_selectable(engine: str, project: Project | None = None, model_config: ModelConfig | None = None) -> bool:
    try:
        return bool(verification_status(engine, project, model_config)['selectable'])
    except Exception:
        return False


def engine_catalog(project: Project | None = None, model_config: ModelConfig | None = None) -> list[dict]:
    values = []
    for engine in ReviewEngine.values:
        status = verification_status(engine, project, model_config)
        values.append({
            'id': engine,
            'label': ENGINE_LABELS[engine],
            'capabilities': ENGINE_CAPABILITIES[engine],
            'availability': status['status'],
            'installed': status['installed'],
            'version': status['version'],
            'verified': status['verified'],
            'selectable': status['selectable'],
            'reason': status['reason'],
            'verified_at': status['verified_at'],
        })
    return values


def verify_engine(engine: str, project: Project | None = None, model_config: ModelConfig | None = None) -> dict:
    if engine not in ReviewEngine.values:
        return {'ok': False, 'engine': engine, 'error': '未知的评审引擎'}
    if project is not None:
        return {'ok': False, 'engine': engine, 'error': '引擎只能由平台统一验证'}
    model_config = model_config if model_config is not None else resolve_model_config()
    client = LLMClient(model_config)
    if not client.remote_enabled:
        return {'ok': False, 'engine': engine, 'error': '请先配置可调用的模型连接'}
    try:
        if engine == ReviewEngine.BUILTIN:
            probe = client.test_inference()
            if not probe.get('ok'):
                return {'ok': False, 'engine': engine, 'error': probe.get('error') or '内置引擎推理探测失败'}
            version = 'builtin'
        else:
            adapter = ADAPTERS.get(engine)
            if adapter is None:
                return {'ok': False, 'engine': engine, 'error': '该评审引擎尚未实现'}
            with create_probe_snapshot() as (snapshot, diff):
                context = ReviewContext(
                    project=project,
                    title='智审台引擎连接测试',
                    branch='cwcr-engine-probe',
                    mr_iid=0,
                    mr_url='',
                    mode='review',
                    diff=diff,
                    prompt=(
                        '这是智审台的合成连接探测，不是真实项目代码。检查新增行是否有明确問題，'
                        '如无问题可以返回空 findings；必须严格使用简体中文，并返回结构化结果。'
                    ),
                    model_config=model_config,
                    timeout=min(client.timeout, 180),
                    job_id='engine-probe',
                    repository=snapshot.repository,
                    base_ref=snapshot.base_ref,
                    head_ref=snapshot.head_ref,
                )
                result = adapter.analyze(context)
                result['findings'] = ground_suggestion_originals(diff, result.get('findings', []))
                validate_findings(diff, result.get('findings', []))
                validate_suggestions(diff, result.get('findings', []))
                version = result.get('engine', {}).get('version') or adapter.runtime_version()
        config = get_platform_engine_config()
        records = dict(config.verified_engines or {})
        records[_verification_key(engine)] = {
            'engine_version': version,
            'model_fingerprint': model_connection_fingerprint(model_config),
            'verified_at': datetime.now(timezone.utc).isoformat(),
        }
        config.verified_engines = records
        config.save(update_fields=('verified_engines', 'updated_at'))
        _RUNTIME_CACHE.pop(engine, None)
        return {'ok': True, 'engine': engine, 'version': version}
    except Exception as exc:
        config = get_platform_engine_config()
        records = dict(config.verified_engines or {})
        records.pop(_verification_key(engine), None)
        config.verified_engines = records
        config.save(update_fields=('verified_engines', 'updated_at'))
        _RUNTIME_CACHE.pop(engine, None)
        return {'ok': False, 'engine': engine, 'error': safe_error(exc)}
