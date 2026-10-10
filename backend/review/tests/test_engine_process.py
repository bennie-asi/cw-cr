import os
import sys
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase

from review.engines.process import (
    EngineOutputTooLarge,
    EngineProcessError,
    EngineProcessTimeout,
    isolated_environment,
    run_engine_process,
)


class EngineProcessTests(SimpleTestCase):
    def test_environment_is_allowlisted_and_uses_private_home(self):
        with patch.dict(os.environ, {'GITLAB_TOKEN': 'gitlab-secret', 'WECOM_WEBHOOK_URL': 'wecom-secret'}, clear=False):
            env = isolated_environment({'CWCR_ENGINE_API_KEY': 'model-secret'}, home=tempfile.gettempdir())
        self.assertNotIn('GITLAB_TOKEN', env)
        self.assertNotIn('WECOM_WEBHOOK_URL', env)
        self.assertEqual(env['CWCR_ENGINE_API_KEY'], 'model-secret')
        self.assertEqual(env['USERPROFILE'], tempfile.gettempdir())

    def test_nonzero_exit_is_reported_without_leaking_model_key(self):
        with self.assertRaises(EngineProcessError) as caught:
            run_engine_process(
                [sys.executable, '-c', 'import os,sys;print(os.environ["CWCR_ENGINE_API_KEY"],file=sys.stderr);sys.exit(2)'],
                cwd=tempfile.gettempdir(), env={'CWCR_ENGINE_API_KEY': 'model-super-secret'}, timeout=3,
            )
        self.assertNotIn('model-super-secret', str(caught.exception))
        self.assertIn('status 2', str(caught.exception))

    def test_timeout_terminates_process_tree(self):
        with self.assertRaises(EngineProcessTimeout):
            run_engine_process(
                [sys.executable, '-c', 'import time;time.sleep(10)'],
                cwd=tempfile.gettempdir(), env={}, timeout=0.1,
            )

    def test_output_limit_terminates_instead_of_buffering_unbounded_output(self):
        with self.assertRaises(EngineOutputTooLarge):
            run_engine_process(
                [sys.executable, '-c', 'print("x" * 2000)'],
                cwd=tempfile.gettempdir(), env={}, timeout=3, max_stdout_bytes=512,
            )
