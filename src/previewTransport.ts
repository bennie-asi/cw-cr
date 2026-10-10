import type { ManagedUser, MergeRequestItem, ProjectMember, RepositoryDraft } from './api'

// Only the development preview imports this transport. Its writes live in memory.
export function createPreviewFetch(network: typeof fetch, origin: string): typeof fetch {
  let users: ManagedUser[] = [
    { id: 0, username: 'v4-preview', display_name: '设计预览', platform_role: 'admin', is_active: true, last_login: null },
    { id: 1, username: 'linxiao', display_name: '林晓', platform_role: 'member', is_active: true, last_login: null },
    { id: 2, username: 'chenyu', display_name: '陈宇', platform_role: 'member', is_active: true, last_login: null },
    { id: 3, username: 'zhouqi', display_name: '周琪', platform_role: 'member', is_active: true, last_login: null },
  ]
  const members = new Map<string, ProjectMember[]>()
  const requests = new Map<string, MergeRequestItem[]>()
  const reply = (value: unknown, status = 200) => new Response(status === 204 ? null : JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
  return async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, origin)
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase()
    const staticAsset = /^(?:\/src\/|\/assets\/|\/node_modules\/|\/@(?:vite|id|fs)\/|\/@react-refresh$|\/ui-implementation-preview(?:-hub)?\.html$)/.test(url.pathname)
    if (url.origin === origin && staticAsset && ['GET', 'HEAD'].includes(method)) return network(input, init)
    let body: Record<string, unknown> = {}
    try { body = JSON.parse(typeof init?.body === 'string' ? init.body : input instanceof Request ? await input.clone().text() || '{}' : '{}') }
    catch { return reply({ detail: '无法解析预览请求' }, 400) }
    if (url.pathname === '/api/overview/coverage/' && method === 'GET') return reply({ state: 'complete', open_mrs: 12, reviewed_mrs: 9, unreviewed_mrs: 3, percentage: 75, checked_at: new Date().toISOString(), repositories_checked: 4, repositories_total: 4, errors: [] })
    if (url.pathname === '/api/projects/connection-check/' && method === 'POST') return reply({ webhook_url: `${origin}/api/webhooks/gitlab/`, repositories: (Array.isArray(body.repositories) ? body.repositories as RepositoryDraft[] : []).map(repository => ({ path: repository.path, connection: { state: 'ready', detail: '模拟连接检查通过，未访问真实 GitLab' }, webhook: { state: 'missing', detail: '模拟仓库尚未设置 Webhook，可创建项目后配置' } })) })
    const project = url.pathname.match(/^\/api\/projects\/([^/]+)\/(members|merge-requests|trigger)\/(\d+\/)?$/)
    if (project) {
      const [, scope, action, memberId] = project
      if (!members.has(scope)) members.set(scope, users.filter(user => [1, 2].includes(user.id)).map(user => ({ ...user, user_id: user.id, role: user.id === 1 ? 'admin' : 'member' })))
      if (!requests.has(scope)) requests.set(scope, [
        { iid: 357, title: '优化订单查询与异常处理', state: 'opened', web_url: '', source_branch: 'feature/order-query', target_branch: 'main', sha: 'abc357', review_state: 'reviewed', review_id: 'review-357', issue_count: 6, updated_at: '2026-09-30T06:32:00Z' },
        { iid: 128, title: '新增批量任务接口', state: 'opened', web_url: '', source_branch: 'feature/batch-jobs', target_branch: 'main', sha: 'abc128', review_state: 'unreviewed', issue_count: null, updated_at: '2026-09-30T06:20:00Z' },
        { iid: 124, title: '修复超时任务恢复逻辑', state: 'opened', web_url: '', source_branch: 'fix/timeouts', target_branch: 'main', sha: 'new124', review_state: 'stale', issue_count: null, draft: true, updated_at: '2026-09-30T05:30:00Z' },
        { iid: 123, title: '调整请求日志格式', state: 'opened', web_url: '', source_branch: 'chore/logging', target_branch: 'main', sha: 'abc123', review_state: 'queued', issue_count: null, updated_at: '2026-09-30T05:00:00Z' },
      ])
      const rows = members.get(scope)!
      if (action === 'members') {
        const id = memberId ? Number(memberId.replace('/', '')) : null
        if (method === 'GET') return reply(rows)
        if (method === 'POST') {
          const user = users.find(user => user.username === body.username && user.is_active)
          if (!user) return reply({ username: ['账号不存在或已停用'] }, 400)
          if (rows.some(row => row.user_id === user.id)) return reply({ detail: '该账号已是项目成员' }, 400)
          const row: ProjectMember = { ...user, id: Date.now(), user_id: user.id, role: body.role === 'admin' ? 'admin' : 'member' }
          members.set(scope, [...rows, row]); return reply(row, 201)
        }
        const row = rows.find(row => row.id === id)
        if (!row) return reply({ detail: '成员不存在' }, 404)
        if (row.role === 'admin' && rows.filter(row => row.role === 'admin' && row.is_active).length === 1 && (method === 'DELETE' || body.role === 'member')) return reply({ detail: '请先指定另一位项目管理员' }, 400)
        if (method === 'PATCH') { const updated = { ...row, role: body.role === 'admin' ? 'admin' as const : 'member' as const }; members.set(scope, rows.map(item => item.id === row.id ? updated : item)); return reply(updated) }
        if (method === 'DELETE') { members.set(scope, rows.filter(item => item.id !== id)); return reply(null, 204) }
      }
      if (action === 'merge-requests' && method === 'GET') return reply({ results: url.searchParams.get('state') === 'merged' ? [] : requests.get(scope), has_next: false })
      if (action === 'trigger' && method === 'POST') {
        requests.set(scope, requests.get(scope)!.map(row => row.iid === Number(body.mr_iid) ? { ...row, review_state: 'queued' } : row))
        return reply({ job_id: 'preview-job', job_status: 'queued' }, 202)
      }
    }
    if (url.pathname === '/api/users/') {
      if (method === 'GET') return reply(users)
      if (method === 'POST') {
        if (users.some(user => user.username === body.username)) return reply({ username: ['用户名已存在'] }, 400)
        const user: ManagedUser = { id: Date.now(), username: String(body.username || ''), display_name: String(body.display_name || ''), platform_role: body.platform_role === 'admin' ? 'admin' : 'member', is_active: true, last_login: null }
        users = [...users, user]; return reply(user, 201)
      }
    }
    const userId = url.pathname.match(/^\/api\/users\/(\d+)\/(reset-password\/)?$/)
    if (userId) {
      if (userId[2] && method === 'POST') return reply({ detail: '预览重置已模拟完成，没有修改真实密码' })
      const user = users.find(user => user.id === Number(userId[1]))
      if (user && method === 'PATCH') {
        if (user.id === 0 && (body.is_active === false || body.platform_role === 'member')) return reply({ detail: '不能停用或降级当前平台管理员' }, 400)
        const updated: ManagedUser = { ...user, display_name: typeof body.display_name === 'string' ? body.display_name : user.display_name, is_active: typeof body.is_active === 'boolean' ? body.is_active : user.is_active, platform_role: body.platform_role === 'admin' ? 'admin' : body.platform_role === 'member' ? 'member' : user.platform_role }
        users = users.map(row => row.id === updated.id ? updated : row); return reply(updated)
      }
    }
    return reply({ detail: '此操作未提供模拟响应；交互预览不会访问或修改真实后端' }, 501)
  }
}
