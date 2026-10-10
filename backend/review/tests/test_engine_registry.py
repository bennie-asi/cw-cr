from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from review.engines.registry import verify_engine, verification_status
from review.models import Project, ReviewEngine, ReviewEngineConfig


class EngineRegistryVerificationTests(TestCase):
    def test_changed_model_requires_explicit_reverification_reason(self):
        ReviewEngineConfig.objects.update_or_create(pk=1, defaults={'verified_engines': {'pi-agent|global': {
            'engine_version': '0.86.0', 'model_fingerprint': 'previous-model', 'verified_at': '2026-09-23',
        }}})
        with (
            patch('review.engines.registry.runtime_status', return_value={'installed': True, 'version': '0.86.0', 'error': ''}),
            patch('review.engines.registry.LLMClient', return_value=SimpleNamespace(remote_enabled=True)),
            patch('review.engines.registry.model_connection_fingerprint', return_value='new-model'),
        ):
            result = verification_status(ReviewEngine.PI_AGENT)
        self.assertFalse(result['selectable'])
        self.assertIn('已变更', result['reason'])
        self.assertIn('重新验证', result['reason'])

    def test_engine_verification_grounds_suggestion_source_in_the_verified_diff(self):
        project = Project.objects.create(name='probe project', gitlab_project_path='team/probe-project')
        ReviewEngineConfig.objects.get_or_create(pk=1)
        diff = (
            'diff --git a/probe.py b/probe.py\n'
            '--- a/probe.py\n'
            '+++ b/probe.py\n'
            '@@ -1,2 +1,2 @@\n'
            ' def probe(value):\n'
            '-    return value\n'
            '+    return value.name\n'
        )
        analysis_result = {
            'summary': '合成验证。',
            'engine': {'id': ReviewEngine.PI_AGENT, 'version': '0.86.0'},
            'findings': [{
                'file_path': 'probe.py', 'new_line': 2, 'level': 'P2',
                'title': '问题', 'body': '说明。',
                'suggestion': {
                    'start_line': 2, 'end_line': 2,
                    'original_code': '    return value',
                    'replacement_code': '    return value.name or ""',
                },
            }],
        }
        adapter = SimpleNamespace(analyze=lambda context: analysis_result)

        @contextmanager
        def synthetic_snapshot():
            yield SimpleNamespace(repository=Path('.'), base_ref='base', head_ref='head'), diff

        with (
            patch('review.engines.registry.runtime_status', return_value={'installed': True, 'version': '0.86.0', 'error': ''}),
            patch('review.engines.registry.LLMClient', return_value=SimpleNamespace(remote_enabled=True, timeout=60)),
            patch('review.engines.registry.model_connection_fingerprint', return_value='probe-fingerprint'),
            patch('review.engines.registry.create_probe_snapshot', synthetic_snapshot),
            patch('review.engines.registry.ADAPTERS', {ReviewEngine.PI_AGENT: adapter}),
        ):
            result = verify_engine(ReviewEngine.PI_AGENT)

        self.assertTrue(result['ok'], result)
        self.assertEqual(
            analysis_result['findings'][0]['suggestion']['original_code'],
            '    return value.name',
        )
        config = ReviewEngineConfig.objects.get(pk=1)
        self.assertIn(f'{ReviewEngine.PI_AGENT}|global', config.verified_engines)
        with (
            patch('review.engines.registry.runtime_status', return_value={'installed': True, 'version': '0.86.0', 'error': ''}),
            patch('review.engines.registry.LLMClient', return_value=SimpleNamespace(remote_enabled=True)),
            patch('review.engines.registry.model_connection_fingerprint', return_value='probe-fingerprint'),
        ):
            self.assertTrue(verification_status(ReviewEngine.PI_AGENT, project=project)['selectable'])
