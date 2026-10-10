"""Single-host, process-lifetime locks. Never delete lock files while services run."""
from contextlib import contextmanager
from functools import wraps
from hashlib import sha256
from pathlib import Path
import os

from django.conf import settings


class ResourceBusy(RuntimeError):
    pass


@contextmanager
def resource_lock(key):
    directory = Path(settings.BASE_DIR) / '.runtime' / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (sha256(str(key).encode()).hexdigest() + '.lock')
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ResourceBusy('此操作正在执行，请稍后刷新状态') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def exclusive_resource(key):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with resource_lock(key(*args, **kwargs)):
                return function(*args, **kwargs)
        return wrapped
    return decorate
