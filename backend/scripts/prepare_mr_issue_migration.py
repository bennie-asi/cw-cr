"""Back up the local application DB before applying the MR issue migration."""
import json
import os
from datetime import datetime
from pathlib import Path
import sqlite3
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from review.models import ReviewJob

source = Path(settings.DATABASES['default']['NAME']).resolve()
assert source == (BACKEND / 'db.sqlite3').resolve() and source.exists(), 'Unexpected real database'
assert not ReviewJob.objects.filter(status__in=('queued', 'running')).exists(), 'Review queue is busy'
runtime = BACKEND / '.runtime' / 'functional-e2e-20261008'
backup = runtime / ('real-before-mr-issues-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
assert backup.resolve().parent == runtime.resolve() and not backup.exists()
with sqlite3.connect(source) as original, sqlite3.connect(backup) as destination:
    original.backup(destination)
    assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
record = {'backup': backup.name, 'integrity_check': 'ok', 'queued': 0, 'running': 0}
(runtime / 'mr-issue-activation.local.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
print('Verified local DB backup:', backup.name)
