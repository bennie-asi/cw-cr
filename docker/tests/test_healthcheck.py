import io
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


class HealthcheckTests(unittest.TestCase):
    def execute(self, names):
        output = '\n'.join(f'{name} RUNNING pid 1, uptime 0:01:00' for name in names)
        connection = MagicMock()
        modules = {'django': SimpleNamespace(setup=lambda: None), 'django.db': SimpleNamespace(connection=connection)}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"status":"ok"}')), \
             patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=output)), \
             patch.dict(sys.modules, modules):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'healthcheck.py'))

    def test_dead_synchronizer_makes_container_unhealthy(self):
        with self.assertRaisesRegex(SystemExit, 'managed process'):
            self.execute(('api', 'worker', 'web'))

    def test_all_four_processes_make_container_healthy(self):
        self.execute(('api', 'worker', 'sync', 'web'))
