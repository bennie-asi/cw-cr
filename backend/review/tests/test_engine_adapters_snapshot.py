import io
import json
import os
import tarfile
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.utils import override_settings

from review.engines.adapters import OpenCodeReviewAdapter, PRAgentAdapter, PiAgentAdapter
from review.engines.base import ReviewContext
from review.engines.process import ProcessResult
from review.engines.snapshot import safe_extract_repository_archive


def pi_stream(text, stop_reason='stop'):
    return '\n'.join(json.dumps(event, ensure_ascii=False) for event in (
        {'type': 'session', 'version': 3},
        {'type': 'agent_start'},
        {'type': 'agent_end', 'messages': [{
            'role': 'assistant', 'stopReason': stop_reason,
            'content': [{'type': 'text', 'text': text}],
        }]},
    ))


class ReviewEngineAdapterTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = Path(self.temp.name) / 'repo'
        self.repository.mkdir()
        config = SimpleNamespace(gateway_type='CCSwitch / OpenAI 兼容')
        self.context = ReviewContext(
            project=None, title='修复请求校验', branch='feature/fix', mr_iid=7,
            mr_url='', mode='review', diff='diff --git a/a.py b/a.py', prompt='只评审改动',
            model_config=None, timeout=60, job_id='job-7', repository=self.repository,
        )
        self.client = SimpleNamespace(
            remote_enabled=True, config=config, model='review-model',
            api_key='model-secret-value', base_url='http://127.0.0.1:15721/v1',
        )

    @override_settings(REVIEW_PR_AGENT_PYTHON='')
    @patch('review.engines.adapters.Path.is_file', return_value=True)
    def test_pr_agent_prefers_project_local_isolated_runtime(self, _is_file):
        runtime_bin = Path('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        expected = Path(__file__).resolve().parents[2] / '.runtime' / 'pr-agent' / runtime_bin

        self.assertEqual(PRAgentAdapter.python_executable(), str(expected))

    @patch('review.engines.adapters.OpenCodeReviewAdapter.executable', return_value='ocr')
    @patch('review.engines.adapters.run_engine_process')
    def test_opencode_version_probe_keeps_working_directory_outside_temporary_home(self, run, _executable):
        run.return_value = ProcessResult(
            stdout='open-code-review v1.12.9 (test)\n', stderr='', returncode=0,
        )

        version = OpenCodeReviewAdapter().runtime_version()

        self.assertEqual(version, 'open-code-review v1.12.9 (test)')
        kwargs = run.call_args.kwargs
        self.assertNotEqual(Path(kwargs['cwd']).resolve(), Path(kwargs['env']['HOME']).resolve())
        self.assertEqual(kwargs['env'].get('OCR_NO_UPDATE'), '1')

    @patch('review.engines.adapters.OpenCodeReviewAdapter.runtime_version', return_value='open-code-review v1.12.9')
    @patch('review.engines.adapters.OpenCodeReviewAdapter.executable', return_value='ocr')
    @patch('review.engines.adapters.run_engine_process')
    @patch('review.engines.adapters.LLMClient')
    def test_opencode_review_disables_detached_updater_and_only_returns_structured_output(
        self, llm_client, run, _executable, _version,
    ):
        llm_client.return_value = self.client
        captured = {}

        def return_structured_result(argv, *, cwd, env, timeout):
            captured['config'] = json.loads((Path(env['HOME']) / '.opencodereview' / 'config.json').read_text(encoding='utf-8'))
            captured['prompt'] = (Path(env['HOME']) / 'review-context.md').read_text(encoding='utf-8')
            return ProcessResult(
                stdout=json.dumps({'status': 'completed', 'summary': '本次没有发现问题。', 'comments': []}),
                stderr='', returncode=0,
            )

        run.side_effect = return_structured_result

        result = OpenCodeReviewAdapter().analyze(replace(self.context, timeout=120.0))

        self.assertEqual(result['summary'], '本次没有发现问题。')
        self.assertEqual(run.call_args.kwargs['env'].get('OCR_NO_UPDATE'), '1')
        self.assertNotIn('GITLAB_TOKEN', run.call_args.kwargs['env'])
        self.assertNotIn(self.context.diff, captured['prompt'])
        self.assertEqual(run.call_args.kwargs['timeout'], 480)
        self.assertIs(type(captured['config']['providers']['openai']['timeout_sec']), int)
        self.assertEqual(captured['config']['providers']['openai']['timeout_sec'], 120)
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--timeout') + 1], '8')

    @patch('review.engines.adapters.PRAgentAdapter.runtime_version', return_value='PR-Agent 0.46.0')
    @patch('review.engines.adapters.run_engine_process')
    @patch('review.engines.adapters.LLMClient')
    def test_pr_agent_uses_local_provider_and_consumes_structured_output_only(self, llm_client, run, _version):
        self.client.config.gateway_type = 'Anthropic 兼容'
        llm_client.return_value = self.client

        def write_output(argv, *, cwd, env, timeout):
            self.assertEqual(argv[-2:], ['cwcr-base', 'review'])
            self.assertEqual(env['CONFIG__GIT_PROVIDER'], 'local')
            self.assertEqual(env['CONFIG__USE_REPO_SETTINGS_FILE'], 'false')
            self.assertEqual(env['PUSH_OUTPUTS__CHANNELS'], '["file"]')
            self.assertEqual(env['ANTHROPIC_API_KEY'], 'model-secret-value')
            self.assertEqual(env.get('ANTHROPIC_API_BASE'), 'http://127.0.0.1:15721')
            self.assertEqual(env.get('CONFIG__CUSTOM_MODEL_MAX_TOKENS'), '32000')
            self.assertEqual(env.get('CONFIG__FALLBACK_MODELS'), '[]')
            self.assertIn('逐字', env['PR_CODE_SUGGESTIONS__EXTRA_INSTRUCTIONS'])
            self.assertEqual(timeout, 180)
            self.assertNotIn('GITLAB_TOKEN', env)
            self.assertNotIn('WECOM_WEBHOOK_URL', env)
            output = Path(env['PUSH_OUTPUTS__FILE_PATH'])
            output.write_text(json.dumps({
                'type': 'review',
                'payload': {'review': {'key_issues_to_review': []}},
                'markdown': '## 评审结果',
            }), encoding='utf-8')
            return ProcessResult(stdout='', stderr='', returncode=0)

        run.side_effect = write_output
        result = PRAgentAdapter().analyze(self.context)

        self.assertEqual(result['engine']['id'], 'pr-agent')
        self.assertEqual(result['findings'], [])
        run.assert_called_once()

    @patch('review.engines.adapters.PiAgentAdapter.runtime_version', return_value='0.86.0')
    @patch('review.engines.adapters.PiAgentAdapter.executable', return_value='pi')
    @patch('review.engines.adapters.run_engine_process')
    @patch('review.engines.adapters.LLMClient')
    def test_pi_agent_is_read_only_and_requires_one_json_result(self, llm_client, run, _executable, _version):
        self.client.config.gateway_type = 'Anthropic 兼容'
        llm_client.return_value = self.client

        def return_result(argv, *, cwd, env, timeout):
            model_config = json.loads((Path(env['PI_CODING_AGENT_DIR']) / 'models.json').read_text(encoding='utf-8'))
            custom_model = model_config['providers']['cwcr']
            self.assertEqual(custom_model['api'], 'anthropic-messages')
            self.assertEqual(custom_model['baseUrl'], 'http://127.0.0.1:15721')
            return ProcessResult(
                stdout=pi_stream(json.dumps({'summary': '本次变更没有发现问题。', 'findings': []})),
                stderr='', returncode=0,
            )

        run.side_effect = return_result

        result = PiAgentAdapter().analyze(self.context)

        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--mode') + 1], 'json')
        self.assertIn('read,grep,find,ls', argv)
        self.assertNotIn('write', argv)
        self.assertEqual(result['engine']['id'], 'pi-agent')
        env = run.call_args.kwargs['env']
        self.assertEqual(env['CWCR_ENGINE_API_KEY'], 'model-secret-value')
        self.assertNotIn('GITLAB_TOKEN', env)
        self.assertEqual(run.call_args.kwargs['timeout'], 300)

    @patch('review.engines.adapters.PiAgentAdapter.runtime_version', return_value='0.86.0')
    @patch('review.engines.adapters.PiAgentAdapter.executable', return_value='pi')
    @patch('review.engines.adapters.run_engine_process')
    @patch('review.engines.adapters.LLMClient')
    def test_pi_agent_reformats_invalid_json_once_without_repository_tools(self, llm_client, run, _executable, _version):
        llm_client.return_value = self.client
        invalid = '根据 diff 分析，仅删除空行。\n{"summary":"MR 标题为"调试 CI"，仅删除空行。","findings":[]}'
        valid = {'summary': 'MR 标题为“调试 CI”，仅删除空行。', 'findings': []}
        captured = []

        def return_result(argv, *, cwd, env, timeout):
            captured.append(argv)
            if len(captured) == 1:
                return ProcessResult(pi_stream(invalid), '', 0)
            self.assertIn('--no-tools', argv)
            self.assertNotIn('--tools', argv)
            repair = Path(argv[-1].removeprefix('@')).read_text(encoding='utf-8')
            self.assertIn(json.dumps(invalid, ensure_ascii=False), repair)
            self.assertIn('不得新增、删除或改写问题', repair)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 300)
            return ProcessResult(pi_stream(json.dumps(valid)), '', 0)

        run.side_effect = return_result
        result = PiAgentAdapter().analyze(self.context)

        self.assertEqual(result['summary'], valid['summary'])
        self.assertEqual(result['findings'], [])
        self.assertEqual(len(captured), 2)

    @patch('review.engines.adapters.PiAgentAdapter.runtime_version', return_value='0.86.0')
    @patch('review.engines.adapters.PiAgentAdapter.executable', return_value='pi')
    @patch('review.engines.adapters.run_engine_process')
    @patch('review.engines.adapters.LLMClient')
    def test_pi_agent_does_not_retry_incomplete_or_invalid_structured_results(self, llm_client, run, _executable, _version):
        llm_client.return_value = self.client
        for text, reason in (('{"summary":"总评","findings":[]}', 'length'),
                             ('{"summary":"总评","findings":[]}', 'error'),
                             ('{"summary":"总评","findings":[]}', 'toolUse'),
                             ('{"summary":"总评","findings":[{"file_path":"a.py"}]}', 'stop')):
            with self.subTest(reason=reason, text=text):
                run.reset_mock()
                run.return_value = ProcessResult(pi_stream(text, reason), '', 0)
                with self.assertRaises(ValueError):
                    PiAgentAdapter().analyze(self.context)
                run.assert_called_once()

    @patch('review.engines.adapters.PiAgentAdapter.runtime_version', return_value='0.86.0')
    @patch('review.engines.adapters.PiAgentAdapter.executable', return_value='pi')
    @patch('review.engines.adapters.run_engine_process')
    @patch('review.engines.adapters.LLMClient')
    def test_pi_agent_stops_after_a_failed_format_retry(self, llm_client, run, _executable, _version):
        llm_client.return_value = self.client
        run.return_value = ProcessResult(pi_stream('这不是 JSON。'), '', 0)

        with self.assertRaisesRegex(ValueError, '格式恢复后仍未返回合法 JSON'):
            PiAgentAdapter().analyze(self.context)

        self.assertEqual(run.call_count, 2)


class RepositoryArchiveSafetyTests(SimpleTestCase):
    def make_archive(self, members):
        archive_file = tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False)
        archive_file.close()
        self.addCleanup(lambda: Path(archive_file.name).unlink(missing_ok=True))
        with tarfile.open(archive_file.name, 'w:gz') as archive:
            for name, content, kind in members:
                info = tarfile.TarInfo(name)
                if kind == 'symlink':
                    info.type = tarfile.SYMTYPE
                    info.linkname = '../../outside'
                    archive.addfile(info)
                else:
                    data = content.encode()
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
        return archive_file.name

    def test_extracts_regular_single_root_archive(self):
        archive = self.make_archive([('project-a/src/a.py', 'print("ok")', 'file')])
        with tempfile.TemporaryDirectory() as destination:
            safe_extract_repository_archive(archive, destination)
            self.assertEqual((Path(destination) / 'src' / 'a.py').read_text(), 'print("ok")')

    def test_rejects_parent_traversal_without_writing_outside_destination(self):
        archive = self.make_archive([('project-a/../escape.txt', 'unsafe', 'file')])
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / 'snapshot'
            with self.assertRaises(ValueError):
                safe_extract_repository_archive(archive, destination)
            self.assertFalse((Path(root) / 'escape.txt').exists())

    def test_rejects_symbolic_links_even_when_archive_root_is_valid(self):
        archive = self.make_archive([('project-a/src/link', '', 'symlink')])
        with tempfile.TemporaryDirectory() as destination:
            with self.assertRaises(ValueError):
                safe_extract_repository_archive(archive, destination)
