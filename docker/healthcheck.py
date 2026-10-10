"""Check the HTTP stack, database and all four managed processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen

with urlopen(Request('http://127.0.0.1:8080/api/health/', headers={'Host': 'localhost'}), timeout=5) as response:
    if json.load(response).get('status') != 'ok':
        raise SystemExit('API is unavailable')
status = subprocess.run(['supervisorctl', '-c', '/app/docker/supervisord.conf', 'status'], capture_output=True, text=True, timeout=5)
if status.returncode or not all(any(line.startswith(name + ' ') and 'RUNNING' in line for line in status.stdout.splitlines()) for name in ('api', 'worker', 'sync', 'web')):
    raise SystemExit('A managed process is unavailable')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import django
django.setup()
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute('SELECT 1')
