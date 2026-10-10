from django.test import SimpleTestCase
from django.conf import settings
import os
import subprocess
import sys


class ExecutionLockTests(SimpleTestCase):
    def test_os_releases_lock_after_owner_process_exits(self):
        from review.services.execution_locks import resource_lock, ResourceBusy
        code = "from review.services.execution_locks import resource_lock; import sys;\nwith resource_lock('test:process-death'):\n print('locked', flush=True)\n sys.stdin.read()"
        process = subprocess.Popen([sys.executable, '-c', code], cwd=settings.BASE_DIR,
            env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings'}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            with self.assertRaises(ResourceBusy):
                with resource_lock('test:process-death'):
                    pass
            process.kill()
            process.wait(timeout=10)
            with resource_lock('test:process-death'):
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
    def test_same_resource_cannot_be_entered_twice(self):
        from review.services.execution_locks import resource_lock, ResourceBusy
        with resource_lock('test:same'):
            with self.assertRaises(ResourceBusy):
                with resource_lock('test:same'):
                    self.fail('concurrent entry')

    def test_other_resources_and_released_locks_are_available(self):
        from review.services.execution_locks import resource_lock
        with resource_lock('test:first'):
            with resource_lock('test:second'):
                pass
        with resource_lock('test:first'):
            pass
