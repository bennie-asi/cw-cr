"""Read-only preflight for unsaved GitLab repository configuration."""
from urllib.parse import quote

import requests
from django.conf import settings

from review.integrations.gitlab import _connection_values, gitlab_origin
from review.services.read_budget import ReadBudget, ReadBudgetExhausted


def _get(base_url, project_ref, token, budget, resource='', params=None):
    timeout = budget.next_timeout()
    response = budget.run(lambda: requests.get(
        f'{base_url}/api/v4/projects/{project_ref}{resource}',
        headers={'PRIVATE-TOKEN': token, 'Content-Type': 'application/json'},
        allow_redirects=False, timeout=timeout, params=params,
    ))
    if response.status_code != 200:
        raise requests.HTTPError('GitLab 读取失败', response=response)
    return response


def _check_webhook(base_url, project_ref, token, budget, webhook_url):
    page = 1
    disabled_match = False
    while True:
        response = _get(base_url, project_ref, token, budget, '/hooks', {'per_page': 100, 'page': page})
        hooks = response.json()
        if not isinstance(hooks, list) or any(not isinstance(hook, dict) for hook in hooks):
            raise ValueError('GitLab Webhook 数据格式无效')
        for hook in hooks:
            if hook.get('url') != webhook_url:
                continue
            if (hook.get('merge_requests_events') is True and not hook.get('disabled_until')
                    and hook.get('alert_status', 'executable') == 'executable'):
                return {'state': 'ready', 'detail': 'Webhook URL 与 MR 事件配置已就绪；GitLab 不返回 Secret，未验证 Secret 或实际投递'}
            disabled_match = True
        next_page = response.headers.get('X-Next-Page')
        if next_page == '' or (next_page is None and len(hooks) < 100):
            detail = 'Webhook 已停用或未开启 MR 事件，请在 GitLab 检查配置' if disabled_match else '未找到匹配的 MR Webhook，请在 GitLab 添加配置'
            return {'state': 'missing', 'detail': detail + '；未验证 Secret 或实际投递'}
        candidate = int(next_page) if next_page is not None else page + 1
        if candidate != page + 1:
            raise ValueError('GitLab Webhook 分页异常')
        page = candidate


def check_project_connections(repositories, webhook_url):
    configured_url, token, enabled = _connection_values()
    budget = ReadBudget(
        getattr(settings, 'PROJECT_CONNECTION_MAX_REQUESTS', 30),
        getattr(settings, 'PROJECT_CONNECTION_MAX_SECONDS', 15),
        getattr(settings, 'PROJECT_CONNECTION_REQUEST_TIMEOUT', 3),
    )
    results = []
    for repository in repositories:
        row = {
            'path': repository['path'],
            'connection': {'state': 'unconfigured', 'detail': '请先启用并配置平台 GitLab 地址和 API Token'},
            'webhook': {'state': 'unknown', 'detail': '尚未确认仓库连接，无法读取 Webhook 配置'},
        }
        results.append(row)
        if not enabled or not configured_url or not token:
            continue
        selected_url = (repository.get('gitlab_url') or configured_url).strip().rstrip('/')
        try:
            # Compare the origin before constructing any credential-bearing request.
            if gitlab_origin(selected_url) != gitlab_origin(configured_url):
                raise ValueError('GitLab 地址与平台连接不一致')
        except ValueError:
            row['connection'] = {'state': 'error', 'detail': '仓库 GitLab 地址必须与平台连接同源，且不能含账号凭据'}
            continue
        project_ref = quote(repository.get('gitlab_project_id') or repository['path'], safe='')
        try:
            project = _get(selected_url, project_ref, token, budget).json()
            if not isinstance(project, dict) or not project.get('id'):
                raise ValueError('GitLab 项目数据格式无效')
        except ReadBudgetExhausted:
            row['connection'] = {'state': 'error', 'detail': '检查达到请求次数或时间上限，请稍后重试'}
            continue
        except (requests.RequestException, ValueError, TypeError):
            row['connection'] = {'state': 'error', 'detail': '无法读取 GitLab 仓库，请检查地址、项目路径和访问权限'}
            continue
        row['connection'] = {'state': 'ready', 'detail': '已确认可以读取 GitLab 仓库'}
        try:
            row['webhook'] = _check_webhook(selected_url, project_ref, token, budget, webhook_url)
        except requests.HTTPError as exc:
            detail = '当前 Token 无权读取项目 Webhook，请由 GitLab 管理员确认配置' if exc.response is not None and exc.response.status_code == 403 else '无法读取项目 Webhook，请检查 GitLab 访问权限'
            row['webhook'] = {'state': 'unknown', 'detail': detail}
        except ReadBudgetExhausted:
            row['webhook'] = {'state': 'unknown', 'detail': 'Webhook 检查达到请求次数或时间上限，请稍后重试'}
        except (requests.RequestException, ValueError, TypeError):
            row['webhook'] = {'state': 'unknown', 'detail': '无法确认项目 Webhook 配置，请在 GitLab 检查'}
    return {'webhook_url': webhook_url, 'repositories': results}
