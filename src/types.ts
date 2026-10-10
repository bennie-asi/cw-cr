export type PageId =
  | 'overview'
  | 'projects'
  | 'mr-list'
  | 'history'
  | 'review-detail'
  | 'policy'
  | 'models'
  | 'prompts'
  | 'notifications'
  | 'system'
  | 'members'
  | 'users'
  | 'runtime'
  | 'merge-gate'

export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

export type Project = {
  currentUserRole?: 'admin' | 'member' | null
  id: string
  name: string
  path: string
  repositories?: { path: string; gitlab_project_id?: string; gitlab_url?: string }[]
  enabled?: boolean
  modelInherit?: boolean
  notificationInherit?: boolean
  statusReasons?: string[]
  automationEnabled?: boolean
  syncEnabled?: boolean
  owner: string
  reviews: number
  blockers: number
  status: '运行中' | '待配置' | '已暂停'
  model: string
  notify: '继承全局' | '已覆盖' | '未配置'
  updatedAt: string
}

export type Review = {
  id: string
  projectId?: string
  repositoryPath?: string
  mr: string
  project: string
  branch: string
  source: 'GitLab Webhook' | '定时同步' | '/review' | '/improve' | '手动触发'
  status: '已完成' | '排队中' | '运行中' | '有阻断' | '失败'
  generationState?: string
  publicationState?: string
  notificationState?: string
  blockers: number
  comments: number
  createdAt: string
  duration: string
  engineId?: string
  engineVersion?: string
  engineModel?: string
}

export type LineComment = {
  id: string
  file: string
  line: number
  side?: 'old' | 'new'
  location?: {
    status: 'exact' | 'relocated' | 'unverified'
    new_line?: number | null
    old_line?: number | null
    original_new_line?: number | null
    original_old_line?: number | null
    reason: string
  }
  level: 'P0' | 'P1' | 'P2' | 'UNRATED'
  title: string
  body: string
  status: '待发布' | '已发布' | '已解决' | '发布失败'
}

export type ToastState = {
  tone: 'success' | 'info' | 'warning'
  message: string
} | null

export type PolicyState = {
  webhookTrigger: boolean
  commandReview: boolean
  commandImprove: boolean
  security: boolean
  reliability: boolean
  style: boolean
  blockOnP1: boolean
  blockOnP2?: boolean
  securityPrompt?: string
  reliabilityPrompt?: string
  stylePrompt?: string
  effectivePrompts?: { security_prompt?: string; reliability_prompt?: string; style_prompt?: string }
  platformPrompts?: { security_prompt?: string; reliability_prompt?: string; style_prompt?: string }
  prompt: string
}
