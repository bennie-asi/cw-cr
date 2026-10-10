from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_STDOUT_LIMIT = 8 * 1024 * 1024
DEFAULT_STDERR_LIMIT = 256 * 1024
_SAFE_ENV_KEYS = {
    'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP',
    'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'HOME', 'LANG', 'LC_ALL',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
}


class EngineProcessError(RuntimeError):
    pass


class EngineProcessTimeout(EngineProcessError):
    pass


class EngineOutputTooLarge(EngineProcessError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str
    returncode: int


def isolated_environment(overrides: Mapping[str, str] | None = None, home: str | Path | None = None) -> dict[str, str]:
    """Pass only runtime/network essentials and explicit per-job settings to an engine."""
    allowed = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV_KEYS}
    if home is not None:
        private_home = str(home)
        for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
            if key in allowed or key in ('HOME', 'USERPROFILE'):
                allowed[key] = private_home
    for key, value in (overrides or {}).items():
        if value is not None:
            allowed[str(key)] = str(value)
    return allowed


def _redact(value: str, secrets: Sequence[str]) -> str:
    result = value
    for secret in sorted((item for item in secrets if len(item) >= 4), key=len, reverse=True):
        result = result.replace(secret, '[REDACTED]')
    result = re.sub(r'(?i)(bearer\s+)[^\s,;]+', r'\1[REDACTED]', result)
    result = re.sub(r'(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', result)
    return result


def _terminate_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == 'nt':
        try:
            subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except (OSError, subprocess.SubprocessError):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def _read_bounded(stream, limit: int, output: list[bytes], overflow: threading.Event) -> None:
    size = 0
    while True:
        chunk = stream.read(min(65536, max(1, limit - size + 1)))
        if not chunk:
            return
        size += len(chunk)
        if size > limit:
            overflow.set()
            return
        output.append(chunk)


def run_engine_process(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str],
    timeout: float,
    max_stdout_bytes: int = DEFAULT_STDOUT_LIMIT,
    max_stderr_bytes: int = DEFAULT_STDERR_LIMIT,
) -> ProcessResult:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError('engine command must be a non-empty argument array')
    private_env = isolated_environment(env)
    forbidden = [key for key in env if any(marker in key.upper() for marker in ('GITLAB', 'WECOM', 'WEBHOOK', 'PRIVATE_TOKEN'))]
    if forbidden:
        raise ValueError('review engine environment cannot receive GitLab or notification credentials')
    secret_values = [value for key, value in private_env.items() if any(part in key.upper() for part in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))]
    flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) if os.name == 'nt' else 0
    kwargs = {'start_new_session': os.name != 'nt'} if os.name != 'nt' else {'creationflags': flags}
    process = subprocess.Popen(
        list(argv), cwd=str(cwd), env=private_env, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs,
    )
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_overflow = threading.Event()
    stderr_overflow = threading.Event()
    readers = [
        threading.Thread(target=_read_bounded, args=(process.stdout, max_stdout_bytes, stdout_chunks, stdout_overflow), daemon=True),
        threading.Thread(target=_read_bounded, args=(process.stderr, max_stderr_bytes, stderr_chunks, stderr_overflow), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + max(0.1, timeout)
    timed_out = False
    while process.poll() is None:
        if stdout_overflow.is_set() or stderr_overflow.is_set():
            _terminate_tree(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate_tree(process)
            break
        time.sleep(0.025)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    for reader in readers:
        reader.join(timeout=2)

    stderr = _redact(b''.join(stderr_chunks).decode('utf-8', errors='replace'), secret_values)
    if timed_out:
        raise EngineProcessTimeout('review engine exceeded the configured timeout')
    if stdout_overflow.is_set() or stderr_overflow.is_set():
        raise EngineOutputTooLarge('review engine output exceeded the configured size limit')
    result = ProcessResult(
        stdout=b''.join(stdout_chunks).decode('utf-8', errors='replace'),
        stderr=stderr,
        returncode=int(process.returncode or 0),
    )
    if result.returncode != 0:
        detail = result.stderr.strip().replace('\n', ' ')[:500]
        raise EngineProcessError(f'review engine exited with status {result.returncode}' + (f': {detail}' if detail else ''))
    return result
