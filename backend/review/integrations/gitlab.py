from __future__ import annotations
from review.integrations.errors import safe_error

from urllib.parse import quote, urlparse, urlsplit

import requests
from django.conf import settings

from review.integrations.errors import IntegrationNotConfiguredError
from review.models import GitLabConfig, LineComment, Project, Review
from review.services.execution_locks import exclusive_resource
from review.services.diff_positions import validate_findings, discussion_position
from review.services.finding_locations import ground_finding_anchors, comment_finding
from review.services.suggestions import suggestion_body


def gitlab_origin(url):
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if (scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('GitLab 地址格式无效')
    return scheme, parsed.hostname.lower(), parsed.port or (443 if scheme == 'https' else 80)


def _global_gitlab_config() -> GitLabConfig | None:
    return GitLabConfig.objects.filter(pk=1).first()


def _connection_values(overrides: dict | None = None) -> tuple[str, str, bool]:
    config = _global_gitlab_config()
    values = overrides or {}
    if config is not None and values.get('enabled', config.enabled) is False:
        return '', '', False
    base_url = (values.get('base_url') or (config.base_url if config and config.base_url else settings.GITLAB_URL)).strip().rstrip('/')
    token = values.get('api_token') or (config.api_token if config and config.api_token else settings.GITLAB_TOKEN)
    return base_url, token, bool(values.get('enabled', config.enabled if config is not None else True))


def gitlab_webhook_secret(project: Project | None = None) -> str:
    if project and project.webhook_secret:
        return project.webhook_secret
    config = _global_gitlab_config()
    if config and config.webhook_secret:
        return config.webhook_secret
    return settings.GITLAB_WEBHOOK_SECRET


def test_gitlab_connection(timeout: float | None = None, overrides: dict | None = None) -> dict:
    base_url, token, enabled = _connection_values(overrides)
    if not enabled or not token or not base_url:
        if settings.REVIEW_ALLOW_TEST_FALLBACK:
            return {
                'ok': True,
                'configured': False,
                'base_url': base_url,
                'message': '未配置 GitLab，未发起外部请求。',
            }
        return {
            'ok': False,
            'configured': False,
            'base_url': base_url,
            'error': 'GitLab 未配置完整连接信息（需要地址和 API Token）',
        }
    try:
        response = requests.get(
            f'{base_url}/api/v4/user',
            allow_redirects=False, headers={'PRIVATE-TOKEN': token, 'Content-Type': 'application/json'},
            timeout=timeout if timeout is not None else settings.GITLAB_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json() or {}
        return {
            'ok': True,
            'configured': True,
            'base_url': base_url,
            'user_id': payload.get('id'),
            'username': payload.get('username') or payload.get('name', ''),
        }
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {'ok': False, 'configured': True, 'base_url': base_url, 'error': safe_error(exc)}


class GitLabClient:
    """Small GitLab REST adapter with a deterministic local mode."""

    def __init__(self, project: Project, timeout: float | None = None, repository_path: str | None = None):
        self.project = project
        self.repository_path = repository_path or project.gitlab_project_path
        repository = project.repositories.filter(path=self.repository_path).first()
        if repository_path and repository is None and repository_path != project.gitlab_project_path:
            raise ValueError('仓库不属于当前项目')
        self.repository = repository
        self.timeout = timeout if timeout is not None else settings.GITLAB_REQUEST_TIMEOUT
        project_url = ((repository.gitlab_url if repository else project.gitlab_url) or '').strip()
        if not project_url:
            project_url, _, _ = _connection_values()
        self.base_url = project_url.rstrip('/')
        configured_url, self.token, self.connection_enabled = _connection_values()
        if self.token and configured_url:
            if gitlab_origin(configured_url) != gitlab_origin(self.base_url):
                raise ValueError('项目 GitLab 地址与全局连接不一致，禁止向其他站点发送全局 Token')

    @property
    def project_ref(self) -> str:
        raw_ref = (self.repository.gitlab_project_id or self.repository.path) if self.repository else (self.project.gitlab_project_id or self.project.gitlab_project_path)
        if raw_ref.startswith(('http://', 'https://')):
            raw_ref = urlparse(raw_ref).path.strip('/')
        return quote(raw_ref, safe='')

    @property
    def remote_enabled(self) -> bool:
        return bool(self.connection_enabled and self.token and self.base_url)

    def _headers(self) -> dict[str, str]:
        return {'PRIVATE-TOKEN': self.token, 'Content-Type': 'application/json'}

    def fetch_merge_request(self, mr_iid: int, payload: dict) -> dict:
        if not self.remote_enabled and not settings.REVIEW_ALLOW_TEST_FALLBACK:
            raise IntegrationNotConfiguredError('GitLab 未配置完整连接信息，无法读取 MR')
        attributes = payload.get('object_attributes') or payload.get('merge_request') or {}
        local = {
            'iid': mr_iid,
            'title': attributes.get('title') or payload.get('title', ''),
            'source_branch': attributes.get('source_branch') or payload.get('branch', ''),
            'target_branch': attributes.get('target_branch', 'main'),
            'web_url': attributes.get('url') or payload.get('gitlab_url', ''),
            'diff_refs': attributes.get('diff_refs') or {},
        }
        last_commit = attributes.get('last_commit') or {}
        if isinstance(last_commit, dict) and last_commit.get('id'):
            local['diff_refs'] = {**local['diff_refs'], 'head_sha': last_commit['id']}
        if attributes:
            if not self.remote_enabled:
                return local
            try:
                response = requests.get(
                    f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{mr_iid}',
                    allow_redirects=False, headers=self._headers(), timeout=self.timeout,
                )
                response.raise_for_status()
                return {**local, **response.json()}
            except requests.RequestException:
                if settings.REVIEW_ALLOW_TEST_FALLBACK:
                    return local
                raise
        if not self.remote_enabled:
            return {
                'iid': mr_iid,
                'title': payload.get('title', ''),
                'source_branch': payload.get('branch', ''),
                'target_branch': 'main',
                'web_url': payload.get('gitlab_url', ''),
                'diff_refs': {},
            }
        response = requests.get(
            f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{mr_iid}',
            allow_redirects=False, headers=self._headers(), timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def list_merge_requests(self, page: int = 1, state: str = 'opened') -> tuple[list[dict], bool]:
        if not self.remote_enabled:
            raise IntegrationNotConfiguredError('请先配置 GitLab 地址和 API Token')
        response = requests.get(
            f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests',
            allow_redirects=False, headers=self._headers(), timeout=self.timeout,
            params={'state': state, 'order_by': 'updated_at', 'sort': 'desc', 'per_page': 20, 'page': page},
        )
        response.raise_for_status()
        if isinstance(response.status_code, int) and 300 <= response.status_code < 400:
            raise ValueError('GitLab MR 列表返回重定向')
        items = response.json()
        if (not isinstance(items, list) or any(not isinstance(item, dict)
                or not isinstance(item.get('iid'), int) or isinstance(item['iid'], bool) or item['iid'] <= 0
                for item in items)):
            raise ValueError('GitLab MR 列表格式无效')
        fields = ('iid', 'title', 'state', 'source_branch', 'target_branch', 'web_url', 'sha', 'updated_at')
        rows = [{**{key: item.get(key) for key in fields},
                 'draft': item.get('draft', item.get('work_in_progress', False)) is True} for item in items]
        if any(row['sha'] is not None and not isinstance(row['sha'], str) for row in rows):
            raise ValueError('GitLab MR 提交 SHA 格式无效')
        next_page = response.headers.get('X-Next-Page')
        if next_page not in ('', None) and int(next_page) != page + 1:
            raise ValueError('GitLab MR 分页异常')
        return rows, bool(next_page) if next_page is not None else len(items) >= 20

    def fetch_diff(self, mr_iid: int, payload: dict) -> str:
        if not self.remote_enabled and not settings.REVIEW_ALLOW_TEST_FALLBACK:
            raise IntegrationNotConfiguredError('GitLab 未配置完整连接信息，无法读取 MR diff')
        if payload.get('diff') and settings.REVIEW_ALLOW_TEST_FALLBACK:
            return str(payload['diff'])
        if not self.remote_enabled:
            return 'src/service/review.ts\nsrc/service/webhook.ts\nsrc/types/review.ts'
        response = requests.get(
            f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{mr_iid}/changes',
            allow_redirects=False, headers=self._headers(), timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get('overflow') is True:
            raise ValueError('GitLab diff 被截断，无法生成完整评审结论')
        changes = data.get('changes', [])
        parts = []
        for change in changes:
            path = change.get('new_path') or change.get('old_path') or ''
            old_path = change.get('old_path') or path
            patch = change.get('diff') or ''
            if patch:
                parts.append(f'diff --git a/{old_path} b/{path}\n--- a/{old_path}\n+++ b/{path}\n{patch}')
            elif path:
                parts.append(path)
        return '\n'.join(parts)

    def download_repository_archive(self, sha: str, target_path) -> int:
        """Download one immutable repository snapshot using the API token only in an HTTP header."""
        import re
        from pathlib import Path

        if not self.remote_enabled:
            raise IntegrationNotConfiguredError('GitLab 未配置完整连接信息，无法获取评审快照')
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{40,64}', sha):
            raise ValueError('GitLab 快照 SHA 格式无效')
        maximum = int(getattr(settings, 'REVIEW_ENGINE_ARCHIVE_MAX_BYTES', 200 * 1024 * 1024))
        response = requests.get(
            f'{self.base_url}/api/v4/projects/{self.project_ref}/repository/archive.tar.gz',
            allow_redirects=False,
            headers=self._headers(),
            params={'sha': sha},
            timeout=self.timeout,
            stream=True,
        )
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise
        content_length = response.headers.get('Content-Length')
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                response.close()
                raise ValueError('GitLab 仓库归档大小信息无效') from exc
            if declared_size < 0:
                response.close()
                raise ValueError('GitLab 仓库归档大小信息无效')
            if declared_size > maximum:
                response.close()
                raise ValueError('GitLab 仓库归档超过安全大小限制')
        total = 0
        target = Path(target_path)
        try:
            with target.open('wb') as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError('GitLab 仓库归档超过安全大小限制')
                    output.write(chunk)
        except Exception:
            try:
                target.unlink(missing_ok=True)
            finally:
                response.close()
            raise
        response.close()
        return total

    def _find_publication(self, review: Review, resource: str, marker: str) -> dict | None:
        # A failed/incomplete lookup must never be interpreted as permission to POST.
        page = 1
        for _ in range(100):
            response = requests.get(
                f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{review.mr_iid}/{resource}',
                allow_redirects=False, headers=self._headers(), timeout=self.timeout,
                params={'per_page': 100, 'page': page},
            )
            response.raise_for_status()
            items = response.json()
            if not isinstance(items, list):
                raise ValueError('GitLab 发布记录格式异常，已停止发送')
            for item in items:
                if not isinstance(item, dict) or not item.get('id'):
                    raise ValueError('GitLab 发布记录缺少 ID，已停止发送')
                notes = item.get('notes') if resource == 'discussions' else [item]
                if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
                    raise ValueError('GitLab 评论格式异常，已停止发送')
                for note in notes:
                    if str(note.get('body', '')).rstrip().endswith(marker):
                        return {**item, 'resolved': note.get('resolved') is True}
            next_page = response.headers.get('X-Next-Page')
            if next_page == '' or (next_page is None and len(items) < 100):
                return None
            candidate = page + 1 if next_page is None else int(next_page)
            if candidate <= page:
                raise ValueError('GitLab 分页异常，已停止发送')
            page = candidate
        raise ValueError('GitLab 评论分页超过安全上限，已停止发送，请人工核对')

    @exclusive_resource(lambda self, review, comment: f'comment:{comment.issue.first_comment_id if comment.issue_id and comment.issue.first_comment_id else comment.pk}')
    def create_discussion(self, review: Review, comment: LineComment) -> dict:
        if not self.remote_enabled:
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                raise IntegrationNotConfiguredError('GitLab 未配置完整连接信息，无法发布行级评论')
            return {'id': f'local-discussion-{comment.id}'}
        marker_id = comment.issue.first_comment_id if comment.issue_id and comment.issue.first_comment_id else comment.pk
        marker = f'<!-- cw-cr:comment:{marker_id} -->'
        existing = self._find_publication(review, 'discussions', marker)
        if existing is not None:
            return existing
        if not settings.REVIEW_ALLOW_TEST_FALLBACK:
            if not Project.objects.filter(pk=review.project_id, enabled=True).exists():
                raise ValueError('项目已暂停，已停止发布')
            grounded = ground_finding_anchors(review.diff_text, [comment_finding(comment)])[0]
            if any(grounded[field] != getattr(comment, field) for field in ('new_line', 'old_line')):
                raise ValueError('评论原发布坐标与引用代码不符，已停止发布，请重新评审')
            validate_findings(review.diff_text, [grounded])
            refs = self.fetch_merge_request(review.mr_iid, {}).get('diff_refs') or {}
            if any(not getattr(review, field) or refs.get(field) != getattr(review, field) for field in ('base_sha', 'start_sha', 'head_sha')):
                raise ValueError('MR 提交已变化，已停止发布，请重新评审')
        position = {
            'position_type': 'text',
            'base_sha': review.base_sha or review.head_sha,
            'start_sha': review.start_sha or review.base_sha or review.head_sha,
            'head_sha': review.head_sha or review.start_sha,
            'new_path': comment.file_path,
            'old_path': comment.file_path,
        }
        for line in review.diff_text.splitlines():
            if line.startswith('diff --git a/') and line.endswith(' b/' + comment.file_path):
                position['old_path'] = line[len('diff --git a/'):-(len(' b/') + len(comment.file_path))]
                break
        if comment.new_line:
            position['new_line'] = comment.new_line
        elif comment.old_line:
            position['old_line'] = comment.old_line
        if not settings.REVIEW_ALLOW_TEST_FALLBACK or review.diff_text.startswith('diff --git '):
            position.update(discussion_position(review.diff_text, comment.file_path, comment.new_line, comment.old_line))
        body = suggestion_body(review.diff_text, comment.file_path, comment.new_line, comment.old_line,
                               f'**[{comment.level}] {comment.title}**\n\n{comment.body}', comment.suggestion)
        response = requests.post(
            f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{review.mr_iid}/discussions',
            allow_redirects=False, headers=self._headers(),
            json={'body': f'{body}\n\n{marker}', 'position': position},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    @exclusive_resource(lambda self, review, update_existing=False: f'summary:{review.pk}')
    def create_summary_note(self, review: Review, update_existing=False) -> dict:
        """Publish the Chinese total review as a top-level MR note."""
        if not self.remote_enabled:
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                raise IntegrationNotConfiguredError('GitLab 未配置完整连接信息，无法同步中文总评')
            return {'id': f'local-summary-{review.id}'}
        marker = f'<!-- cw-cr:summary:{review.pk} -->'
        existing = self._find_publication(review, 'notes', marker)
        if existing is not None and not update_existing:
            return existing
        summary = (review.summary or '本次评审未生成额外说明。').strip()
        body = f'## 智审台 · 中文总评\n\n{summary}\n\n> 行级评论与门禁结果请查看本 MR 的评审详情。\n\n{marker}'
        if existing is not None:
            response = requests.put(
                f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{review.mr_iid}/notes/{existing["id"]}',
                allow_redirects=False, headers=self._headers(), json={'body': body}, timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        response = requests.post(
            f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{review.mr_iid}/notes',
            allow_redirects=False, headers=self._headers(),
            json={'body': body},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def resolve_discussion(self, review: Review, comment: LineComment) -> bool:
        return self._set_discussion_resolved(review, comment, True)

    def reopen_discussion(self, review: Review, comment: LineComment) -> bool:
        return self._set_discussion_resolved(review, comment, False)

    def _set_discussion_resolved(self, review: Review, comment: LineComment, resolved: bool) -> bool:
        if not comment.discussion_id:
            return True
        if not self.remote_enabled:
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                raise IntegrationNotConfiguredError('GitLab 未配置完整连接信息，无法解决 Discussion')
            return True
        response = requests.put(
            f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{review.mr_iid}/discussions/{comment.discussion_id}',
            allow_redirects=False, headers=self._headers(), json={'resolved': resolved}, timeout=self.timeout,
        )
        response.raise_for_status()
        return True

    def merge(self, review: Review) -> dict:
        if not self.remote_enabled:
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                raise IntegrationNotConfiguredError('GitLab 未配置完整连接信息，无法调用合并 API')
            return {'merged': False, 'message': 'GitLab 未配置，未调用合并 API'}
        if not review.head_sha:
            raise ValueError('评审缺少提交 SHA，无法安全合并；请重新评审')
        response = requests.put(
            f'{self.base_url}/api/v4/projects/{self.project_ref}/merge_requests/{review.mr_iid}/merge',
            allow_redirects=False, headers=self._headers(), json={'squash': False, 'sha': review.head_sha}, timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return {**payload, 'merged': payload.get('state') == 'merged'}
