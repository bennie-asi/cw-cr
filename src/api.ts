import type { LineComment, Project, Review } from './types'

export type BackendProject = {
  current_user_role?: 'admin' | 'member' | null
  id: string
  name: string
  gitlab_project_path: string
  repositories?: { path: string; gitlab_project_id?: string; gitlab_url?: string }[]
  enabled?: boolean
  model_inherit?: boolean
  notification_inherit?: boolean
  status_reasons?: string[]
  owner?: string
  reviews_count?: number
  blockers?: number
  status?: Project['status']
  model?: { inherited?: boolean; configured?: boolean; model?: string }
  notification?: { inherited?: boolean; configured?: boolean }
  policy?: { webhook_trigger?: boolean }
  updated_at?: string
}

export type BackendReview = {
  recheck_result?: {
    applied?: boolean
    new?: number
    repeated?: number
    resolved?: number
    retained?: number
    resolved_issues?: Array<{ issue_id: string; title: string; file_path: string; level: string }>
    retained_issue_ids?: string[]
  }
  execution_state?: string
  generation_state?: string
  publication_state?: string
  summary_sync_status?: string
  notification_state?: string
  merge_status?: string
  merge_notification_state?: string
  merge_attempts?: number
  merge_error?: string
  error_message?: string
  gate_reason?: string
  id: string
  mr_iid: number
  repository_path?: string
  title?: string
  project?: string
  project_name?: string
  branch?: string
  summary?: string
  gitlab_url?: string
  diff_text?: string
  changed_files?: string[]
  additions?: number
  deletions?: number
  source: Review['source']
  status: string
  status_label?: Review['status']
  blocking_comment_count?: number
  comment_count?: number
  duration_ms?: number
  created_at?: string
  comments?: BackendComment[]
  engine_id?: string
  engine_version?: string
  engine_model?: string
}

export type EngineCatalogItem = {
  id: string
  label: string
  capabilities: string[]
  availability: 'ready' | 'not-installed' | 'model-not-configured' | 'unverified' | string
  installed: boolean
  version: string
  verified: boolean
  selectable: boolean
  reason: string
  verified_at: string | null
}

export type GlobalEngineConfig = {
  default_engine: string
  effective_engine: string
  engines: EngineCatalogItem[]
}

export type ProjectEngineConfig = {
  inherited: boolean
  configured_engine: string
  effective_engine: string
  engines: EngineCatalogItem[]
}

export type BackendComment = {
  id: string
  issue_id?: string | null
  file_path: string
  new_line?: number
  old_line?: number
  anchor_code?: string
  location?: LineComment['location']
  level: LineComment['level']
  title: string
  body: string
  status_label?: LineComment['status']
  status: string
}

export type TriggerResponse = {
  job_id: string
  job_status: string
  review: BackendReview | null
}

export type MergeRequestItem = {
  iid: number
  title: string
  state?: string
  source_branch: string
  target_branch: string
  web_url: string
  sha?: string | null
  draft?: boolean
  updated_at?: string | null
  review_state?: 'unreviewed' | 'queued' | 'running' | 'reviewed' | 'stale' | 'failed' | 'unknown'
  review_id?: string | null
  review_sha?: string | null
  issue_count?: number | null
}

export type MRCoverage = {
  state: 'complete' | 'partial' | 'unavailable'
  open_mrs: number
  reviewed_mrs: number
  unreviewed_mrs: number
  percentage: number | null
  checked_at: string
  repositories_checked: number
  repositories_total: number
  errors: { path: string; detail: string }[]
}

export type RepositoryDraft = { path: string; gitlab_project_id?: string; gitlab_url?: string }
export type ProjectConnectionCheck = {
  webhook_url: string
  repositories: {
    path: string
    connection: { state: 'ready' | 'error' | 'unconfigured'; detail: string }
    webhook: { state: 'ready' | 'missing' | 'unknown'; detail: string }
  }[]
}

export type BackendGitLabConfig = {
  id?: number
  base_url?: string
  api_token_masked?: string
  api_token_configured?: boolean
  webhook_secret_configured?: boolean
  enabled?: boolean
}

export type GateAssessment = {
  project_id: string
  review_id: string
  mr_iid: number
  reviewed_sha: string
  current_sha: string
  blocking_levels?: string[]
  blocking_count?: number
  checks: Record<string, boolean>
  passed: boolean
  reason: string
  remote_error?: boolean
  policy_fingerprint?: string
  completed_at?: string
}

export type MergeGateResponse = {
  scope?: 'project'
  automatic_validation_required?: boolean
  project_id: string
  enabled: boolean
  dry_run_required: boolean
  dry_run_passed: boolean
  dry_run_completed_at?: string | null
  dry_run_reason?: string
  assessment?: GateAssessment | null
  engine?: string
  block_on_p1?: boolean
}

const API_BASE = (import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000/api').replace(/\/$/, '')

export type SessionUser = {
  id: number
  username: string
  display_name: string
  platform_role: 'admin' | 'member'
}
export type SessionState = { user: SessionUser | null; csrf_token: string; needs_setup?: boolean }
export type ManagedUser = SessionUser & { is_active: boolean; last_login: string | null }
export type ProjectMember = {
  id: number
  user_id: number
  username: string
  display_name: string
  role: 'admin' | 'member'
  is_active: boolean
  platform_role: SessionUser['platform_role']
}
export type CreateUserInput = Pick<SessionUser, 'username' | 'display_name'> & {
  password: string
  platform_role?: SessionUser['platform_role']
}
export type UpdateUserInput = Partial<Pick<ManagedUser, 'display_name' | 'platform_role' | 'is_active'>>

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message)
    this.name = 'ApiError'
  }
}

let csrfToken = ''
let sessionGeneration = 0
const sessionExpiredListeners = new Set<() => void>()

export function subscribeSessionExpired(listener: () => void): () => void {
  sessionExpiredListeners.add(listener)
  return () => { sessionExpiredListeners.delete(listener) }
}

async function sessionRequest(path: string, init: RequestInit = {}): Promise<SessionState> {
  const generation = ++sessionGeneration
  const data = await request<SessionState>(path, init)
  if (generation === sessionGeneration) {
    csrfToken = typeof data.csrf_token === 'string' ? data.csrf_token : ''
  }
  return data
}

export function apiUrl(path: string): string {
  return `${API_BASE}/${path.replace(/^\//, '')}`
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const generation = sessionGeneration
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (csrfToken && !['GET', 'HEAD', 'OPTIONS'].includes((init.method || 'GET').toUpperCase())) {
    headers.set('X-CSRFToken', csrfToken)
  }
  const response = await fetch(apiUrl(path), {
    ...init,
    credentials: 'include',
    headers,
  })
  if (!response.ok) {
    const detail = await response.text()
    let message = `请求失败（HTTP ${response.status}）`
    try {
      const data = JSON.parse(detail)
      if (typeof data?.error === 'string') message = data.error
      else if (typeof data?.detail === 'string') message = data.detail
      else if (data && typeof data === 'object') {
        const fields = ['username', 'display_name', 'password', 'new_password', 'role', 'platform_role', 'is_active', 'non_field_errors']
        const values: unknown[] = Array.isArray(data) ? data : fields.flatMap(field => data[field] ?? [])
        const messages = values.filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
        if (messages.length) message = messages.join('；')
      }
    } catch { /* Non-JSON upstream responses should not render raw HTML. */ }
    const anonymousAuthEndpoint = ['auth/login/', 'auth/session/', 'auth/logout/'].includes(path.replace(/^\//, ''))
    if (response.status === 401 && !anonymousAuthEndpoint && generation === sessionGeneration) {
      sessionGeneration++
      csrfToken = ''
      sessionExpiredListeners.forEach(listener => listener())
    }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function mapProject(input: BackendProject): Project {
  const inheritedModel = input.model?.inherited !== false
  return {
    id: input.id,
    name: input.name,
    currentUserRole: input.current_user_role || null,
    path: input.gitlab_project_path,
    repositories: input.repositories?.length ? input.repositories : [{ path: input.gitlab_project_path }],
    enabled: input.enabled !== false,
    modelInherit: input.model_inherit !== false,
    notificationInherit: input.notification_inherit !== false,
    statusReasons: input.status_reasons || [],
    automationEnabled: input.policy?.webhook_trigger !== false,
    owner: input.owner || '未分配',
    reviews: input.reviews_count || 0,
    blockers: input.blockers || 0,
    status: input.status || '待配置',
    model: input.model?.configured === false ? '未配置' : inheritedModel ? (input.model?.model || '继承全局模型') : (input.model?.model || '未配置'),
    notify: input.notification?.configured === false ? '未配置' : input.notification?.inherited === false ? '已覆盖' : '继承全局',
    updatedAt: input.updated_at ? new Date(input.updated_at).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—',
  }
}

export function mapComment(input: BackendComment): LineComment {
  const status = input.status_label || (input.status === 'published' ? '已发布' : input.status === 'resolved' ? '已解决' : input.status === 'failed' ? '发布失败' : '待发布')
  const newLine = input.location?.status === 'unverified' ? null : input.location ? input.location.new_line : input.new_line
  const oldLine = input.location?.status === 'unverified' ? null : input.location ? input.location.old_line : input.old_line
  return {
    id: input.id,
    file: input.file_path,
    line: newLine || oldLine || 0,
    side: newLine ? 'new' : 'old',
    location: input.location,
    level: input.level,
    title: input.title,
    body: input.body,
    status,
  }
}

export function mapReview(input: BackendReview): Review {
  const statusMap: Record<string, Review['status']> = { blocked: '有阻断', completed: '已完成', queued: '排队中', running: '运行中', failed: '失败' }
  return {
    id: input.id,
    projectId: input.project,
    repositoryPath: input.repository_path,
    mr: `!${input.mr_iid}`,
    project: input.project_name || input.project || '未关联项目',
    branch: input.branch || '未记录分支',
    source: input.source,
    status: input.status_label || statusMap[input.status] || '运行中',
    generationState: input.generation_state || input.status,
    publicationState: input.publication_state,
    notificationState: input.notification_state,
    blockers: input.blocking_comment_count || 0,
    comments: input.comment_count || input.comments?.length || 0,
    createdAt: input.created_at ? new Date(input.created_at).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—',
    duration: input.duration_ms ? `${Math.round(input.duration_ms / 1000)}s` : '—',
    engineId: input.engine_id,
    engineVersion: input.engine_version,
    engineModel: input.engine_model,
  }
}

export type PromptConfig = {
  general_prompt: string
  review_prompt: string
  improve_prompt: string
  security_prompt: string
  reliability_prompt: string
  style_prompt: string
}

export const api = {
  promptConfig: () => request<PromptConfig>('/prompt-config/'),
  updatePromptConfig: (payload: Record<string, unknown>) => request<PromptConfig>('/prompt-config/', { method: 'PATCH', body: JSON.stringify(payload) }),
  listUsers: () => request<ManagedUser[]>('/users/'),
  createUser: (payload: CreateUserInput) => request<ManagedUser>('/users/', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: UpdateUserInput) => request<ManagedUser>(`/users/${id}/`, { method: 'PATCH', body: JSON.stringify(payload) }),
  resetUserPassword: (id: number, new_password: string) => request<{ detail: string }>(`/users/${id}/reset-password/`, { method: 'POST', body: JSON.stringify({ new_password }) }),
  listProjectMembers: (projectId: string) => request<ProjectMember[]>(`/projects/${encodeURIComponent(projectId)}/members/`),
  addProjectMember: (projectId: string, payload: { username: string; role: ProjectMember['role'] }) => request<ProjectMember>(`/projects/${encodeURIComponent(projectId)}/members/`, { method: 'POST', body: JSON.stringify(payload) }),
  updateProjectMember: (projectId: string, memberId: number, role: ProjectMember['role']) => request<ProjectMember>(`/projects/${encodeURIComponent(projectId)}/members/${memberId}/`, { method: 'PATCH', body: JSON.stringify({ role }) }),
  removeProjectMember: (projectId: string, memberId: number) => request<void>(`/projects/${encodeURIComponent(projectId)}/members/${memberId}/`, { method: 'DELETE' }),
  session: () => sessionRequest('/auth/session/'),
  login: (username: string, password: string) => sessionRequest('/auth/login/', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => sessionRequest('/auth/logout/', { method: 'POST' }),
  changePassword: async (current_password: string, new_password: string) => {
    const generation = sessionGeneration
    const data = await request<{ detail: string; csrf_token: string }>('/auth/password/', { method: 'POST', body: JSON.stringify({ current_password, new_password }) })
    if (generation === sessionGeneration) csrfToken = data.csrf_token
    return data
  },
  listMergeRequests: (id: string, page = 1, repository?: string, state: 'opened' | 'merged' = 'opened') => request<{ results: MergeRequestItem[]; has_next: boolean }>(`/projects/${id}/merge-requests/?page=${page}&state=${state}${repository ? `&repository=${encodeURIComponent(repository)}` : ''}`),
  overviewCoverage: () => request<MRCoverage>('/overview/coverage/'),
  checkProjectConnection: (repositories: RepositoryDraft[]) => request<ProjectConnectionCheck>('/projects/connection-check/', { method: 'POST', body: JSON.stringify({ repositories }) }),
  health: () => request<{ status: string }>('/health/'),
  overview: () => request<Record<string, unknown>>('/overview/'),
  listProjects: async () => {
    const data = await request<{ results: BackendProject[] }>('/projects/')
    return data.results.map(mapProject)
  },
  createProject: (payload: { name: string; gitlab_project_path?: string; repositories?: { path: string; gitlab_project_id?: string; gitlab_url?: string }[]; owner?: string; model_inherit?: boolean; notification_inherit?: boolean }) => request<BackendProject>('/projects/', { method: 'POST', body: JSON.stringify(payload) }).then(mapProject),
  updateProject: (id: string, payload: Record<string, unknown>) => request<BackendProject>(`/projects/${encodeURIComponent(id)}/`, { method: 'PATCH', body: JSON.stringify(payload) }).then(mapProject),
  listReviews: async (project?: string) => {
    const data = await request<{ results: BackendReview[] }>(`/reviews/${project ? `?project=${encodeURIComponent(project)}` : ''}`)
    return data.results.map(mapReview)
  },
  getReview: (id: string) => request<BackendReview>(`/reviews/${id}/`),
  getPolicy: (projectId: string) => request<Record<string, unknown>>(`/projects/${projectId}/policy/`),
  updatePolicy: (projectId: string, payload: Record<string, unknown>) => request<Record<string, unknown>>(`/projects/${projectId}/policy/`, { method: 'PATCH', body: JSON.stringify(payload) }),
  projectEngineConfig: (projectId: string) => request<ProjectEngineConfig>(`/projects/${encodeURIComponent(projectId)}/engine-config/`),
  updateProjectEngineConfig: (projectId: string, payload: { engine: string } | { inherited: true }) => request<ProjectEngineConfig>(`/projects/${encodeURIComponent(projectId)}/engine-config/`, { method: 'PATCH', body: JSON.stringify(payload) }),
  projectModelConfig: (projectId: string) => request<Record<string, unknown>>(`/projects/${projectId}/model-config/`),
  updateProjectModelConfig: (projectId: string, payload: Record<string, unknown>) => request<Record<string, unknown>>(`/projects/${projectId}/model-config/`, { method: 'PATCH', body: JSON.stringify(payload) }),
  testProjectModelConfig: (projectId: string, payload: Record<string, unknown> = {}) => request<Record<string, unknown>>(`/projects/${projectId}/model-config/test/`, { method: 'POST', body: JSON.stringify(payload) }),
  projectNotificationConfig: (projectId: string) => request<Record<string, unknown>>(`/projects/${projectId}/notification-config/`),
  updateProjectNotificationConfig: (projectId: string, payload: Record<string, unknown>) => request<Record<string, unknown>>(`/projects/${projectId}/notification-config/`, { method: 'PATCH', body: JSON.stringify(payload) }),
  testProjectNotificationConfig: (projectId: string, payload: Record<string, unknown> = {}) => request<Record<string, unknown>>(`/projects/${projectId}/notification-config/test/`, { method: 'POST', body: JSON.stringify(payload) }),
  projectNotificationPreview: (projectId: string) => request<{ review_id: string | null; content: string; will_send: boolean; repository_path?: string }>(`/projects/${encodeURIComponent(projectId)}/notification-preview/`),
  triggerReview: (projectId: string, payload: Record<string, unknown>) => request<TriggerResponse>(`/projects/${projectId}/trigger/`, { method: 'POST', body: JSON.stringify(payload) }),
  retryReview: (reviewId: string) => request<TriggerResponse>(`/reviews/${reviewId}/retry/`, { method: 'POST' }),
  notifyReview: (reviewId: string) => request<Record<string, unknown>>(`/reviews/${reviewId}/notify/`, { method: 'POST' }),
  notifyMergeResult: (reviewId: string) => request<Record<string, unknown>>(`/reviews/${reviewId}/merge-notify/`, { method: 'POST' }),
  updateSummary: (reviewId: string, summary: string) => request<BackendReview>(`/reviews/${reviewId}/summary/`, { method: 'PATCH', body: JSON.stringify({ summary }) }),
  publishComment: (reviewId: string, commentId: string) => request<BackendComment>(`/reviews/${reviewId}/comments/${commentId}/publish/`, { method: 'POST' }).then(mapComment),
  resolveComment: (reviewId: string, commentId: string) => request<BackendComment>(`/reviews/${reviewId}/comments/${commentId}/resolve/`, { method: 'POST' }).then(mapComment),
  modelConfig: () => request<Record<string, unknown>>('/model-config/'),
  updateModelConfig: (payload: Record<string, unknown>) => request<Record<string, unknown>>('/model-config/', { method: 'PATCH', body: JSON.stringify(payload) }),
  testModelConfig: (payload: Record<string, unknown> = {}) => request<Record<string, unknown>>('/model-config/test/', { method: 'POST', body: JSON.stringify(payload) }),
  engineConfig: () => request<GlobalEngineConfig>('/engine-config/'),
  updateEngineConfig: (engine: string) => request<GlobalEngineConfig>('/engine-config/', { method: 'PATCH', body: JSON.stringify({ default_engine: engine }) }),
  testEngineConfig: (engine: string) => request<{ ok: boolean; engine: string; version?: string; error?: string }>('/engine-config/test/', { method: 'POST', body: JSON.stringify({ engine }) }),
  gitlabConfig: () => request<BackendGitLabConfig>('/gitlab-config/'),
  updateGitlabConfig: (payload: Record<string, unknown>) => request<BackendGitLabConfig>('/gitlab-config/', { method: 'PATCH', body: JSON.stringify(payload) }),
  testGitlabConfig: (payload: Record<string, unknown> = {}) => request<Record<string, unknown>>('/gitlab-config/test/', { method: 'POST', body: JSON.stringify(payload) }),
  notificationConfig: () => request<Record<string, unknown>>('/notification-config/'),
  projectAuditLogs: (projectId: string) => request<Array<Record<string, unknown>>>(`/projects/${encodeURIComponent(projectId)}/audit-logs/?category=notification`),
  auditLogs: (projectId?: string) => request<Array<Record<string, unknown>>>(`/audit-logs/?category=notification${projectId ? `&project=${encodeURIComponent(projectId)}` : ''}`),
  updateNotificationConfig: (payload: Record<string, unknown>) => request<Record<string, unknown>>('/notification-config/', { method: 'PATCH', body: JSON.stringify(payload) }),
  testNotificationConfig: (payload: Record<string, unknown> = {}) => request<Record<string, unknown>>('/notification-config/test/', { method: 'POST', body: JSON.stringify(payload) }),
  systemStatus: () => request<Record<string, unknown>>('/system/'),
  retryFailedJobs: () => request<{ count: number; job_ids: string[]; status: string }>('/system/retry/', { method: 'POST' }),
  mergeGate: (projectId?: string) => request<MergeGateResponse>(`/merge-gate/${projectId ? `?project=${encodeURIComponent(projectId)}` : ''}`),
  mergeGateDryRun: (projectId: string, reviewId: string) => request<GateAssessment>(`/merge-gate/dry-run/?project=${encodeURIComponent(projectId)}`, { method: 'POST', body: JSON.stringify({ review_id: reviewId }) }),
  updateMergeGate: (projectId: string, payload: Record<string, unknown>) => request<MergeGateResponse>(`/merge-gate/?project=${encodeURIComponent(projectId)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  mergeReview: (reviewId: string, payload: Record<string, unknown> = {}) => request<Record<string, unknown>>(`/reviews/${reviewId}/merge/`, { method: 'POST', body: JSON.stringify(payload) }),
}
