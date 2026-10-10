from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings

from review.engines.process import isolated_environment


MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 100_000


@dataclass(frozen=True)
class ReviewSnapshot:
    root: Path
    repository: Path
    base_ref: str = 'cwcr-base'
    head_ref: str = 'cwcr-head'


def safe_extract_repository_archive(archive_path: str | Path, destination: str | Path) -> None:
    """Extract GitLab's one-root tarball without trusting archive paths or links."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    count = 0
    archive_prefix = None
    with tarfile.open(archive_path, mode='r:gz') as archive:
        for member in archive:
            count += 1
            if count > MAX_ARCHIVE_FILES:
                raise ValueError('GitLab 仓库快照文件数量超过安全限制')
            parts = PurePosixPath(member.name).parts
            if not parts or PurePosixPath(member.name).is_absolute() or any(part in ('', '.', '..') for part in parts):
                raise ValueError('GitLab 仓库归档包含不安全路径')
            if archive_prefix is None:
                archive_prefix = parts[0]
            if parts[0] != archive_prefix or any(part.lower() == '.git' for part in parts[1:]):
                raise ValueError('GitLab 仓库归档结构无效')
            relative_parts = parts[1:]
            if not relative_parts:
                continue
            target = destination.joinpath(*relative_parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('GitLab 仓库归档路径越界')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile() or member.issym() or member.islnk() or member.isdev():
                raise ValueError('GitLab 仓库归档包含不支持的链接或特殊文件')
            total_bytes += member.size
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise ValueError('GitLab 仓库快照展开后超过安全大小限制')
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError('GitLab 仓库归档文件无法读取')
            with source, target.open('wb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _git(repository: Path, home: Path, *args: str) -> str:
    env = isolated_environment({}, home=home)
    env.update({
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': str(home / 'empty.gitconfig'),
        'GIT_TERMINAL_PROMPT': '0',
        'GIT_ALLOW_PROTOCOL': 'file',
    })
    completed = subprocess.run(
        ['git', *args], cwd=repository, env=env, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError('无法准备只读评审仓库快照')
    return completed.stdout.decode('utf-8', errors='replace').strip()


def _replace_worktree(repository: Path, source: Path) -> None:
    for child in repository.iterdir():
        if child.name == '.git':
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    for child in source.iterdir():
        target = repository / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, target)
        elif child.is_file() and not child.is_symlink():
            shutil.copy2(child, target)
        else:
            raise ValueError('GitLab 仓库快照包含不支持的链接')


@contextlib.contextmanager
def create_review_snapshot(gitlab_client, base_sha: str, head_sha: str):
    """Build two local commits from verified GitLab archives; no engine gets GitLab credentials."""
    if not base_sha or not head_sha:
        raise ValueError('缺少 MR 快照所需的 base/head SHA')
    with tempfile.TemporaryDirectory(prefix='cw-cr-review-') as temp_root:
        root = Path(temp_root)
        base_tree = root / 'base-tree'
        head_tree = root / 'head-tree'
        base_tree.mkdir()
        head_tree.mkdir()
        base_archive = root / 'base.tar.gz'
        head_archive = root / 'head.tar.gz'
        gitlab_client.download_repository_archive(base_sha, base_archive)
        gitlab_client.download_repository_archive(head_sha, head_archive)
        safe_extract_repository_archive(base_archive, base_tree)
        safe_extract_repository_archive(head_archive, head_tree)
        base_archive.unlink(missing_ok=True)
        head_archive.unlink(missing_ok=True)

        repository = root / 'repository'
        shutil.copytree(base_tree, repository)
        _git(repository, root, 'init', '--quiet', '--initial-branch=cwcr-base')
        _git(repository, root, 'add', '--all')
        _git(repository, root, '-c', 'user.name=智审台', '-c', 'user.email=review@localhost', 'commit', '--quiet', '--allow-empty', '-m', 'review base snapshot')
        _git(repository, root, 'switch', '--quiet', '-c', 'cwcr-head')
        _replace_worktree(repository, head_tree)
        _git(repository, root, 'add', '--all')
        _git(repository, root, '-c', 'user.name=智审台', '-c', 'user.email=review@localhost', 'commit', '--quiet', '--allow-empty', '-m', 'review head snapshot')
        yield ReviewSnapshot(root=root, repository=repository)


@contextlib.contextmanager
def create_probe_snapshot():
    """Create a tiny local-only repository for runtime verification without external writes."""
    with tempfile.TemporaryDirectory(prefix='cw-cr-engine-probe-') as temp_root:
        root = Path(temp_root)
        repository = root / 'repository'
        repository.mkdir()
        (repository / 'probe.py').write_text('def probe(value):\n    return value\n', encoding='utf-8')
        _git(repository, root, 'init', '--quiet', '--initial-branch=cwcr-base')
        _git(repository, root, 'add', '--all')
        _git(repository, root, '-c', 'user.name=智审台', '-c', 'user.email=review@localhost', 'commit', '--quiet', '-m', 'probe base')
        _git(repository, root, 'switch', '--quiet', '-c', 'cwcr-head')
        (repository / 'probe.py').write_text('def probe(value):\n    return value.name\n', encoding='utf-8')
        _git(repository, root, 'add', '--all')
        _git(repository, root, '-c', 'user.name=智审台', '-c', 'user.email=review@localhost', 'commit', '--quiet', '-m', 'probe head')
        diff = _git(repository, root, 'diff', '--no-ext-diff', '--unified=3', 'cwcr-base...cwcr-head')
        yield ReviewSnapshot(root=root, repository=repository), diff


def snapshot_archive_limit() -> int:
    return int(getattr(settings, 'REVIEW_ENGINE_ARCHIVE_MAX_BYTES', 200 * 1024 * 1024))
