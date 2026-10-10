"""Initialize the persistent Django session key without printing or replacing it."""
import os
from pathlib import Path
import secrets


def initialize():
    if os.environ.get('DJANGO_SECRET_KEY', '').strip():
        return
    path = Path(os.environ.get('DJANGO_SECRET_KEY_FILE') or '/data/secrets/django.key')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if len(path.read_text(encoding='utf-8').strip()) < 50:
            raise SystemExit('Existing Django key file is invalid; restore the original key.')
        return
    with os.fdopen(descriptor, 'w', encoding='utf-8') as destination:
        destination.write(secrets.token_urlsafe(64) + '\n')
        destination.flush()
        os.fsync(destination.fileno())


if __name__ == '__main__':
    initialize()
