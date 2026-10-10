import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'bootstrap.py'


class BootstrapTests(unittest.TestCase):
    def run_bootstrap(self, key, **extra):
        environment = {**os.environ, 'DJANGO_SECRET_KEY': '', 'DJANGO_SECRET_KEY_FILE': str(key), **extra}
        return subprocess.run([sys.executable, str(SCRIPT)], env=environment, capture_output=True, text=True)

    def test_generates_secret_and_preserves_it_on_restart_without_logging_it(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'secrets' / 'django.key'
            result = self.run_bootstrap(key)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = key.read_text().strip()
            self.assertGreaterEqual(len(original), 50)
            self.assertNotIn(original, result.stdout + result.stderr)
            result = self.run_bootstrap(key)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(key.read_text().strip(), original)
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)

    def test_environment_secret_does_not_create_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'django.key'
            result = self.run_bootstrap(key, DJANGO_SECRET_KEY='explicit-secret-' + 'x' * 64)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(key.exists())

    def test_empty_existing_file_is_rejected_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'django.key'
            key.write_text('')
            result = self.run_bootstrap(key)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(key.read_text(), '')
