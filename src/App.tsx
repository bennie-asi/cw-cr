import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ActionMenu } from './ActionMenu'
import './menus.css'
import { parseWorkspaceRoute, workspacePath } from './workspaceRoutes'
import { notificationFeedback } from './notificationFeedback'
import { notificationDeliveryStatus } from './notificationHistory'
import { MergeRequests } from './MergeRequests'
import { ResizableTable } from './ResizableTable'
import { tableColumns } from './tableColumns'
import { OverviewCoverage } from './OverviewCoverage'
import { ReviewHistory } from './ReviewHistory'
import { ReviewRecheck } from './ReviewRecheck'
import type { HistoryFilters } from './ReviewHistory'
import { ProjectDrawer } from './ProjectOnboarding'
import type { ProjectDraft } from './ProjectOnboarding'
import { ProjectQuickAccess } from './ProjectQuickAccess'
import { EffectiveConfigSummary } from './EffectiveConfigSummary'
import { GitLabEffectiveSummary } from './GitLabEffectiveSummary'
import { AccountMenu } from './AccountMenu'
import { ProjectMembers } from './ProjectMembers'
import { UserManagement } from './UserManagement'
import { GlobalPrompts } from './GlobalPrompts'
import { HelpMenu } from './HelpMenu'
import { ProjectReviewPolicy, thresholdLabel } from './ProjectReviewPolicy'
import { InheritanceDialog } from './InheritanceDialog'
import { useDraftState } from './useDraftState'
import { useConfigSave } from './useConfigSave'
import { useUnsavedNavigation } from './useUnsavedNavigation'
import type { MouseEventHandler, ReactNode } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Bell,
  Bot,
  Bug,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Code2,
  Copy,
  Eye,
  ExternalLink,
  FileCheck,
  FileWarning,
  Filter,
  Folder,
  GitBranch,
  GitMerge,
  GitPullRequest,
  History,
  LayoutDashboard,
  Lock,
  Menu,
  MessageSquare,
  MoreHorizontal,
  Play,
  Plus,
  RefreshCw,
  Rocket,
  Save,
  Search,
  Send,
  Server,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Terminal,
  Webhook,
  X,
  XCircle,
  Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, apiUrl, mapComment, mapReview } from './api'
import type { BackendGitLabConfig, BackendReview, EngineCatalogItem, GateAssessment, GlobalEngineConfig, MergeGateResponse, ProjectEngineConfig, SessionUser } from './api'
// This module is imported only for Vitest's test build. Production state starts
// empty and is hydrated from the Django API on every page load.
import { DEFAULT_POLICY, LINE_COMMENTS, OVERVIEW_STATS, PROJECTS, REVIEW_DIFF, REVIEWS } from './test-fixtures'
import type { LineComment, PageId, PolicyState, Project, Review, StatusTone, ToastState } from './types'

type NavItem = { id: PageId; label: string; icon: LucideIcon }

const DEMO_ENGINE_CATALOG: EngineCatalogItem[] = [
  { id: 'builtin', label: '智审台内置评审', capabilities: ['行级评论', '中文总评'], availability: 'ready', installed: true, version: 'builtin', verified: true, selectable: true, reason: '内置引擎可用', verified_at: null },
  { id: 'pr-agent', label: 'PR-Agent', capabilities: ['结构化行级问题', '改进建议'], availability: 'unverified', installed: true, version: 'PR-Agent 0.46.0', verified: false, selectable: false, reason: '测试数据：尚未验证', verified_at: null },
  { id: 'open-code-review', label: 'OpenCodeReview', capabilities: ['结构化行级问题', '代码替换建议'], availability: 'unverified', installed: true, version: 'OpenCodeReview v1.12.7', verified: false, selectable: false, reason: '测试数据：尚未验证', verified_at: null },
  { id: 'pi-agent', label: 'Pi Agent', capabilities: ['只读代码上下文', '建议替换'], availability: 'unverified', installed: true, version: '0.86.0', verified: false, selectable: false, reason: '测试数据：尚未验证', verified_at: null },
]
const DEMO_GLOBAL_ENGINE_CONFIG: GlobalEngineConfig = { default_engine: 'builtin', effective_engine: 'builtin', engines: DEMO_ENGINE_CATALOG }
const DEMO_PROJECT_ENGINE_CONFIG: ProjectEngineConfig = { inherited: true, configured_engine: 'builtin', effective_engine: 'builtin', engines: DEMO_ENGINE_CATALOG }

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: '工作台',
    items: [
      { id: 'overview', label: '总览', icon: LayoutDashboard },
      { id: 'projects', label: '项目管理', icon: Folder },
    ],
  },
  {
    label: '审查中心',
    items: [
      { id: 'history', label: '评审历史', icon: History },
      { id: 'review-detail', label: 'MR 行级审查', icon: GitPullRequest },
    ],
  },
  {
    label: '平台配置',
    items: [
      { id: 'policy', label: '项目策略', icon: SlidersHorizontal },
      { id: 'models', label: '模型与引擎', icon: Bot },
      { id: 'prompts', label: '提示词配置', icon: MessageSquare },
      { id: 'notifications', label: '通知配置', icon: Bell },
      { id: 'system', label: '系统设置', icon: Settings },
      { id: 'merge-gate', label: '自动合并门禁', icon: GitMerge },
    ],
  },
]

const PAGE_META: Record<PageId, { eyebrow: string; title: string; description: string }> = {
  prompts: { eyebrow: '平台配置', title: '提示词配置', description: '管理所有评审引擎共用的默认提示词与专项检查要求。' },
  users: { eyebrow: '平台配置', title: '系统设置', description: '维护平台内置登录账号；项目权限在各项目空间配置。' },
  runtime: { eyebrow: '平台配置', title: '系统设置', description: '观察任务队列、Webhook 和平台操作审计。' },
  members: { eyebrow: '项目配置', title: '成员权限', description: '管理当前项目成员与角色，平台账号仍由平台管理员统一管理。' },
  'mr-list': { eyebrow: '项目空间', title: 'MR 列表', description: '查看当前项目的 GitLab MR 并触发评审。' },
  overview: { eyebrow: '工作台', title: '总览', description: '掌握团队代码审查的运行状态与质量趋势。' },
  projects: { eyebrow: '工作台 / 项目', title: '项目管理', description: '管理 GitLab 项目接入、模型继承和通知覆盖。' },
  history: { eyebrow: '审查中心', title: '评审历史', description: '追踪每次 MR 评审从触发到门禁的完整链路。' },
  'review-detail': { eyebrow: '审查中心 / MR', title: 'MR 行级审查详情', description: '在具体代码行上查看、发布和解决 AI 评审意见。' },
  policy: { eyebrow: '平台配置', title: '项目策略配置', description: '控制自动触发、评审规则、提示词和阻断门槛。' },
  models: { eyebrow: '平台配置', title: '模型与引擎', description: '配置模型网关，并管理项目使用的审查引擎。' },
  notifications: { eyebrow: '平台配置', title: '通知配置', description: '让企业微信收到清晰、可追溯的评审结果。' },
  system: { eyebrow: '平台配置', title: '系统设置', description: '观察 Webhook、任务队列、重试与平台操作记录。' },
  'merge-gate': { eyebrow: '平台配置', title: '自动合并门禁', description: '在合并前拦截高优先级问题，并把结果通知到团队。' },
}

const cn = (...classes: Array<string | false | undefined>) => classes.filter(Boolean).join(' ')
const appMode = () => import.meta.env.PROD ? 'production' : (globalThis as typeof globalThis & { process?: { env?: Record<string, string | undefined> } }).process?.env?.MODE ?? import.meta.env.MODE

const policyToApi = (value: PolicyState) => ({
  webhook_trigger: value.webhookTrigger,
  command_review: value.commandReview,
  command_improve: value.commandImprove,
  security_checks: value.security,
  reliability_checks: value.reliability,
  style_checks: value.style,
  block_on_p1: value.blockOnP1,
  block_on_p2: value.blockOnP2 === true,
  security_prompt: value.securityPrompt || '',
  reliability_prompt: value.reliabilityPrompt || '',
  style_prompt: value.stylePrompt || '',
  summary_prompt: value.prompt,
})

const policyFromApi = (value: Record<string, unknown>): PolicyState => ({
  webhookTrigger: value.webhook_trigger !== false,
  commandReview: value.command_review !== false,
  commandImprove: value.command_improve !== false,
  security: value.security_checks !== false,
  reliability: value.reliability_checks !== false,
  style: value.style_checks === true,
  blockOnP1: value.block_on_p1 !== false,
  blockOnP2: value.block_on_p2 === true,
  securityPrompt: typeof value.security_prompt === 'string' ? value.security_prompt : '',
  reliabilityPrompt: typeof value.reliability_prompt === 'string' ? value.reliability_prompt : '',
  stylePrompt: typeof value.style_prompt === 'string' ? value.style_prompt : '',
  effectivePrompts: value.effective_prompts && typeof value.effective_prompts === 'object' ? value.effective_prompts as PolicyState['effectivePrompts'] : {},
  platformPrompts: value.default_prompts && typeof value.default_prompts === 'object' ? value.default_prompts as PolicyState['platformPrompts'] : {},
  prompt: typeof value.summary_prompt === 'string' && value.summary_prompt ? value.summary_prompt : DEFAULT_POLICY.prompt,
})

export function normalizeGateAssessment(value: unknown): GateAssessment | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  const checks = candidate.checks
  if (!checks || typeof checks !== 'object' || Array.isArray(checks)) return null
  if (!Object.keys(checks).length || Object.values(checks).some(item => typeof item !== 'boolean')) return null
  if (typeof candidate.project_id !== 'string' || typeof candidate.review_id !== 'string' || typeof candidate.passed !== 'boolean') return null
  return candidate as unknown as GateAssessment
}

function App({ user, onLogout }: { user: SessionUser; onLogout: () => Promise<void> }) {
  const dirtyForm = useRef(false)
  const onDirtyChange = useCallback((dirty: boolean) => { dirtyForm.current = dirty }, [])
  const navigationGuard = useUnsavedNavigation(dirtyForm)
  const guardedLogout = () => new Promise<void>((resolve, reject) => {
    const wasDirty = dirtyForm.current
    navigationGuard.request(() => {
      void onLogout().then(resolve).catch(error => { dirtyForm.current = wasDirty; reject(error) })
    }, resolve)
  })
  const historyOwner = useRef(crypto.randomUUID())
  const historyIndex = useRef(0)
  const currentUrl = useRef(window.location.pathname + window.location.search + window.location.hash)
  const historyRollback = useRef<(() => void) | null>(null)
  const historyReplay = useRef(false)
  const platformAdmin = user.platform_role === 'admin'
  const initialRoute = useRef(parseWorkspaceRoute(window.location.pathname))
  const [invalidRoute, setInvalidRoute] = useState(!initialRoute.current)
  const [globalQuery, setGlobalQuery] = useState('')
  const detailRequest = useRef(0)
  const navigationVersion = useRef(0)
  const [modelProbeMessage, setModelProbeMessage] = useState('')
  const testFixturesEnabled = !import.meta.env.PROD && appMode() === 'test'
  const [page, setPage] = useState<PageId>(initialRoute.current?.page || 'overview')
  const [mobileNav, setMobileNav] = useState(false)
  const [projects, setProjects] = useState<Project[]>(testFixturesEnabled ? PROJECTS : [])
  const [reviews, setReviews] = useState<Review[]>(testFixturesEnabled ? REVIEWS : [])
  const [comments, setComments] = useState<LineComment[]>(testFixturesEnabled ? LINE_COMMENTS : [])
  const [backendConnected, setBackendConnected] = useState(false)
  const [activeProjectId, setActiveProjectId] = useState<string | null>(initialRoute.current?.projectId || null)
  const projectContext = useRef({ id: activeProjectId, version: 0 })
  if (projectContext.current.id !== activeProjectId) {
    projectContext.current = { id: activeProjectId, version: projectContext.current.version + 1 }
  }
  const [loadedProjectId, setLoadedProjectId] = useState<string | null>(null)
  const [projectLoadError, setProjectLoadError] = useState(false)
  const [activeReviewId, setActiveReviewId] = useState<string | null>(initialRoute.current?.reviewId || null)
  const [activeReviewDetail, setActiveReviewDetail] = useState<BackendReview | null>(null)
  const [policy, setPolicy] = useState<PolicyState>(DEFAULT_POLICY)
  const [policySaveError, setPolicySaveError] = useState('')
  const savedPolicy = useRef<PolicyState>(DEFAULT_POLICY)
  const policySaves = useRef(new Set<string>())
  const [pendingPolicyProjects, setPendingPolicyProjects] = useState(new Set<string>())
  const policyCurrent = useRef(policy)
  policyCurrent.current = policy
  const [toast, setToast] = useState<ToastState>(null)
  const [projectDrawer, setProjectDrawer] = useState<'create' | Project | null>(null)
  const [projectSavePending, setProjectSavePending] = useState(false)
  const [projectQuery, setProjectQuery] = useState('')
  const [projectFilter, setProjectFilter] = useState<'全部' | Project['status']>('全部')
  const [historyFilters, setHistoryFilters] = useState<HistoryFilters>({ project: '', source: '全部', exception: 'all' })
  const historyReturn = useRef<{ projectId: string | null; scroll: number; reviews: Review[]; restoring: boolean } | null>(null)
  useLayoutEffect(() => {
    const previous = historyReturn.current
    if (page === 'history' && previous?.projectId === activeProjectId && previous.restoring) {
      if (reviews !== previous.reviews) {
        // Restore the full list before the browser can clamp the saved scroll.
        setReviews(previous.reviews)
        return
      }
      document.documentElement.scrollTop = previous.scroll
      document.body.scrollTop = previous.scroll
      previous.restoring = false
    }
  }, [page, activeProjectId, reviews])
  const [modelTested, setModelTested] = useState(false)
  const [modelCatalog, setModelCatalog] = useState<string[]>([])
  const [notificationTested, setNotificationTested] = useState(false)
  const [notificationTesting, setNotificationTesting] = useState(false)
  const notificationTestRequest = useRef(0)
  const notificationInFlight = useRef(false)
  const [gateDryRun, setGateDryRun] = useState(false)
  const [gateDryRunPassed, setGateDryRunPassed] = useState(false)
  const [gateEnabled, setGateEnabled] = useState(false)
  const [gateAssessment, setGateAssessment] = useState<GateAssessment | null>(null)
  const [selectedGateReviewId, setSelectedGateReviewId] = useState<string | null>(null)
  const [gateAction, setGateAction] = useState<'dry-run' | 'toggle' | null>(null)
  const [gateError, setGateError] = useState('')
  const gateRequest = useRef(0)
  const [systemStats, setSystemStats] = useState<Record<string, unknown> | null>(null)
  const [projectAudit, setProjectAudit] = useState<{ projectId: string; rows: Array<Record<string, unknown>>; status: 'loading' | 'ready' | 'error' } | null>(null)
  const [auditRefreshToken, setAuditRefreshToken] = useState(0)
  const [overviewStats, setOverviewStats] = useState<Record<string, unknown> | null>(testFixturesEnabled ? OVERVIEW_STATS : null)
  const [modelConfigData, setModelConfigData] = useState<Record<string, unknown> | null>(null)
  const [gitlabConfigData, setGitlabConfigData] = useState<BackendGitLabConfig | null>(null)
  const [gitlabTested, setGitlabTested] = useState(false)
  const [projectModelConfigData, setProjectModelConfigData] = useState<Record<string, unknown> | null>(null)
  const [notificationConfigData, setNotificationConfigData] = useState<Record<string, unknown> | null>(null)
  const [globalConfigStatus, setGlobalConfigStatus] = useState<Record<'models' | 'engines' | 'notifications' | 'system', 'loading' | 'ready' | 'error'>>({ models: 'loading', engines: 'loading', notifications: 'loading', system: 'loading' })
  const [globalEngineConfigData, setGlobalEngineConfigData] = useState<GlobalEngineConfig | null>(testFixturesEnabled ? DEMO_GLOBAL_ENGINE_CONFIG : null)
  const [projectEngineConfigData, setProjectEngineConfigData] = useState<ProjectEngineConfig | null>(testFixturesEnabled ? DEMO_PROJECT_ENGINE_CONFIG : null)
  const [projectEngineLoadStatus, setProjectEngineLoadStatus] = useState<'loading' | 'ready' | 'error'>(testFixturesEnabled ? 'ready' : 'loading')
  const [pendingEngineScope, setPendingEngineScope] = useState<'global' | 'project' | null>(null)
  const reviewTriggers = useRef(new Set<string>())
  const [pendingReviewTriggerKeys, setPendingReviewTriggerKeys] = useState<string[]>([])
  const [projectNotificationConfigData, setProjectNotificationConfigData] = useState<Record<string, unknown> | null>(null)
  const modelScope = activeProjectId ? 'project' : 'global'
  const notificationScope = modelScope
  useEffect(() => {
    notificationTestRequest.current++
    notificationInFlight.current = false
    setNotificationTested(false)
    setNotificationTesting(false)
  }, [activeProjectId])
  useEffect(() => {
    gateRequest.current += 1
    setSelectedGateReviewId(null)
    setGateAssessment(null)
    setGateDryRun(false)
    setGateDryRunPassed(false)
    setGateError('')
  }, [activeProjectId])
  const currentProject = projects.find(project => project.id === activeProjectId)
  const projectConfigReadOnly = Boolean(activeProjectId && !platformAdmin && currentProject?.currentUserRole !== 'admin' && ['policy', 'models', 'notifications', 'merge-gate'].includes(page))
  const modelTestRequest = useRef(0)
  useEffect(() => {
    modelTestRequest.current += 1
    setModelTested(false)
    setModelCatalog([])
    setModelProbeMessage('')
  }, [modelScope, activeProjectId])
  const [backendRefreshToken, setBackendRefreshToken] = useState(0)

  const currentMeta = { ...PAGE_META[page], eyebrow: activeProjectId ? `项目空间 / ${projects.find(p => p.id === activeProjectId)?.name || activeProjectId}` : '平台工作台' }
  const navGroups = activeProjectId ? [
    { label: '项目审查', items: [{id:'mr-list' as PageId,label:'MR 列表',icon:GitPullRequest}, {id:'history' as PageId,label:'评审历史',icon:History}] },
    { label: '项目配置', items: [...NAV_GROUPS[2].items.filter(item => !['system', 'prompts'].includes(item.id)), { id: 'members' as PageId, label: '成员权限', icon: ShieldCheck }] },
  ] : NAV_GROUPS.filter(group => platformAdmin || group.label !== '平台配置').map(group => ({...group, items:group.items.filter(item => !['policy','merge-gate','review-detail'].includes(item.id))}))
  const scopedReviews = activeProjectId ? reviews.filter(r => r.projectId === activeProjectId || (testFixturesEnabled && r.project === projects.find(p => p.id === activeProjectId)?.name)) : reviews
  const unresolvedBlockers = comments.filter((comment) => comment.level !== 'P2' && comment.status !== '已解决').length

  const notify = (message: string, tone: 'success' | 'info' | 'warning' = 'success') => {
    setToast({ message, tone })
    window.setTimeout(() => setToast(null), 3600)
  }

  useEffect(() => {
    if (appMode() === 'test') return
    let cancelled = false
    const loadBackend = async () => {
      try {
        await api.health()
        const remoteProjects = await api.listProjects()
        if (cancelled) return
        setBackendConnected(true)
        setProjects(remoteProjects)
        // A project outside the visible list is inaccessible, not a failed
        // backend connection. Do not request its scoped data or show old data.
        if (activeProjectId && !remoteProjects.some(project => project.id === activeProjectId)) {
          setReviews([])
          setActiveReviewDetail(null)
          setComments([])
          return
        }
        const remoteReviews = await api.listReviews(activeProjectId || undefined)
        if (cancelled) return
        void api.overview().then(value => { if (!cancelled) setOverviewStats(value) }).catch(() => undefined)
        if (platformAdmin) {
          void api.systemStatus().then(value => { if (!cancelled) setSystemStats(value) }).catch(() => undefined)
        }
        setReviews(remoteReviews)
        if (activeReviewId) {
          try {
            const requestVersion = detailRequest.current
            const remoteReview = await api.getReview(activeReviewId)
            if (!cancelled && detailRequest.current === requestVersion) {
              if (remoteReview.id !== activeReviewId || (activeProjectId && remoteReview.project !== activeProjectId)) { setInvalidRoute(true); return }
              setActiveReviewDetail(remoteReview)
              setComments((remoteReview.comments || []).map(mapComment))
            }
          } catch {
            // The list view remains useful even when detail hydration is unavailable.
          }
        } else {
          setActiveReviewId(null)
          setActiveReviewDetail(null)
          setComments([])
        }
      } catch {
        if (!cancelled) { setBackendConnected(false); notify('后端数据加载失败，请点击刷新重试', 'warning') }
      }
    }
    void loadBackend()
    return () => { cancelled = true }
  }, [backendRefreshToken, activeProjectId, activeReviewId, platformAdmin])

  useEffect(() => {
    if (testFixturesEnabled || !backendConnected || !platformAdmin) return
    let cancelled = false
    setGlobalConfigStatus({ models: 'loading', engines: 'loading', notifications: 'loading', system: 'loading' })
    function load<T>(key: 'models' | 'engines' | 'notifications' | 'system', request: Promise<T>, accept: (value: T) => void) {
      void request.then(value => {
        if (cancelled) return
        accept(value)
        setGlobalConfigStatus(current => ({ ...current, [key]: 'ready' }))
      }).catch(() => {
        if (!cancelled) setGlobalConfigStatus(current => ({ ...current, [key]: 'error' }))
      })
    }
    load('models', api.modelConfig(), setModelConfigData)
    load('engines', api.engineConfig(), setGlobalEngineConfigData)
    load('notifications', api.notificationConfig(), setNotificationConfigData)
    load('system', api.gitlabConfig(), setGitlabConfigData)
    return () => { cancelled = true }
  }, [backendConnected, backendRefreshToken, platformAdmin, testFixturesEnabled])

  useEffect(() => {
    if (!backendConnected || (activeProjectId && !currentProject)) return
    let cancelled = false
    let busy = false
    const timer = window.setInterval(async () => {
      if (busy || reviewTriggers.current.size) return
      busy = true
      const requestVersion = detailRequest.current
      try {
        const [latestReviews, latestSystem, latestOverview] = await Promise.all([
          api.listReviews(activeProjectId || undefined), platformAdmin ? api.systemStatus() : Promise.resolve(null), api.overview(),
        ])
        if (cancelled || detailRequest.current !== requestVersion) return
        setReviews(latestReviews)
        setSystemStats(latestSystem)
        setOverviewStats(latestOverview)
        if (activeReviewId) {
          const detail = await api.getReview(activeReviewId)
          if (!cancelled && !reviewTriggers.current.size && detailRequest.current === requestVersion) {
            if (detail.id !== activeReviewId || detail.project !== activeProjectId) return
            setActiveReviewDetail(detail)
            setComments((detail.comments || []).map(mapComment))
          }
        }
      } catch {
        // Keep the current view during transient polling failures; manual refresh
        // performs the full connection check without overwriting config drafts.
      } finally { busy = false }
    }, 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [backendConnected, activeReviewId, activeProjectId, currentProject?.id, platformAdmin])

  useEffect(() => {
    if (!backendConnected || !activeProjectId || !currentProject) return
    let cancelled = false
    setLoadedProjectId(null)
    setProjectLoadError(false)
    setProjectModelConfigData(null)
    setProjectNotificationConfigData(null)
    const loadProjectScope = async () => {
      try {
        const [remotePolicy, remoteModel, remoteNotification, remoteGate] = await Promise.all([
          api.getPolicy(activeProjectId),
          api.projectModelConfig(activeProjectId),
          api.projectNotificationConfig(activeProjectId),
          api.mergeGate(activeProjectId),
        ])
        if (cancelled) return
        savedPolicy.current = policyFromApi(remotePolicy)
        setPolicy(savedPolicy.current)
        setProjectModelConfigData(remoteModel)
        setProjectNotificationConfigData(remoteNotification)
        setGateEnabled(remoteGate.enabled === true)
        const assessment = normalizeGateAssessment(remoteGate.assessment)
        setGateAssessment(assessment)
        setSelectedGateReviewId(typeof assessment?.review_id === 'string' ? assessment.review_id : null)
        // Only a structurally valid assessment represents a completed dry-run.
        // Older records may contain a stale timestamp with an empty `{}`
        // assessment; showing that as completed leaves the page with no
        // usable result to display.
        setGateDryRun(Boolean(assessment?.completed_at))
        setGateDryRunPassed(assessment?.passed === true && remoteGate.dry_run_passed === true)
        setGateError('')
        setLoadedProjectId(activeProjectId)
      } catch {
        if (!cancelled) { setProjectLoadError(true); notify('项目配置加载失败，请刷新后再保存', 'warning') }
      }
    }
    void loadProjectScope()
    return () => { cancelled = true }
  }, [backendConnected, activeProjectId, currentProject?.id, backendRefreshToken])

  useEffect(() => {
    if (testFixturesEnabled || !backendConnected || !activeProjectId || !currentProject) return
    let cancelled = false
    setProjectEngineConfigData(null)
    setProjectEngineLoadStatus('loading')
    void api.projectEngineConfig(activeProjectId).then(config => {
      if (cancelled) return
      setProjectEngineConfigData(config)
      setProjectEngineLoadStatus('ready')
    }).catch(() => {
      if (cancelled) return
      setProjectEngineLoadStatus('error')
    })
    return () => { cancelled = true }
  }, [backendConnected, activeProjectId, currentProject?.id, backendRefreshToken, testFixturesEnabled])

  useEffect(() => {
    if (!backendConnected || !activeProjectId || !currentProject || page !== 'notifications') return
    let cancelled = false
    const projectId = activeProjectId
    const context = projectContext.current
    setProjectAudit({ projectId, rows: [], status: 'loading' })
    void api.projectAuditLogs(projectId).then(rows => {
      if (!cancelled && context === projectContext.current) setProjectAudit({ projectId, rows, status: 'ready' })
    }).catch(() => {
      if (!cancelled && context === projectContext.current) setProjectAudit({ projectId, rows: [], status: 'error' })
    })
    return () => { cancelled = true }
  }, [backendConnected, activeProjectId, currentProject?.id, page, auditRefreshToken, backendRefreshToken])

  const navigateNow = (nextPage: PageId, projectId: string | null = activeProjectId, reviewId: string | null = null) => {
    setPolicySaveError('')
    dirtyForm.current = false
    setPolicy(savedPolicy.current)
    if (['overview', 'projects', 'system', 'users', 'runtime'].includes(nextPage)) projectId = null
    if (!projectId && ['policy','merge-gate','mr-list','review-detail','members'].includes(nextPage)) { nextPage = 'projects'; reviewId = null }
    const path = workspacePath(nextPage, projectId, reviewId)
    navigationVersion.current += 1
    if (window.location.pathname !== path) {
      historyIndex.current++
      window.history.pushState({ cwCr: { owner: historyOwner.current, index: historyIndex.current } }, '', path)
    }
    currentUrl.current = window.location.pathname + window.location.search + window.location.hash
    setInvalidRoute(false)
    setActiveProjectId(projectId)
    if (nextPage !== 'review-detail') { setActiveReviewId(null); setActiveReviewDetail(null) }
    setPage(nextPage)
    setMobileNav(false)
    document.documentElement.scrollTop = 0
    document.body.scrollTop = 0
  }
  const navigate = (nextPage: PageId, projectId: string | null = activeProjectId, reviewId: string | null = null) => {
    if (['overview', 'projects', 'system', 'users', 'runtime'].includes(nextPage)) projectId = null
    if (!projectId && ['policy','merge-gate','mr-list','review-detail','members'].includes(nextPage)) { nextPage = 'projects'; reviewId = null }
    if (!invalidRoute && window.location.pathname === workspacePath(nextPage, projectId, reviewId)) return
    navigationGuard.request(() => navigateNow(nextPage, projectId, reviewId))
  }

  useEffect(() => {
    window.history.replaceState({ ...window.history.state, cwCr: { owner: historyOwner.current, index: historyIndex.current } }, '', currentUrl.current)
    const applyRoute = () => {
      setPolicySaveError('')
      dirtyForm.current = false
      setPolicy(savedPolicy.current)
      navigationVersion.current += 1
      const route = parseWorkspaceRoute(window.location.pathname)
      if (!route) { setInvalidRoute(true); return }
      setInvalidRoute(false)
      detailRequest.current += 1
      setPage(route.page)
      setActiveProjectId(route.projectId)
      setActiveReviewId(route.reviewId)
      setActiveReviewDetail(null)
      if (!testFixturesEnabled) setComments([])
    }
    const restore = (event: PopStateEvent) => {
      const marker = event.state?.cwCr
      const known = marker?.owner === historyOwner.current && Number.isInteger(marker.index)
      const destination = window.location.pathname + window.location.search + window.location.hash
      if (historyRollback.current) {
        const afterRollback = historyRollback.current
        historyRollback.current = null
        afterRollback()
        return
      }
      if (dirtyForm.current && !historyReplay.current) {
        if (known && marker.index !== historyIndex.current) {
          const delta = marker.index - historyIndex.current
          historyRollback.current = () => navigationGuard.request(() => {
            historyReplay.current = true
            window.history.go(delta)
          })
          window.history.go(-delta)
        } else {
          // Compatibility for entries created outside this mounted router.
          window.history.replaceState({ cwCr: { owner: historyOwner.current, index: historyIndex.current } }, '', currentUrl.current)
          navigationGuard.request(() => {
            historyIndex.current++
            window.history.pushState({ cwCr: { owner: historyOwner.current, index: historyIndex.current } }, '', destination)
            currentUrl.current = destination
            applyRoute()
          })
        }
        return
      }
      historyReplay.current = false
      if (known) historyIndex.current = marker.index
      currentUrl.current = destination
      applyRoute()
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [testFixturesEnabled, navigationGuard.request])

  const openReview = (id: string) => navigationGuard.request(() => openReviewNow(id))
  const openReviewNow = (id: string) => {
    const requestId = ++detailRequest.current
    const selected = reviews.find(r => r.id === id)
    const owner = selected?.projectId || projects.find(p => p.name === selected?.project)?.id || activeProjectId
    if (!owner) { notify('无法确定评审所属项目，请刷新后重试', 'warning'); return }
    historyReturn.current = page === 'history' ? { projectId: activeProjectId, scroll: document.documentElement.scrollTop || document.body.scrollTop, reviews, restoring: true } : null
    navigateNow('review-detail', owner, id)
    const version = navigationVersion.current
    setActiveReviewId(id)
    if (backendConnected && !id.startsWith('review-')) {
      setActiveReviewDetail(null)
      setComments([])
      void api.getReview(id)
        .then((detail) => {
          if (detailRequest.current !== requestId || version !== navigationVersion.current) return
          setActiveReviewDetail(detail)
          if (detail.project !== owner || detail.id !== id) { setInvalidRoute(true); setActiveReviewDetail(null); return }
          setComments((detail.comments || []).map(mapComment))
        })
        .catch(() => { if (detailRequest.current === requestId && version === navigationVersion.current) notify('评审详情加载失败，请刷新重试', 'warning') })
    } else {
      const local = reviews.find((item) => item.id === id)
      setActiveReviewDetail(local ? {
        id: local.id,
        mr_iid: Number(local.mr.replace('!', '')),
        title: local.mr,
        project_name: local.project,
        branch: local.branch,
        source: local.source,
        status: local.status === '已完成' ? 'completed' : local.status === '有阻断' ? 'blocked' : local.status === '失败' ? 'failed' : 'running',
        blocking_comment_count: local.blockers,
        comment_count: local.comments,
        changed_files: testFixturesEnabled ? Array.from(new Set(LINE_COMMENTS.map((comment) => comment.file))) : [],
        diff_text: testFixturesEnabled && local.id === 'review-357' ? REVIEW_DIFF : '',
        additions: testFixturesEnabled && local.id === 'review-357' ? 4 : 0,
        deletions: testFixturesEnabled && local.id === 'review-357' ? 4 : 0,
        summary: testFixturesEnabled && local.id === 'review-357' ? '本次 MR 调整订单查询、Webhook 处理与评审引擎。发现异常处理和幂等保护问题，建议修复阻断问题后再合并。' : '',
      } : null)
    }
  }

  const publishComment = (id: string) => {
    if (backendConnected && activeReviewId && !id.startsWith('comment-')) {
      const version = navigationVersion.current
      void api.publishComment(activeReviewId, id)
        .then((remoteComment) => {
          if (version !== navigationVersion.current || remoteComment.id !== id) return
          setComments((current) => current.map((comment) => comment.id === id ? remoteComment : comment))
          notify('行级评论已发布到 GitLab Discussion')
        })
        .catch(() => { if (version === navigationVersion.current) notify('评论发布失败，请检查 GitLab 配置', 'warning') })
      return
    }
    if (!testFixturesEnabled) {
      notify('后端未连接，无法发布行级评论', 'warning')
      return
    }
    setComments((current) => current.map((comment) => (comment.id === id ? { ...comment, status: '已发布' } : comment)))
    notify('行级评论已发布到 GitLab Discussion')
  }

  const resolveComment = (id: string) => {
    if (backendConnected && activeReviewId && !id.startsWith('comment-')) {
      const version = navigationVersion.current
      void api.resolveComment(activeReviewId, id)
        .then((remoteComment) => {
          if (version !== navigationVersion.current || remoteComment.id !== id) return
          setComments((current) => current.map((comment) => comment.id === id ? remoteComment : comment))
          notify('已标记为解决，门禁状态将重新计算')
        })
        .catch(() => { if (version === navigationVersion.current) notify('评论解决失败，请检查 GitLab 配置', 'warning') })
      return
    }
    if (!testFixturesEnabled) {
      notify('后端未连接，无法解决行级评论', 'warning')
      return
    }
    setComments((current) => current.map((comment) => (comment.id === id ? { ...comment, status: '已解决' } : comment)))
    notify('已标记为解决，门禁状态将重新计算')
  }

  const retryReview = (id: string) => {
    if (backendConnected && !id.startsWith('review-')) {
      const version = navigationVersion.current
      return api.retryReview(id)
        .then((result) => {
          if (version !== navigationVersion.current) return
          if (result.review) {
            if (result.review.id !== id || (activeProjectId && result.review.project !== activeProjectId)) {
              notify('返回的评审与当前记录不一致，请刷新后检查任务', 'warning')
              return
            }
            const mapped = mapReview(result.review)
            setReviews((current) => [mapped, ...current.filter((review) => review.id !== mapped.id)])
          }
          notify('已重新加入评审队列', 'info')
        })
        .catch(() => { if (version === navigationVersion.current) notify('重新评审失败，请检查后端任务队列', 'warning') })
    }
    if (!testFixturesEnabled) {
      notify('后端未连接，无法重新排队评审', 'warning')
      return
    }
    setReviews((current) => current.map((review) => (review.id === id ? { ...review, status: '运行中' } : review)))
    notify('已重新加入评审队列', 'info')
  }

  const saveProject = (draft: ProjectDraft) => {
    if (!backendConnected && !testFixturesEnabled) {
      return Promise.reject(new Error('后端未连接，无法保存项目'))
    }
    const editing = projectDrawer && projectDrawer !== 'create' ? projectDrawer : null
    const payload = {
      name: draft.name, owner: draft.owner, repositories: draft.repositories,
      model_inherit: draft.modelInherit, notification_inherit: draft.notificationInherit,
      ...(editing ? { enabled: draft.enabled } : {}),
    }
    if (backendConnected) {
      if (projectSavePending) return Promise.reject(new Error('项目正在保存，请稍候'))
      setProjectSavePending(true)
      return (editing ? api.updateProject(editing.id, payload) : api.createProject(payload))
        .then((remoteProject) => {
          setProjects((current) => [remoteProject, ...current.filter(project => project.id !== remoteProject.id)])
          notify(`项目 ${remoteProject.name} 已${editing ? '更新' : '接入评审平台'}`)
          return remoteProject
        })
        .finally(() => setProjectSavePending(false))
    }
    if (testFixturesEnabled) {
      const project: Project = {
        ...(editing || {}), id: editing?.id || draft.name.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
        name: draft.name, path: draft.repositories[0].path, repositories: draft.repositories,
        owner: draft.owner, reviews: editing?.reviews || 0, blockers: editing?.blockers || 0,
        status: draft.enabled ? '待配置' : '已暂停', enabled: draft.enabled,
        modelInherit: draft.modelInherit, notificationInherit: draft.notificationInherit,
        model: editing?.model || '未配置', notify: editing?.notify || '未配置', updatedAt: '—',
      }
      setProjects((current) => [project, ...current.filter(item => item.id !== project.id)])
      notify(`项目 ${project.name} 已${editing ? '更新' : '接入评审平台'}`)
      return Promise.resolve(project)
    }
    return Promise.reject(new Error('后端未连接，无法保存项目'))
  }

  const toggleProject = (project: Project) => {
    const enabled = project.enabled === false
    if (backendConnected) {
      void api.updateProject(project.id, { enabled }).then(updated => {
        setProjects(current => current.map(item => item.id === updated.id ? updated : item))
        notify(`项目 ${project.name} 已${enabled ? '恢复' : '暂停'}`)
      }).catch(error => notify(error instanceof Error ? error.message : '切换项目状态失败', 'warning'))
    } else if (testFixturesEnabled) {
      setProjects(current => current.map(item => item.id === project.id ? { ...item, enabled, status: enabled ? '待配置' : '已暂停' } : item))
    }
  }

  const selectProject = (projectId: string) => {
    if (projectId === activeProjectId) return
    navigationGuard.request(() => {
    const target = ['policy','models','notifications','merge-gate','history','mr-list','members'].includes(page) ? page : 'mr-list'
    navigateNow(target, projectId)
    const project = projects.find((item) => item.id === projectId)
    if (project) notify(`已切换到项目 ${project.name}`, 'info')
    })
  }

  const updatePolicy = (value: PolicyState) => {
    setPolicy(value)
    dirtyForm.current = JSON.stringify(value) !== JSON.stringify(savedPolicy.current)
  }

  const savePolicy = (value: PolicyState) => {
    if (!value.prompt.trim()) { setPolicySaveError('中文总评提示词不能为空'); return }
    setPolicySaveError('')
    if (backendConnected && activeProjectId) {
      if (loadedProjectId !== activeProjectId || policySaves.current.has(activeProjectId)) return
      const projectId = activeProjectId
      policySaves.current.add(projectId)
      setPendingPolicyProjects(new Set(policySaves.current))
      const context = projectContext.current
      const version = navigationVersion.current
      void api.updatePolicy(activeProjectId, policyToApi(value))
        .then((result) => {
          if (context !== projectContext.current || version !== navigationVersion.current) return
          savedPolicy.current = policyFromApi(result)
          if (JSON.stringify(policyCurrent.current) === JSON.stringify(value)) {
            setPolicy(savedPolicy.current)
            dirtyForm.current = false
          } else dirtyForm.current = JSON.stringify(policyCurrent.current) !== JSON.stringify(savedPolicy.current)
          notify('项目策略已保存')
          if (typeof result.auto_merge_enabled === 'boolean') setGateEnabled(result.auto_merge_enabled)
          if ('dry_run_passed' in result && result.dry_run_passed !== true) {
            setGateDryRun(false)
            setGateDryRunPassed(false)
            setGateAssessment(null)
          }
          // The saved threshold changes existing review counts. Refresh only
          // review data so a newer configuration draft is not rehydrated away.
          void api.listReviews(projectId).then(latestReviews => {
            if (context === projectContext.current) setReviews(latestReviews)
          }).catch(() => {
            if (context === projectContext.current) notify('策略已保存，评审计数刷新失败，请点击刷新数据', 'warning')
          })
        })
        .catch((reason) => { if (context === projectContext.current && version === navigationVersion.current) setPolicySaveError(reason instanceof Error ? reason.message : '项目策略保存失败，请重试') })
        .finally(() => {
          policySaves.current.delete(projectId)
          setPendingPolicyProjects(new Set(policySaves.current))
        })
      return
    }
    if (!testFixturesEnabled) {
      notify('后端未连接，无法保存项目策略', 'warning')
      return
    }
    setPolicy(value)
    savedPolicy.current = value
    dirtyForm.current = false
    notify('项目策略已保存')
  }

  const saveEngineConfig = (payload: Record<string, unknown>, scope: 'global' | 'project' = 'global') => {
    if (pendingEngineScope) return false
    if (backendConnected) {
      if (scope === 'project' && (!activeProjectId || loadedProjectId !== activeProjectId)) return false
      const projectId = activeProjectId
      const context = projectContext.current
      const version = navigationVersion.current
      setPendingEngineScope(scope)
      const request = scope === 'project' && projectId
        ? api.updateProjectEngineConfig(projectId, payload as { engine: string } | { inherited: true })
        : api.updateEngineConfig(String(payload.default_engine || ''))
      return request.then(result => {
        if (version !== navigationVersion.current || (scope === 'project' && context !== projectContext.current)) return false
        if (scope === 'project') setProjectEngineConfigData(result as ProjectEngineConfig)
        else setGlobalEngineConfigData(result as GlobalEngineConfig)
        notify(scope === 'project' ? '项目评审引擎配置已保存' : '平台默认评审引擎已保存')
        return true
      }).catch((error: Error) => {
        if (version === navigationVersion.current && (scope !== 'project' || context === projectContext.current)) notify(error.message || '评审引擎配置保存失败', 'warning')
        return false
      }).finally(() => setPendingEngineScope(current => current === scope ? null : current))
    }
    if (testFixturesEnabled) {
      if (scope === 'project' && projectEngineConfigData) {
        setProjectEngineConfigData(payload.inherited === true
          ? { ...projectEngineConfigData, inherited: true }
          : { ...projectEngineConfigData, inherited: false, configured_engine: String(payload.engine), effective_engine: String(payload.engine) })
      } else if (globalEngineConfigData) {
        const engine = String(payload.default_engine)
        setGlobalEngineConfigData({ ...globalEngineConfigData, default_engine: engine, effective_engine: engine })
      }
      notify(scope === 'project' ? '项目评审引擎配置已保存' : '平台默认评审引擎已保存')
      return true
    }
    notify('后端未连接，无法保存评审引擎配置', 'warning')
    return false
  }

  const testEngineConfig = (engine: string) => {
    if (pendingEngineScope) return
    if (!backendConnected) {
      notify('后端未连接，无法运行引擎推理验证', 'warning')
      return
    }
    const version = navigationVersion.current
    setPendingEngineScope('global')
    void api.testEngineConfig(engine).then(async result => {
      if (version !== navigationVersion.current) return
      if (!result.ok) {
        notify(result.error || `${engine} 推理验证失败`, 'warning')
        return
      }
      const latest = await api.engineConfig()
      if (version !== navigationVersion.current) return
      setGlobalEngineConfigData(latest)
      notify(`${engine} 推理验证成功${result.version ? `：${result.version}` : ''}`)
    }).catch((error: Error) => {
      if (version === navigationVersion.current) notify(error.message || `${engine} 推理验证失败`, 'warning')
    }).finally(() => setPendingEngineScope(current => current === 'global' ? null : current))
  }

  const runReview = (mode: 'review' | 'improve') => {
    if (activeReviewDetail && ['queued', 'running'].some(state => [activeReviewDetail.status, activeReviewDetail.generation_state, activeReviewDetail.execution_state].includes(state))) return
    if (backendConnected && activeProjectId) {
      const targetProjectId = typeof activeReviewDetail?.project === 'string' ? activeReviewDetail.project : activeProjectId
      if (!activeReviewDetail?.mr_iid) {
        notify('请先选择一个项目或已有 MR，再触发评审', 'warning')
        return
      }
      const version = navigationVersion.current
      const triggerKey = `${targetProjectId}:${activeReviewDetail.repository_path || currentProject?.path}:${activeReviewDetail.mr_iid}`
      if (reviewTriggers.current.has(triggerKey)) return
      reviewTriggers.current.add(triggerKey)
      detailRequest.current += 1
      setPendingReviewTriggerKeys([...reviewTriggers.current])
      void api.triggerReview(targetProjectId, {
        mode,
        mr_iid: activeReviewDetail.mr_iid,
        repository_path: activeReviewDetail.repository_path || currentProject?.path,
        title: activeReviewDetail.title || `MR !${activeReviewDetail.mr_iid}`,
        branch: activeReviewDetail.branch || '',
        gitlab_url: activeReviewDetail?.gitlab_url || '',
      }).then((result) => {
        if (version !== navigationVersion.current) return
        detailRequest.current += 1
        if (result.review) {
          if (result.review.project !== targetProjectId) {
            notify('返回的评审不属于当前项目，请刷新后检查任务', 'warning')
            return
          }
          const mapped = mapReview(result.review)
          setReviews((current) => [mapped, ...current.filter((review) => review.id !== mapped.id)])
          navigate('review-detail', targetProjectId, result.review.id)
          setActiveReviewId(result.review.id)
          setActiveReviewDetail({ ...result.review, execution_state: result.job_status })
          setComments((result.review.comments || []).map(mapComment))
        } else {
          setActiveReviewDetail(current => current ? { ...current, execution_state: result.job_status } : current)
        }
        notify(mode === 'review' ? '已触发 /review，评审任务进入队列' : '已触发 /improve，正在生成定向改进建议', 'info')
      }).catch(() => { if (version === navigationVersion.current) notify('触发评审失败，请检查 GitLab、模型和队列配置', 'warning') }).finally(() => {
        reviewTriggers.current.delete(triggerKey)
        setPendingReviewTriggerKeys([...reviewTriggers.current])
      })
      return
    }
    notify('后端未连接，无法触发评审', 'warning')
  }

  const saveSummary = (summary: string) => {
    if (backendConnected && activeReviewId && !activeReviewId.startsWith('review-')) {
      const version = navigationVersion.current
      void api.updateSummary(activeReviewId, summary)
        .then((detail) => {
          if (version !== navigationVersion.current || detail.id !== activeReviewId || detail.project !== activeProjectId) return
          setActiveReviewDetail(detail)
          notify(detail.summary_sync_status === 'failed' ? '总评已保存，但同步 GitLab 失败；可再次保存重试' : '中文总评已保存并同步', detail.summary_sync_status === 'failed' ? 'warning' : 'success')
        })
        .catch(() => { if (version === navigationVersion.current) notify('中文总评保存失败，请检查后端服务', 'warning') })
      return
    }
    notify('后端未连接，无法保存中文总评', 'warning')
  }

  const notifyReview = () => {
    if (backendConnected && activeReviewId && !activeReviewId.startsWith('review-')) {
      const version = navigationVersion.current
      void api.notifyReview(activeReviewId)
        .then((result) => {
          if (version !== navigationVersion.current) return
          if (platformAdmin) void api.systemStatus().then(value => { if (version === navigationVersion.current) setSystemStats(value) }).catch(() => undefined)
          const feedback = notificationFeedback(result)
          notify(feedback.message, feedback.tone)
        })
        .catch(() => { if (version === navigationVersion.current) notify('企业微信通知发送失败，请检查 Webhook 配置', 'warning') })
      return
    }
    notify('后端未连接，无法发送企业微信通知', 'warning')
  }

  const notifyMergeResult = () => {
    if (backendConnected && activeReviewId && !activeReviewId.startsWith('review-')) {
      const version = navigationVersion.current
      void api.notifyMergeResult(activeReviewId)
        .then((result) => {
          if (version !== navigationVersion.current) return
          void api.getReview(activeReviewId).then(detail => {
            if (version === navigationVersion.current && detail.id === activeReviewId && detail.project === activeProjectId) setActiveReviewDetail(detail)
          }).catch(() => undefined)
          const state = typeof result.status === 'string' ? result.status : 'unknown'
          notify(state === 'done' ? '自动合并结果已重新发送到企业微信' : '自动合并结果通知仍未确认，请核对群消息', state === 'done' ? 'success' : 'warning')
        })
        .catch(() => { if (version === navigationVersion.current) notify('自动合并结果通知重发失败，请检查 Webhook 配置', 'warning') })
      return
    }
    notify('后端未连接，无法重发自动合并结果通知', 'warning')
  }

  const testModelConnection = (scope: 'global' | 'project' = 'global', payload: Record<string, unknown> = {}) => {
    if (backendConnected) {
      if (scope === 'project' && (!activeProjectId || loadedProjectId !== activeProjectId)) return
      const requestId = ++modelTestRequest.current
      if (payload.probe) setModelProbeMessage('正在验证模型调用…')
      const request = scope === 'project' && activeProjectId ? api.testProjectModelConfig(activeProjectId, payload) : api.testModelConfig(payload)
      void request
        .then((result) => {
          if (requestId !== modelTestRequest.current) return
          if (payload.probe) { setModelProbeMessage('模型实际调用成功，已验证评审 JSON 响应'); notify('模型实际调用成功，已验证评审 JSON 响应'); return }
          const ids = Array.isArray(result.model_ids)
            ? result.model_ids.filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
            : []
          setModelCatalog(Array.from(new Set(ids)))
          setModelTested(true)
          notify(
            ids.length
              ? '当前模型网关已连通，模型列表已同步；可继续验证模型调用'
              : '当前模型网关已连通，但未返回模型列表，可直接填写模型 ID',
            ids.length ? 'success' : 'warning',
          )
        })
        .catch((error: Error) => {
          if (requestId !== modelTestRequest.current) return
          if (payload.probe) setModelProbeMessage(error.message || '模型调用失败')
          if (!payload.probe) setModelTested(false)
          notify(error.message || '模型列表读取失败，请检查当前模型网关', 'warning')
        })
      return
    }
    if (testFixturesEnabled) {
      setModelTested(true)
      notify('模型配置已读取，模型列表已同步')
      return
    }
    notify('后端未连接，无法测试模型网关', 'warning')
  }

  const saveModelConfig = (payload: Record<string, unknown>, scope: 'global' | 'project' = 'global') => {
    if (backendConnected) {
      if (scope === 'project' && (!activeProjectId || loadedProjectId !== activeProjectId)) return false
      const context = projectContext.current
      const version = navigationVersion.current
      const request = scope === 'project' && activeProjectId ? api.updateProjectModelConfig(activeProjectId, payload) : api.updateModelConfig(payload)
      return request
        .then((result) => {
          if (version !== navigationVersion.current || (scope === 'project' && context !== projectContext.current)) return false
          if (scope === 'project') setProjectModelConfigData(result)
          else {
            setModelConfigData(result)
            setGlobalEngineConfigData(null)
            setGlobalConfigStatus(previous => ({ ...previous, engines: 'loading' }))
            void api.engineConfig().then(config => {
              if (version !== navigationVersion.current) return
              setGlobalEngineConfigData(config)
              setGlobalConfigStatus(previous => ({ ...previous, engines: 'ready' }))
            }).catch(() => {
              if (version === navigationVersion.current) setGlobalConfigStatus(previous => ({ ...previous, engines: 'error' }))
            })
          }
          notify(scope === 'project' ? '项目模型配置已保存' : '模型配置已保存')
          return true
        })
        .catch(() => {
          if (version === navigationVersion.current && (scope !== 'project' || context === projectContext.current)) notify('模型配置保存失败，请检查后端服务', 'warning')
          return false
        })
    }
    if (testFixturesEnabled) {
      notify(scope === 'project' ? '项目模型配置已保存' : '模型配置已保存')
      return true
    }
    notify('后端未连接，无法保存模型配置', 'warning')
    return false
  }

  const testGitlabConnection = (payload: Record<string, unknown> = {}) => {
    if (backendConnected) {
      void api.testGitlabConfig(payload)
        .then((result) => {
          setGitlabTested(result.ok === true)
          notify(result.ok === true ? `GitLab 连接成功${result.username ? `：${result.username}` : ''}` : 'GitLab 配置未完整，无法连接', result.ok === true ? 'info' : 'warning')
        })
        .catch(() => {
          setGitlabTested(false)
          notify('GitLab 连接测试失败，请检查地址和 API Token', 'warning')
        })
      return
    }
    notify('后端未连接，无法测试 GitLab', 'warning')
  }

  const saveGitlabConfig = (payload: Record<string, unknown>) => {
    if (backendConnected) {
      const version = navigationVersion.current
      return api.updateGitlabConfig(payload)
        .then((result) => {
          if (version !== navigationVersion.current) return false
          setGitlabConfigData(result)
          setGitlabTested(false)
          notify('GitLab 连接配置已保存')
          return true
        })
        .catch(() => {
          if (version === navigationVersion.current) notify('GitLab 连接配置保存失败，请检查后端服务', 'warning')
          return false
        })
    }
    if (testFixturesEnabled) {
      notify('GitLab 连接配置已保存')
      return true
    }
    notify('后端未连接，无法保存 GitLab 配置', 'warning')
    return false
  }

  const testNotification = (scope: 'global' | 'project' = 'global', payload: Record<string, unknown> = {}) => {
    if (backendConnected) {
      if (notificationInFlight.current || (scope === 'project' && (!activeProjectId || loadedProjectId !== activeProjectId))) return
      const context = projectContext.current
      const requestId = ++notificationTestRequest.current
      notificationInFlight.current = true
      setNotificationTesting(true)
      setNotificationTested(false)
      const isCurrent = () => context === projectContext.current && requestId === notificationTestRequest.current
      const refreshAudit = () => {
        setAuditRefreshToken(value => value + 1)
        if (scope === 'global' && platformAdmin) void refreshNotificationAudit(api.systemStatus, value => { if (isCurrent()) setSystemStats(value) })
      }
      const request = scope === 'project' && activeProjectId ? api.testProjectNotificationConfig(activeProjectId, payload) : api.testNotificationConfig(payload)
      void request
        .then((result) => {
          if (!isCurrent()) return
          setNotificationTested(result.ok === true && result.skipped !== true)
          refreshAudit()
          notify(result.skipped ? '通知已关闭，本次未发送' : '企业微信测试消息已送达', result.skipped ? 'info' : 'success')
        })
        .catch((error: Error) => {
          if (!isCurrent()) return
          setNotificationTested(false)
          refreshAudit()
          notify(error.message || '企微通知失败，请检查 Webhook', 'warning')
        })
        .finally(() => {
          if (isCurrent()) { notificationInFlight.current = false; setNotificationTesting(false) }
        })
      return
    }
    notify('后端未连接，无法测试企业微信通知', 'warning')
  }

  const saveNotificationConfig = (payload: Record<string, unknown>, scope: 'global' | 'project' = 'global') => {
    if (backendConnected) {
      if (scope === 'project' && (!activeProjectId || loadedProjectId !== activeProjectId)) return false
      const context = projectContext.current
      const version = navigationVersion.current
      const request = scope === 'project' && activeProjectId ? api.updateProjectNotificationConfig(activeProjectId, payload) : api.updateNotificationConfig(payload)
      return request
        .then((result) => {
          if (version !== navigationVersion.current || (scope === 'project' && context !== projectContext.current)) return false
          if (scope === 'project') setProjectNotificationConfigData(result)
          else setNotificationConfigData(result)
          notify(scope === 'project' ? '项目企微配置已保存' : '企微通知配置已保存')
          return true
        })
        .catch(() => {
          if (version === navigationVersion.current && (scope !== 'project' || context === projectContext.current)) notify('企微通知配置保存失败，请检查后端服务', 'warning')
          return false
        })
    }
    if (testFixturesEnabled) {
      notify(scope === 'project' ? '项目企微配置已保存' : '企微通知配置已保存')
      return true
    }
    notify('后端未连接，无法保存企业微信配置', 'warning')
    return false
  }

  const runGateDryRun = () => {
    const selectedId = selectedGateReviewId
    if (!selectedId) {
      notify('请先选择目标评审，再执行检查', 'warning')
      return
    }
    if (backendConnected) {
      if (!activeProjectId || loadedProjectId !== activeProjectId) return
      const context = projectContext.current
      const requestId = ++gateRequest.current
      setGateAction('dry-run')
      setGateError('')
      setGateAssessment(null)
      setGateDryRun(false)
      setGateDryRunPassed(false)
      void api.mergeGateDryRun(activeProjectId, selectedId)
        .then((result) => {
          if (context !== projectContext.current || requestId !== gateRequest.current || selectedId !== selectedGateReviewId) return
          const assessment = normalizeGateAssessment(result)
          setGateAssessment(assessment)
          setGateDryRun(true)
          setGateDryRunPassed(assessment?.passed === true)
          notify(assessment?.passed === true ? 'MR 检查通过，项目开关保持不变' : `MR 检查完成：${assessment?.reason || '仍有阻断问题'}；项目开关保持不变`, assessment?.passed === true ? 'success' : 'info')
        })
        .catch((error: Error) => {
          if (requestId !== gateRequest.current) return
          setGateError(error.message || 'Dry-run 读取失败，请检查后端服务')
          notify(error.message || 'Dry-run 读取失败，请检查后端服务', 'warning')
        })
        .finally(() => { if (requestId === gateRequest.current) setGateAction(null) })
      return
    }
    if (testFixturesEnabled) {
      setGateAssessment({
        project_id: activeProjectId || 'demo-api', review_id: selectedId, mr_iid: Number((scopedReviews.find(review => review.id === selectedId)?.mr || '!0').replace('!', '')),
        reviewed_sha: '', current_sha: '', checks: { review_ready: false, summary_ready: false, blockers_resolved: false },
        passed: false, reason: '测试数据中的评审包含待处理阻断问题',
      })
      setGateDryRun(true)
      setGateDryRunPassed(false)
      notify('MR 检查完成，项目开关保持不变', 'info')
      return
    }
    notify('后端未连接，无法执行 MR 检查', 'warning')
  }

  const retrySystemJobs = () => {
    if (backendConnected) {
      void api.retryFailedJobs()
        .then((result) => {
          void api.systemStatus().then(setSystemStats).catch(() => undefined)
          notify(result.count ? `已重新排队 ${result.count} 个失败任务` : '当前没有失败任务', 'info')
        })
        .catch(() => notify('失败任务重试失败，请检查后端队列', 'warning'))
      return
    }
    notify('后端未连接，无法重试失败任务', 'warning')
  }

  const enableMergeGate = (enabled = true) => {
    if (backendConnected && activeProjectId) {
      if (loadedProjectId !== activeProjectId) return
      const context = projectContext.current
      const requestId = ++gateRequest.current
      setGateAction('toggle')
      setGateError('')
      void api.updateMergeGate(activeProjectId, { auto_merge_enabled: enabled })
        .then((result) => {
          if (context !== projectContext.current || requestId !== gateRequest.current) return
          setGateEnabled(result.enabled === true)
          notify(result.enabled === true ? '项目自动合并已开启，将持续处理后续 MR' : '项目自动合并已关闭')
        })
        .catch((error: Error) => {
          if (requestId !== gateRequest.current) return
          setGateError(error.message || '门禁保存失败，请检查后端服务')
          notify(error.message || '门禁保存失败，请检查后端服务', 'warning')
        })
        .finally(() => { if (requestId === gateRequest.current) setGateAction(null) })
      return
    }
    if (testFixturesEnabled) {
      setGateEnabled(enabled)
      notify(enabled ? '项目自动合并已开启，将持续处理后续 MR' : '项目自动合并已关闭')
      return
    }
    notify('后端未连接，无法启用自动合并门禁', 'warning')
  }

  const content = (() => {
    if (!platformAdmin && !activeProjectId && ['models', 'prompts', 'notifications', 'system', 'users', 'runtime'].includes(page)) {
      return <div className="empty-state"><strong>无权访问平台配置</strong><span>请联系平台管理员，项目配置请进入对应项目空间。</span><Button onClick={() => navigate('projects', null)}>返回项目列表</Button></div>
    }
    if (invalidRoute || (activeProjectId && (backendConnected || testFixturesEnabled) && !projects.some(p => p.id === activeProjectId))) {
      return <div className="empty-state"><strong>页面不存在或项目不可访问</strong><Button onClick={() => navigate('projects', null)}>返回项目列表</Button></div>
    }
    if (!testFixturesEnabled && !activeProjectId && (page === 'models' || page === 'notifications' || page === 'system')) {
      const status = globalConfigStatus[page]
      if (!backendConnected || status !== 'ready') {
        return <div className="empty-state" role={status === 'error' ? 'alert' : 'status'}><strong>{!backendConnected ? '后端未连接，无法编辑平台配置' : status === 'error' ? '平台配置加载失败，请重新加载' : '正在加载平台配置…'}</strong><span>读取成功前不会展示空配置，也不能保存或测试连接。</span><Button variant="secondary" onClick={() => { navigationVersion.current += 1; setBackendRefreshToken(value => value + 1) }}>重新加载配置</Button></div>
      }
    }
    const projectSettings = page === 'policy' || page === 'merge-gate' || (page === 'models' && modelScope === 'project') || (page === 'notifications' && notificationScope === 'project')
    if (!testFixturesEnabled && projectSettings && (!backendConnected || !activeProjectId || loadedProjectId !== activeProjectId)) {
      return <div className="empty-state" role="status"><strong>{!activeProjectId ? '请先选择项目' : !backendConnected ? '后端未连接，无法编辑项目配置' : projectLoadError ? '项目配置加载失败，请刷新重试' : '正在加载当前项目配置…'}</strong><span>加载成功前不会显示或保存其他项目的配置。</span><Button variant="secondary" onClick={() => setBackendRefreshToken(value => value + 1)}>重新加载</Button></div>
    }
    switch (page) {
      case 'prompts':
        return <GlobalPrompts key={backendRefreshToken} onDirtyChange={onDirtyChange} onNotify={notify} />
      case 'users':
        return <UserManagement key={user.id} currentUserId={user.id} onAccessChange={() => window.location.reload()} />
      case 'members':
        return activeProjectId && currentProject ? <ProjectMembers projectId={activeProjectId} projectName={currentProject.name} canManage={platformAdmin || currentProject.currentUserRole === 'admin'} canCreateAccounts={platformAdmin} currentUserId={user.id} onAccessChange={() => window.location.reload()} /> : <div className="empty-state">正在加载当前项目权限…</div>
      case 'mr-list':
        return activeProjectId && (backendConnected || testFixturesEnabled) ? <MergeRequests key={activeProjectId} projectId={activeProjectId} repositories={currentProject?.repositories} onReviewed={() => setBackendRefreshToken(n => n + 1)} onOpenReview={openReview} /> : <div className="empty-state">连接后端后查看当前项目 MR</div>
      case 'overview':
        return <OverviewPage stats={overviewStats} onNavigate={navigate} onOpenReview={openReview} reviews={reviews} projects={projects} unresolvedBlockers={unresolvedBlockers} canManagePlatform={platformAdmin} coverageEnabled={backendConnected || testFixturesEnabled} />
      case 'projects':
        return (
          <ProjectsPage
            canAdd={platformAdmin}
            projects={projects}
            query={projectQuery}
            filter={projectFilter}
            onQueryChange={setProjectQuery}
            onFilterChange={setProjectFilter}
            onAdd={() => setProjectDrawer('create')}
            onEdit={(project) => setProjectDrawer(project)}
            onToggle={toggleProject}
            onOpenPolicy={() => navigate('policy')}
            onProjectAction={(id, target) => navigate(target, id)}
            onSelectProject={selectProject}
          />
        )
      case 'history':
        return (
          <ReviewHistory
            reviews={scopedReviews}
            projects={projects}
            activeProjectId={activeProjectId}
            filters={historyFilters}
            onFiltersChange={setHistoryFilters}
            onRefresh={() => setBackendRefreshToken(value => value + 1)}
            onRetry={retryReview}
            onOpenDetail={openReview}
          />
        )
      case 'review-detail':
        return (
          <><Button variant="ghost" onClick={() => navigate('history', historyReturn.current ? historyReturn.current.projectId : activeProjectId)}>返回评审历史</Button><ReviewDetailPage
            comments={comments}
            review={activeReviewDetail}
            triggering={pendingReviewTriggerKeys.includes(`${activeProjectId}:${activeReviewDetail?.repository_path || currentProject?.path}:${activeReviewDetail?.mr_iid}`)}
            onPublish={publishComment}
            onResolve={resolveComment}
            onNotify={notifyReview}
            onNotifyMerge={notifyMergeResult}
            onSaveSummary={saveSummary}
            onRunReview={() => runReview('review')}
            onRunImprove={() => runReview('improve')}
          /></>
        )
      case 'policy':
        return <PolicyPage key={activeProjectId || 'policy'} saveError={policySaveError} saving={Boolean(activeProjectId && pendingPolicyProjects.has(activeProjectId))} projectName={projects.find((project) => project.id === activeProjectId)?.name || projects[0]?.name || '当前项目'} policy={policy} onChange={updatePolicy} onSave={savePolicy} onNotify={notify} engineConfig={projectEngineConfigData} engineLoadStatus={projectEngineLoadStatus} onReloadEngine={() => setBackendRefreshToken(value => value + 1)} engineSaving={pendingEngineScope === 'project'} readOnly={projectConfigReadOnly} onSaveEngine={(payload) => saveEngineConfig(payload, 'project')} />
      case 'models':
        return <ModelsPageConnected onDirtyChange={onDirtyChange} onInvalidate={() => { setModelProbeMessage(''); setModelTested(false) }} probeMessage={modelProbeMessage} key={`${modelScope}-${activeProjectId}-${backendRefreshToken}`} initial={modelScope === 'project' ? projectModelConfigData : modelConfigData} modelOptions={modelCatalog} scope={modelScope} projectName={projects.find((project) => project.id === activeProjectId)?.name || projects[0]?.name || '当前项目'} onScopeChange={() => undefined} tested={modelTested} onTest={(payload) => testModelConnection(modelScope, payload)} onSave={(payload) => saveModelConfig(payload, modelScope)} engineConfig={globalEngineConfigData} engineLoadStatus={globalConfigStatus.engines} onReloadEngines={() => setBackendRefreshToken(value => value + 1)} engineSaving={pendingEngineScope === 'global'} onSaveEngine={(payload) => saveEngineConfig(payload, 'global')} onTestEngine={testEngineConfig} />
      case 'notifications':
        return (
          <NotificationsPageConnected onDirtyChange={onDirtyChange} key={`${notificationScope}-${activeProjectId}-${backendRefreshToken}`}
            projectId={activeProjectId} projects={projects} auditRefreshKey={auditRefreshToken}
            initial={notificationScope === 'project' ? projectNotificationConfigData : notificationConfigData}
            scope={notificationScope}
            projectName={projects.find((project) => project.id === activeProjectId)?.name || projects[0]?.name || '当前项目'}
            onScopeChange={() => undefined}
            tested={notificationTested}
            testing={notificationTesting}
            readOnly={projectConfigReadOnly}
            onInvalidate={() => setNotificationTested(false)}
            onTest={(payload) => testNotification(notificationScope, payload)}
            onSave={(payload) => saveNotificationConfig(payload, notificationScope)}
            onReloadAudit={() => setAuditRefreshToken(value => value + 1)}
            auditStatus={notificationScope === 'project' ? projectAudit?.projectId === activeProjectId ? projectAudit.status : 'loading' : 'ready'}
            auditLogs={notificationScope === 'project' ? projectAudit?.projectId === activeProjectId ? projectAudit.rows : [] : Array.isArray(systemStats?.audit_logs) ? systemStats.audit_logs as Array<Record<string, unknown>> : []}
          />
        )
      case 'system':
        return <GitLabConnectionCard key={backendRefreshToken} onDirtyChange={onDirtyChange} config={gitlabConfigData} tested={gitlabTested} onTest={testGitlabConnection} onSave={saveGitlabConfig} />
      case 'runtime':
        return <SystemPage stats={systemStats} onRetry={retrySystemJobs} />
      case 'merge-gate':
        return (
          <MergeGatePage
            dryRun={gateDryRun}
            dryRunPassed={gateDryRunPassed}
            enabled={gateEnabled}
            selectedReviewId={selectedGateReviewId}
            selectedReview={scopedReviews.find(review => review.id === selectedGateReviewId) || null}
            reviews={scopedReviews}
            assessment={gateAssessment}
            action={gateAction}
            error={gateError}
            projectName={projects.find((project) => project.id === activeProjectId)?.name || '当前项目'}
            automationEnabled={policy.webhookTrigger}
            projectEnabled={currentProject?.enabled !== false}
            onOpenPolicy={() => navigate('policy')}
            onSelectReview={(reviewId) => { gateRequest.current += 1; setSelectedGateReviewId(reviewId || null); setGateAssessment(null); setGateDryRun(false); setGateDryRunPassed(false); setGateError('') }}
            onDryRun={runGateDryRun}
            onEnable={() => enableMergeGate()}
            onDisable={() => enableMergeGate(false)}
            onOpenDetail={() => selectedGateReviewId ? openReview(selectedGateReviewId) : navigate('history')}
          />
        )
    }
  })()

  return (
    <div className="app-shell">
      <aside className={cn('sidebar', mobileNav && 'sidebar-open')}>
          <div className="brand-block">
          <div className="brand-mark"><Sparkles size={18} strokeWidth={2.4} /></div>
          <div>
            <strong>智审台</strong>
            <span>团队智能代码审查平台</span>
          </div>
          <button className="sidebar-close" aria-label="关闭导航" onClick={() => setMobileNav(false)}><X size={18} /></button>
        </div>
        <ActionMenu label="切换项目" className="workspace-switcher" title={activeProjectId ? projects.find(p => p.id === activeProjectId)?.name || '加载项目…' : '平台工作台'}>
          <strong>{activeProjectId ? '切换项目空间' : '进入项目空间'}</strong>
          {projects.map(project => <button key={project.id} aria-pressed={project.id === activeProjectId} onClick={() => selectProject(project.id)}>{project.name}{project.id === activeProjectId ? ' ✓' : ''}</button>)}
          <button onClick={() => navigate('projects')}>管理项目</button>
        </ActionMenu>
        <nav className="main-nav" aria-label="主导航">
          {activeProjectId && <button className="nav-item" onClick={() => navigate('overview', null)}><LayoutDashboard size={17} />返回平台</button>}
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-group-label">{group.label}</div>
              {group.items.map((item) => {
                const Icon = item.icon
                return (
                  <button
                    key={item.id}
                    className={cn('nav-item', (page === item.id || (page === 'review-detail' && item.id === (historyReturn.current ? 'history' : 'mr-list')) || (item.id === 'system' && ['users', 'runtime'].includes(page))) && 'nav-item-active')}
                    aria-current={page === item.id || (page === 'review-detail' && item.id === (historyReturn.current ? 'history' : 'mr-list')) || (item.id === 'system' && ['users', 'runtime'].includes(page)) ? 'page' : undefined}
                    aria-label={item.label}
                    onClick={() => navigate(item.id)}
                  >
                    <Icon size={17} strokeWidth={page === item.id ? 2.2 : 1.8} />
                    <span>{item.label}</span>
                    {item.id === 'review-detail' && <span className="nav-dot" />}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="system-status"><span className="pulse-dot" style={!backendConnected ? { background: '#a0aaba', boxShadow: 'none' } : undefined} /> {testFixturesEnabled ? '模拟数据 · 交互预览' : backendConnected ? 'Django 后端已连接' : '后端未连接'}</div>
          <AccountMenu label="底部用户菜单" user={user} onLogout={guardedLogout} className="sidebar-user" />
        </div>
      </aside>

      <main className="app-main">
        <header className="topbar">
          <div className="topbar-left"><button className="mobile-menu" aria-label="打开导航" onClick={() => setMobileNav(true)}><Menu size={20} /></button><div className="breadcrumb"><span>{activeProjectId ? `项目空间 · ${projects.find(p => p.id === activeProjectId)?.name || activeProjectId}` : '平台工作台'}</span><ChevronRight size={14} /><strong>{currentMeta.title}</strong></div></div>
          <div className="topbar-actions">
            <label className="top-search"><Search size={16} /><input aria-label="全局搜索" placeholder="搜索项目、MR 或评审记录" value={globalQuery} onChange={e => setGlobalQuery(e.target.value)} /></label>
            <HelpMenu platformAdmin={platformAdmin} onConnections={() => navigate('system')} />
            <AccountMenu label="用户菜单" user={user} onLogout={guardedLogout} className="top-user" />
          </div>
        </header>

        <div className="content-area">{navigationGuard.dialog}
          {globalQuery.trim() && <Card title="搜索结果" subtitle="搜索已加载的项目和评审记录">
            {projects.filter(p => (p.name + p.path).toLowerCase().includes(globalQuery.toLowerCase())).map(p => <Button key={p.id} onClick={() => { navigate('policy', p.id); setGlobalQuery('') }}>{p.name}</Button>)}
            {reviews.filter(r => (r.mr + r.project + r.branch).toLowerCase().includes(globalQuery.toLowerCase())).map(r => <Button key={r.id} onClick={() => { openReview(r.id); setGlobalQuery('') }}>{r.mr} · {r.project}</Button>)}
            {!projects.some(p => (p.name + p.path).toLowerCase().includes(globalQuery.toLowerCase())) && !reviews.some(r => (r.mr + r.project + r.branch).toLowerCase().includes(globalQuery.toLowerCase())) && <p>没有匹配的项目或评审记录</p>}
            <Button variant="ghost" onClick={() => setGlobalQuery('')}>关闭搜索</Button>
          </Card>}
          <div className="page-heading">
            <div><div className="eyebrow">{currentMeta.eyebrow}</div><h1>{currentMeta.title}</h1><p>{currentMeta.description}</p></div>
            <div className="heading-actions"><span className="last-sync"><span className="pulse-dot" style={!backendConnected ? { background: '#a0aaba', boxShadow: 'none' } : undefined} /> {testFixturesEnabled ? '模拟数据' : backendConnected ? '后端数据已同步' : '等待后端连接'}</span>{<Button variant="secondary" icon={<RefreshCw size={15} />} onClick={() => navigationGuard.request(() => { navigationVersion.current += 1; setPolicy(savedPolicy.current); setBackendRefreshToken((value) => value + 1); notify(testFixturesEnabled ? '预览数据已刷新' : '正在刷新后端数据', 'info') })}>刷新数据</Button>}</div>
          </div>
          {platformAdmin && !activeProjectId && ['system', 'users', 'runtime'].includes(page) && <nav className="system-tabs" aria-label="系统设置分类">{([{ id: 'system', label: 'GitLab 连接' }, { id: 'users', label: '用户管理' }, { id: 'runtime', label: '运行与审计' }] as const).map(tab => <button key={tab.id} aria-current={page === tab.id ? 'page' : undefined} onClick={() => navigate(tab.id, null)}>{tab.label}</button>)}</nav>}
          {projectConfigReadOnly ? <>
            <div className="info-banner"><span>当前为项目成员，只能查看项目配置</span><Button variant="secondary" onClick={() => navigate('history')}>查看评审历史</Button></div>
            {page === 'notifications' || page === 'policy' ? content : <fieldset className="readonly-project-config" disabled aria-label="只读项目配置">{content}</fieldset>}
          </> : content}
        </div>
      </main>

      {platformAdmin && projectDrawer && <ProjectDrawer initial={projectDrawer === 'create' ? null : projectDrawer} saving={projectSavePending} onClose={() => setProjectDrawer(null)} onSave={saveProject} onContinue={(id, target) => { setProjectDrawer(null); navigate(target, id) }} />}
      {toast && <div className={cn('toast', `toast-${toast.tone}`)} role="status"><span className="toast-icon">{toast.tone === 'success' ? <CheckCircle2 size={17} /> : toast.tone === 'warning' ? <AlertTriangle size={17} /> : <Zap size={17} />}</span><span>{toast.message}</span><button aria-label="关闭提示" onClick={() => setToast(null)}><X size={15} /></button></div>}
    </div>
  )
}

function Button({ children, onClick, variant = 'primary', icon, disabled, type = 'button', className }: { children: ReactNode; onClick?: MouseEventHandler<HTMLButtonElement>; variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'soft'; icon?: ReactNode; disabled?: boolean; type?: 'button' | 'submit'; className?: string }) {
  return <button className={cn('button', `button-${variant}`, className)} onClick={onClick} disabled={disabled} type={type}>{icon}{children}</button>
}

function Badge({ children, tone = 'neutral', dot = false }: { children: ReactNode; tone?: StatusTone; dot?: boolean }) {
  return <span className={cn('badge', `badge-${tone}`)}>{dot && <span className="badge-dot" />}{children}</span>
}

function Card({ title, subtitle, action, children, className }: { title?: string; subtitle?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={cn('card', className)}>{(title || action) && <div className="card-header"><div>{title && <h2>{title}</h2>}{subtitle && <p>{subtitle}</p>}</div>{action}</div>}{children}</section>
}

function MetricCard({ label, value, detail, icon, tone = 'blue', trend }: { label: string; value: string; detail: string; icon: ReactNode; tone?: 'blue' | 'green' | 'orange' | 'purple'; trend?: string }) {
  return <div className="metric-card"><div className={cn('metric-icon', `metric-${tone}`)}>{icon}</div><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="metric-detail">{trend && <span className="trend-up">{trend}</span>}{detail}</div></div>
}

function EmptyLine({ children }: { children: ReactNode }) { return <div className="empty-line"><CircleDot size={15} />{children}</div> }

export function OverviewPage({ stats, onNavigate, onOpenReview, reviews, projects, unresolvedBlockers, coverageEnabled = false }: { stats: Record<string, unknown> | null; onNavigate: (page: PageId) => void; onOpenReview: (id: string) => void; reviews: Review[]; projects: Project[]; unresolvedBlockers: number; canManagePlatform?: boolean; coverageEnabled?: boolean }) {
  type SevereIssue = { id: string; review_id: string; level: string; title: string; project: string; mr_iid: number; created_at: string }
  const recentIssues = Array.isArray(stats?.recent_severe_issues) ? stats.recent_severe_issues as SevereIssue[] : []
  const severeCount = typeof stats?.recent_severe_count === 'number' ? stats.recent_severe_count : null
  const dailySevere = Array.isArray(stats?.daily_severe_issues) ? (stats.daily_severe_issues as number[]).slice(-7) : []
  const labels = Array.isArray(stats?.daily_labels) ? (stats.daily_labels as string[]).slice(-7) : []
  const maxDaily = Math.max(1, ...dailySevere)
  const totalReviews = typeof stats?.reviews === 'number' ? stats.reviews : reviews.length
  const activeProjects = typeof stats?.projects === 'number' ? stats.projects : projects.length
  return <div className="quality-overview">
    <div className="quality-main">
      <section className="quality-hero card" aria-label="近七天严重问题">
        <div><span className="quality-kicker">近 7 天 · 新发现严重问题</span><div className="quality-count">{severeCount === null ? '—' : severeCount}<span>个</span></div><p>{severeCount === null ? '正在读取评审结果' : severeCount > 0 ? '先查看风险，再定位到具体代码。' : '近 7 天没有新发现的严重问题，请继续关注评审覆盖。'}</p></div>
        <Button variant="secondary" icon={<ArrowUpRight size={15} />} onClick={() => onNavigate('history')}>查看评审历史</Button>
      </section>
      <Card title="需要关注的严重问题" subtitle="近 7 天新发现 · 点击进入评审依据" className="quality-issues">
        {recentIssues.length ? <div className="quality-issue-list">{recentIssues.map(issue => <button className="quality-issue" key={issue.id} onClick={() => onOpenReview(issue.review_id)}><span className={cn('severity', 'severity-' + issue.level.toLowerCase())}>{issue.level}</span><span className="quality-issue-copy"><strong>{issue.title}</strong><small>{issue.project} · MR !{issue.mr_iid} · {new Date(issue.created_at).toLocaleString('zh-CN')}</small></span><ChevronRight size={17} /></button>)}</div> : <EmptyLine>{severeCount === null ? '正在加载严重问题' : '近 7 天暂无新发现的严重问题'}</EmptyLine>}
        {severeCount !== null && severeCount > recentIssues.length && <div className="quality-list-foot">展示最近 {recentIssues.length} 条，共 {severeCount} 条</div>}
      </Card>
      <Card title="最近评审" subtitle="来自已接入项目的最新运行记录" action={<Button variant="ghost" icon={<ArrowUpRight size={14} />} onClick={() => onNavigate('history')}>全部记录</Button>} className="recent-card"><ReviewTable reviews={reviews.slice(0, 4)} onOpen={onOpenReview} /></Card>
    </div>
    <aside className="quality-side" aria-label="趋势与覆盖">
      <Card title="每日新发现严重问题" subtitle="近 7 天 · 按问题创建时间统计">
        {dailySevere.length ? <div className="quality-trend" role="img" aria-label={`近 7 天每日新发现严重问题：${dailySevere.join('、')}`}>
          {dailySevere.map((value, index) => <div className="quality-trend-day" key={index} title={`${labels[index] || `第${index + 1}天`}：${value} 个`}><span>{value}</span><i style={{ height: `${Math.max(3, value / maxDaily * 100)}%` }} /><small>{labels[index]?.slice(3) || ''}</small></div>)}
        </div> : <EmptyLine>暂无趋势数据</EmptyLine>}
      </Card>
      <OverviewCoverage enabled={coverageEnabled} onOpenProjects={() => onNavigate('projects')} />
      <Card title="评审任务概况" subtitle="来自后端评审记录" className="quality-task-summary"><div><strong>{totalReviews}</strong><span>累计评审</span></div><div><strong>{activeProjects}</strong><span>启用项目</span></div><div><strong>{unresolvedBlockers}</strong><span>当前视图中的未解决阻断</span></div></Card>
    </aside>
  </div>
}

function ReviewTable({ reviews, onOpen }: { reviews: Review[]; onOpen: (id: string) => void }) {
  return <ResizableTable tableId="recent-reviews" label="最近评审" columns={tableColumns.recentReviews} stickyLastColumn><tbody>{reviews.map((review) => <tr key={review.id} onClick={() => onOpen(review.id)}><td><div className="mr-cell"><strong>{review.mr} · {review.project}</strong><span><GitBranch size={13} />{review.branch}</span></div></td><td><Badge tone={review.source === 'GitLab Webhook' ? 'info' : 'neutral'}>{review.source}</Badge></td><td><ReviewStatus status={review.status} blockers={review.blockers} /></td><td>{review.comments} 条</td><td>{review.duration}</td><td className="muted-text">{review.createdAt}</td><td><ChevronRight size={16} className="muted-icon" /></td></tr>)}</tbody></ResizableTable>
}

function ReviewStatus({ status, blockers }: { status: Review['status']; blockers: number }) {
  const tone: StatusTone = status === '已完成' ? 'success' : status === '有阻断' ? 'danger' : status === '运行中' || status === '排队中' ? 'info' : 'warning'
  return <Badge tone={tone} dot>{status}{blockers > 0 && ` · ${blockers} 个阻断`}</Badge>
}

function ProjectsPage({ projects, query, filter, onQueryChange, onFilterChange, onAdd, onEdit, onToggle, onOpenPolicy, onSelectProject, onProjectAction, canAdd }: { projects: Project[]; query: string; filter: '全部' | Project['status']; onQueryChange: (value: string) => void; onFilterChange: (value: '全部' | Project['status']) => void; onAdd: () => void; onEdit: (project: Project) => void; onToggle: (project: Project) => void; onOpenPolicy: () => void; onSelectProject: (id: string) => void; onProjectAction: (id: string, page: PageId) => void; canAdd: boolean }) {
  const filtered = projects.filter((project) => (filter === '全部' || project.status === filter) && `${project.name} ${project.path} ${(project.repositories || []).map(repo => repo.path).join(' ')}`.toLowerCase().includes(query.toLowerCase()))
  const webhookEndpoint = apiUrl('webhooks/gitlab/')
  return <>
    <div className="toolbar-row"><label className="input-search"><Search size={16} /><input aria-label="搜索项目" value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索项目名称或路径" /></label><div className="toolbar-spacer" /><div className="segmented-control">{(['全部', '运行中', '待配置', '已暂停'] as const).map((item) => <button key={item} className={filter === item ? 'active' : ''} onClick={() => onFilterChange(item)}>{item}</button>)}</div>{canAdd && <Button icon={<Plus size={16} />} onClick={onAdd}>添加项目</Button>}</div>
    <ProjectQuickAccess projects={projects} onOpen={onSelectProject} />
    <Card title="已接入项目" subtitle={`${projects.length} 个项目 · 状态由连接配置与暂停开关决定`} className="project-card"><ResizableTable tableId="projects" label="已接入项目" className="project-table" columns={tableColumns.projects} stickyLastColumn><tbody>{filtered.map((project) => <tr key={project.id} onClick={() => onSelectProject(project.id)}><td><div className="project-cell"><div className="project-symbol"><Code2 size={17} /></div><div><strong>{project.name}</strong><span>{(project.repositories || [{ path: project.path }]).map(repo => repo.path).join(' · ')}</span></div></div></td><td>{project.owner}</td><td><div className="project-status" title={project.statusReasons?.join('；') || (project.automationEnabled === false ? '手动评审可用；自动 Webhook 已关闭' : 'GitLab 和模型已配置')}><Badge tone={project.status === '运行中' ? 'success' : project.status === '待配置' ? 'warning' : 'neutral'} dot>{project.status}</Badge>{project.automationEnabled === false && project.status === '运行中' && <small>仅手动</small>}{project.statusReasons?.length ? <small>{project.statusReasons[0]}</small> : null}</div></td><td><span className="with-icon"><Bot size={14} />{project.model}</span></td><td><span className="with-icon"><Bell size={14} />{project.notify}</span></td><td className="muted-text">{project.updatedAt}</td><td><ActionMenu label={`${project.name} 更多操作`} title={<MoreHorizontal size={17} />}>{canAdd && <><button onClick={() => onEdit(project)}>编辑项目与仓库</button><button onClick={() => onToggle(project)}>{project.enabled === false ? '恢复项目' : '暂停项目'}</button></>}<button onClick={() => onProjectAction(project.id, 'policy')}>配置项目策略</button><button onClick={() => onProjectAction(project.id, 'models')}>模型配置</button><button onClick={() => onProjectAction(project.id, 'notifications')}>通知配置</button><button onClick={() => onProjectAction(project.id, 'history')}>评审历史</button></ActionMenu></td></tr>)}</tbody></ResizableTable>{filtered.length === 0 && <div className="empty-state"><Search size={22} /><strong>没有找到匹配项目</strong><span>调整搜索词或筛选条件后再试。</span></div>}</Card>
    <div className="status-guide"><strong>状态如何流转</strong><span>待配置：连接、模型或项目覆盖缺失；补齐后自动变为运行中。</span><span>运行中：可执行评审；Webhook 自动触发可在项目策略中独立开关。</span><span>已暂停：在项目更多操作中暂停或恢复，暂停时不执行评审。</span></div>
    <div className="info-banner"><div className="info-banner-icon"><Webhook size={18} /></div><div><strong>GitLab 自动触发</strong><span>Webhook 地址：<code>{webhookEndpoint}</code> · 令牌在系统设置中配置（项目可覆盖）；GitLab 需要可达的部署地址。</span></div><Button variant="soft" onClick={() => navigator.clipboard?.writeText(webhookEndpoint)}>复制 Webhook 地址 <Copy size={14} /></Button><Button variant="soft" onClick={onOpenPolicy}>查看项目策略 <ArrowUpRight size={14} /></Button></div>
  </>
}
function ReviewDetailPage({ review, comments, onPublish, onResolve, onNotify, onNotifyMerge, onSaveSummary, onRunReview, onRunImprove, triggering = false }: { review: BackendReview | null; comments: LineComment[]; onPublish: (id: string) => void; onResolve: (id: string) => void; onNotify: () => void; onNotifyMerge: () => void; onSaveSummary: (summary: string) => void; onRunReview: () => void; onRunImprove: () => void; triggering?: boolean }) {
  const [activeTab, setActiveTab] = useState<'diff' | 'comments' | 'summary'>('diff')
  if (!review) {
    return <Card title="MR 行级审查详情" subtitle="请从评审历史或总览选择一条真实评审记录"><div className="empty-state"><GitPullRequest size={22} /><strong>暂无评审详情</strong><span>后端返回评审记录后，才能查看真实 diff 和行级评论。</span></div></Card>
  }
  const mrLabel = `!${review.mr_iid}`
  const title = review.title && review.title !== mrLabel && review.title !== 'MR ' + mrLabel ? review.title : ''
  const projectName = review.project_name || '当前项目'
  const branch = review.branch || '未记录分支'
  const status = review.status_label || ({ completed: '已完成', running: '运行中', blocked: '有阻断', failed: '失败', queued: '排队中' } as Record<string, string>)[review.status] || '待处理'
  const statusTone: StatusTone = status === '已完成' ? 'success' : status === '运行中' || status === '排队中' ? 'info' : status === '有阻断' ? 'danger' : 'warning'
  const blockerLabel = `${review.blocking_comment_count || 0} 个阻断`
  const mergeStatusLabels: Record<string, string> = {
    merged: '已自动合并', pending: '等待 GitLab 合并', blocked: '门禁阻断',
    unknown: '合并结果待核对', skipped: '未执行自动合并', busy: '自动合并执行中',
  }
  const mergeStatus = review.merge_status && review.merge_status !== 'not_requested' ? review.merge_status : ''
  const mergeStatusTone = mergeStatus === 'merged' ? 'success' : mergeStatus === 'pending' || mergeStatus === 'busy' ? 'warning' : 'danger'
  return <>
    {review.error_message && <div role="alert" className="form-error">评审失败：{review.error_message}</div>}
    {review.summary_sync_status === 'failed' && <div role="alert" className="form-error">GitLab 总评同步失败；请在中文总评中点击“保存并同步”重试。</div>}
    {['failed', 'unknown', 'sending'].includes(review.notification_state || '') && <div role="alert" className="form-error">{review.notification_state === 'failed' ? '企业微信投递失败，可手动重发。' : review.notification_state === 'sending' ? '企业微信消息正在投递，请勿重复发送。' : '企业微信投递结果不确定。请先核对群消息，再决定是否手动重发。'}</div>}
    {mergeStatus && <div role={mergeStatus === 'merged' ? 'status' : 'alert'} className={`merge-result-banner ${mergeStatusTone}`}><GitMerge size={15} /><strong>自动合并：{mergeStatusLabels[mergeStatus] || mergeStatus}</strong>{review.merge_attempts ? <span>尝试 {review.merge_attempts} 次</span> : null}{review.merge_notification_state && review.merge_notification_state !== 'not_requested' ? <span>企微通知：{review.merge_notification_state}</span> : null}{review.merge_error && <span>{review.merge_error}</span>}{['failed', 'unknown'].includes(review.merge_notification_state || '') && <Button variant="ghost" onClick={onNotifyMerge}>重发结果通知</Button>}</div>}
    <div className="mr-header card"><div className="mr-heading"><div className="mr-project-icon"><Code2 size={22} /></div><div><div className="mr-title-row"><h2>{mrLabel}{title ? ' ' + title : ''}</h2><Badge tone={statusTone} dot>{status === '有阻断' ? blockerLabel : status}</Badge></div><p>{projectName} <span>·</span> {branch}</p><div className="mr-meta"><span><GitBranch size={14} />{review.source || '触发来源未记录'}</span><span><Clock3 size={14} />{review.created_at ? new Date(review.created_at).toLocaleString('zh-CN') : '开始时间未记录'}</span><span><Sparkles size={14} />评审引擎与模型由项目配置决定</span></div></div></div><div className="mr-actions"><Button variant="secondary" icon={<RefreshCw size={15} />} disabled={triggering || ['queued', 'running'].some(state => [review.status, review.generation_state, review.execution_state].includes(state))} onClick={onRunReview}>{triggering ? '提交中…' : '/review 重新审查'}</Button><Button variant="soft" icon={<Sparkles size={15} />} disabled={triggering || ['queued', 'running'].some(state => [review.status, review.generation_state, review.execution_state].includes(state))} onClick={onRunImprove}>/improve 生成建议</Button><Button variant="ghost" disabled={!review.gitlab_url} onClick={() => { if (review.gitlab_url?.startsWith('https://') || review.gitlab_url?.startsWith('http://')) window.open(review.gitlab_url, '_blank', 'noopener,noreferrer') }}>打开 GitLab MR</Button></div></div>
    <ReviewRecheck result={review.recheck_result} />
    <div className="detail-tabs"><button className={activeTab === 'diff' ? 'active' : ''} onClick={() => setActiveTab('diff')}><Code2 size={15} />代码差异 <span>{review.changed_files?.length || 0}</span></button><button className={activeTab === 'comments' ? 'active' : ''} onClick={() => setActiveTab('comments')}><MessageSquare size={15} />行级评论 <span>{comments.length}</span></button><button className={activeTab === 'summary' ? 'active' : ''} onClick={() => setActiveTab('summary')}><FileCheck size={15} />中文总评</button><div className="tab-spacer" />{review.gitlab_url ? <a href={review.gitlab_url} className="external-link" target="_blank" rel="noreferrer">在 GitLab 打开 <ExternalLink size={14} /></a> : <span className="external-link disabled-link">GitLab 链接未记录</span>}</div>
    {activeTab === 'summary' ? <SummaryPanel review={review} onNotify={onNotify} onSave={onSaveSummary} /> : activeTab === 'comments' ? <CommentList comments={comments} onPublish={onPublish} onResolve={onResolve} /> : <DiffPanel review={review} comments={comments} onPublish={onPublish} onResolve={onResolve} />}
  </>
}

function DiffPanel({ review, comments, onPublish, onResolve }: { review: BackendReview | null; comments: LineComment[]; onPublish: (id: string) => void; onResolve: (id: string) => void }) {
  const files = review?.changed_files || []
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [selectedCommentId, setSelectedCommentId] = useState<string | null>(null)
  const activeFile = selectedFile && files.includes(selectedFile) ? selectedFile : files[0]
  const selectedComments = comments.filter(comment => comment.file === activeFile)
  const activeComment = selectedComments.find(comment => comment.id === selectedCommentId) || selectedComments[0]
  const additions = review?.additions ?? 0
  const deletions = review?.deletions ?? 0

  useEffect(() => {
    if (!selectedCommentId) return
    const row = [...document.querySelectorAll<HTMLElement>('[data-comment-id]')].find(element => element.dataset.commentId === selectedCommentId)
    row?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }, [selectedCommentId, activeFile, activeComment?.line, activeComment?.side, activeComment?.location?.status])

  return <div className="detail-layout review-reader">
    <div className="reader-code-column">
      <Card title="变更文件" subtitle={files.length + ' 个文件 · +' + additions + ' / -' + deletions} className="files-card">
        <div className="file-list">{files.length ? files.map(file => {
          const count = comments.filter(comment => comment.file === file).length
          const FileIcon = count ? FileWarning : FileCheck
          return <button className={cn('file-item', file === activeFile && 'active')} key={file} onClick={() => { setSelectedFile(file); setSelectedCommentId(null) }}>
            <FileIcon size={16} /><span>{file}</span><span className={cn('file-count', count > 0 && 'danger')}>{count}</span>
          </button>
        }) : <EmptyLine>没有变更文件数据</EmptyLine>}</div>
      </Card>
      <Card title={activeFile || '代码差异'} subtitle="来自 GitLab MR 的实际变更上下文" action={activeFile ? <Button variant="ghost" icon={<Copy size={14} />} onClick={() => navigator.clipboard?.writeText(activeFile)}>复制路径</Button> : undefined} className="diff-card">
        <div className="diff-toolbar"><span className="diff-legend"><i className="diff-added" />新增 <i className="diff-removed" />删除</span><span className="diff-position">{review?.diff_text ? 'GitLab unified diff' : '等待 GitLab diff'}</span></div>
        <div className="code-diff">{review?.diff_text ? <UnifiedDiff text={diffForFile(review.diff_text, activeFile)} comments={selectedComments} activeCommentId={activeComment?.id} /> : <EmptyLine>暂无 diff 数据</EmptyLine>}</div>
      </Card>
    </div>
    <aside className="reader-issues" aria-label="问题导航">
      <Card title="问题导航" subtitle={comments.length + ' 条行级评论 · 选择问题定位代码'} className="reader-issue-list">
        {comments.length ? comments.map(comment => <button key={comment.id} className={cn('reader-issue-choice', activeComment?.id === comment.id && 'active')} aria-label={'查看问题：' + comment.title} aria-pressed={activeComment?.id === comment.id} onClick={() => { setSelectedFile(comment.file); setSelectedCommentId(comment.id) }}>
          <span className={cn('severity', 'severity-' + comment.level.toLowerCase())}>{comment.level}</span>
          <span><strong>{comment.title}</strong><small>{comment.file}:{comment.line || '行号待核对'} · {comment.status}</small></span>
        </button>) : <EmptyLine>暂无行级评论</EmptyLine>}
      </Card>
      <section id="inline-comments" className="reader-explanation card" aria-label="问题说明">
        <div className="card-header"><div><h2>问题说明</h2><p>阅读依据后可发布或解决</p></div></div>
        {activeComment ? <InlineComment comment={activeComment} onPublish={onPublish} onResolve={onResolve} /> : <EmptyLine>选择一个问题查看说明</EmptyLine>}
      </section>
    </aside>
  </div>
}

export function UnifiedDiff({ text, comments, activeCommentId }: { text: string; comments: LineComment[]; activeCommentId?: string }) {
  let oldLine = 0
  let newLine = 0
  let inHunk = false
  return <>{text.split('\n').map((rawLine, index) => {
    if (rawLine.startsWith('diff --git')) { inHunk = false; return null }
    if (rawLine.startsWith('@@')) {
      const match = rawLine.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
      if (match) { oldLine = Number(match[1]); newLine = Number(match[2]); inHunk = true }
      return <div className="diff-hunk" key={`${rawLine}-${index}`}>{rawLine}</div>
    }
    if (!inHunk || rawLine === '') return null
    const tone = rawLine.startsWith('+') ? 'added' : rawLine.startsWith('-') ? 'removed' : undefined
    const textValue = tone ? rawLine.slice(1) : rawLine.startsWith(' ') ? rawLine.slice(1) : rawLine
    const line = tone === 'removed' ? undefined : String(newLine || '')
    const old = tone === 'added' ? undefined : String(oldLine || '')
    if (rawLine.startsWith('\\ No newline')) return <div className="diff-hunk" key={index}>{rawLine}</div>
    const matching = comments.filter((item) => item.location?.status !== 'unverified' && (item.side === 'old'
      ? tone !== 'added' && item.line === oldLine
      : tone !== 'removed' && item.line === newLine))
    const comment = matching.find(item => item.id === activeCommentId) || matching[0]
    if (tone === 'removed') oldLine += 1
    else if (tone === 'added') newLine += 1
    else { oldLine += 1; newLine += 1 }
    return <CodeLine key={`${index}-${rawLine}`} line={line || ''} old={old} text={textValue} tone={tone} prefix={tone === 'added' ? '+' : tone === 'removed' ? '-' : ' '} comment={comment?.id} active={comment?.id === activeCommentId} />
  })}</>
}

function CodeLine({ line, old, text, tone, prefix = ' ', comment, active }: { line: string; old?: string; text: string; tone?: 'added' | 'removed'; prefix?: string; comment?: string; active?: boolean }) {
  return <div data-comment-id={comment} className={cn('code-line', tone && `code-${tone}`, comment && 'code-with-comment', active && 'code-comment-active')}><span className="line-no">{old || ''}</span><span className="line-no">{line}</span><span className="line-prefix">{prefix}</span><code>{text}</code>{comment && <span className="comment-anchor"><MessageSquare size={13} /></span>}</div>
}

function InlineComment({ comment, onPublish, onResolve }: { comment: LineComment; onPublish: (id: string) => void; onResolve: (id: string) => void }) {
  const tone: StatusTone = comment.level === 'P0' || comment.level === 'P1' || comment.level === 'UNRATED' ? 'danger' : 'warning'
  return <div className={cn('inline-comment', comment.status === '已解决' && 'inline-comment-resolved')}><div className="comment-marker"><span className={cn('severity', `severity-${comment.level.toLowerCase()}`)}>{comment.level}</span><span className="comment-location">{comment.file}:{comment.line || '行号待核对'}{comment.side === 'old' && comment.line > 0 ? '（旧侧）' : ''}</span></div><div className="comment-body"><div className="comment-title"><strong>{comment.title}</strong><Badge tone={comment.status === '已解决' ? 'success' : comment.status === '已发布' ? 'info' : comment.status === '发布失败' ? 'danger' : tone}>{comment.status}</Badge></div><CommentLocationNotice comment={comment} /><p>{comment.body}</p><div className="comment-actions">{(comment.status === '待发布' || comment.status === '发布失败') && <Button variant="primary" disabled={comment.location?.status === 'unverified' || comment.location?.status === 'relocated'} icon={<Send size={13} />} onClick={() => onPublish(comment.id)}>发布到 GitLab</Button>}{comment.status !== '已解决' && <Button variant="ghost" icon={<Check size={13} />} onClick={() => onResolve(comment.id)}>标记已解决</Button>}{comment.status === '已发布' && <span className="comment-synced"><CheckCircle2 size={14} />已同步到 GitLab</span>}</div></div></div>
}

function CommentLocationNotice({ comment }: { comment: LineComment }) {
  if (!comment.location || comment.location.status === 'exact') return null
  const original = comment.location.original_new_line || comment.location.original_old_line
  const label = comment.status === '已发布' || comment.status === '已解决' ? 'GitLab 原发布位置' : '原记录位置'
  return <p className="side-note" role="status">{comment.location.status === 'relocated'
    ? `已按原代码定位到第 ${comment.line} 行；${label}：${original || '未记录'}。`
    : `${comment.location.reason}；${label}：${original || '未记录'}。`}
    {(comment.status === '待发布' || comment.status === '发布失败') && '请重新评审后发布。'}</p>
}

function CommentList({ comments, onPublish, onResolve }: { comments: LineComment[]; onPublish: (id: string) => void; onResolve: (id: string) => void }) {
  const p0 = comments.filter((comment) => comment.level === 'P0').length
  const p1 = comments.filter((comment) => comment.level === 'P1').length
  const p2 = comments.filter((comment) => comment.level === 'P2').length
  const unrated = comments.filter((comment) => comment.level === 'UNRATED').length
  const pending = comments.filter((comment) => (comment.status === '待发布' || comment.status === '发布失败') && (!comment.location || comment.location.status === 'exact'))
  return <Card title="所有行级评论" subtitle="每条评论都绑定到具体文件和行号，可直接同步到 GitLab" className="comments-page-card"><div className="comments-filter"><Badge tone="danger" dot>{p0 + p1} 个 P0/P1</Badge><Badge tone="warning" dot>{p2} 个 P2</Badge>{unrated > 0 && <Badge tone="danger" dot>{unrated} 个未评级（门禁阻断）</Badge>}<span className="toolbar-spacer" /><Button variant="secondary" disabled={!pending.length} icon={<Send size={14} />} onClick={() => pending.forEach((comment) => onPublish(comment.id))}>批量发布待发布评论</Button></div><div className="comment-list">{comments.length ? comments.map((comment) => <InlineComment key={comment.id} comment={comment} onPublish={onPublish} onResolve={onResolve} />) : <EmptyLine>暂无行级评论</EmptyLine>}</div></Card>
}

function SummaryPanel({ review, onNotify, onSave }: { review: BackendReview; onNotify: () => void; onSave: (summary: string) => void }) {
  const summary = review.summary
  const fallback = ''
  const [draft, setDraft] = useState(summary || fallback)
  useEffect(() => setDraft(summary || fallback), [summary])
  const generated = Boolean(summary)
  const blockers = review.blocking_comment_count ?? 0
  const finished = ['completed', 'blocked'].includes(review.status)
  const decision = review.status === 'failed' ? '评审失败' : !finished ? '待评审完成' : blockers ? '需要修改' : '无未解决阻断'
  const notificationLabels: Record<string, string> = { done: '投递已处理', failed: '投递失败', unknown: '结果待核对', pending: '待发送', sending: '发送中' }
  return <div className="summary-layout">
    <Card title="中文总评" subtitle={generated ? '模型说明可编辑；评审结论依据实际行级评论' : '等待模型生成中文总评'} action={<Badge tone={generated ? 'success' : 'neutral'} dot>{generated ? '已生成' : '未生成'}</Badge>} className="summary-main">
      <div className="summary-status"><div className="summary-score"><span>当前审查结论</span><strong>{decision}</strong><small>{blockers} 条未解决阻断</small></div>
        <div className="summary-actions"><Button variant="secondary" disabled={!draft.trim()} icon={<Save size={14} />} onClick={() => onSave(draft)}>保存并同步</Button><Button variant="soft" disabled={!generated} icon={<Bell size={14} />} onClick={onNotify}>发送到企业微信</Button></div>
      </div>
      <p className="side-note">模型说明不参与门禁判定；企业微信通知按当前行级评论生成问题清单。</p>
      <textarea aria-label="模型总评说明" className="summary-editor" value={draft} onChange={event => setDraft(event.target.value)} placeholder="尚未生成中文总评" />
      <div className="summary-footer"><span><Sparkles size={14} />{review.engine_model || '等待真实模型结果'}</span><span>全局要求 + 项目补充 · 中文输出</span></div>
    </Card>
    <Card title="门禁结果" subtitle="依据当前未解决行级评论" className="summary-side">
      <div className={cn('gate-result', blockers || review.status === 'failed' ? 'danger' : finished ? 'success' : 'neutral')}><div className="gate-result-icon"><Lock size={17} /></div><div><strong>{blockers ? '存在未解决阻断' : finished ? '当前阻断已清零' : '评审尚未完成'}</strong><span>{review.gate_reason || '以最新后端评估结果为准'}</span></div></div>
      <div className="gate-checks"><div><CircleDot size={16} /><span>未解决阻断</span><b>{blockers} 条</b></div><div><CircleDot size={16} /><span>评审任务状态</span><b>{review.status_label || ({ completed: '已完成', blocked: '有阻断', failed: '失败', queued: '排队中', running: '运行中' } as Record<string, string>)[review.status]}</b></div><div><CircleDot size={16} /><span>中文总评</span><b>{generated ? '已生成' : '待生成'}</b></div><div><CircleDot size={16} /><span>企业微信通知</span><b>{notificationLabels[review.notification_state || 'pending'] || '未记录'}</b></div></div>
      <Button variant="soft" className="full-button" disabled={!generated} onClick={onNotify}>发送通知 <ArrowUpRight size={14} /></Button>
    </Card>
  </div>
}

function PolicyPage({ saving, saveError, projectName, policy, onChange, onSave, onNotify, engineConfig, engineLoadStatus, onReloadEngine, engineSaving, readOnly, onSaveEngine }: { saving: boolean; saveError?: string; projectName: string; policy: PolicyState; onChange: (value: PolicyState) => void; onSave: (value: PolicyState) => void; onNotify: (message: string, tone?: 'success' | 'info' | 'warning') => void; engineConfig: ProjectEngineConfig | null; engineLoadStatus: 'loading' | 'ready' | 'error'; onReloadEngine: () => void; engineSaving: boolean; readOnly: boolean; onSaveEngine: ConfigSave }) {
  const [advanced, setAdvanced] = useState(false)
  const [promptError, setPromptError] = useState('')
  useEffect(() => { if (saveError && /提示词|summary_prompt/.test(saveError)) setAdvanced(true) }, [saveError])
  const update = <K extends keyof PolicyState>(key: K, value: PolicyState[K]) => onChange({ ...policy, [key]: value })
  const promptDraft = policy.prompt
  const setPromptDraft = (value: string) => update('prompt', value)
  return <div className="settings-layout"><div className="settings-main">
    <ProjectReviewPolicy policy={policy} onChange={onChange} onValidityChange={setPromptError} saving={saving} readOnly={readOnly} />
    <fieldset disabled={readOnly} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      <Card title="自动触发" subtitle={`当前项目：${projectName} · 配置 GitLab 事件和命令触发方式`}>
        <div className="setting-row"><div className="setting-icon blue"><Webhook size={17} /></div><div className="setting-copy"><strong>MR 创建 / push 自动触发</strong><span>接收 GitLab merge_request webhook，自动开始完整评审。</span></div><Toggle label="MR 创建 / push 自动触发" checked={policy.webhookTrigger} onChange={(value) => update('webhookTrigger', value)} /></div>
        <div className="setting-row"><div className="setting-icon purple"><Terminal size={17} /></div><div className="setting-copy"><strong>支持 <code>/review</code> 命令</strong><span>在 MR 评论中输入 /review，重新执行完整评审。</span></div><Toggle label="支持 /review 命令" checked={policy.commandReview} onChange={(value) => update('commandReview', value)} /></div>
        <div className="setting-row"><div className="setting-icon orange"><Sparkles size={17} /></div><div className="setting-copy"><strong>支持 <code>/improve</code> 命令</strong><span>只生成针对当前上下文的定向改进建议。</span></div><Toggle label="支持 /improve 命令" checked={policy.commandImprove} onChange={(value) => update('commandImprove', value)} /></div>
      </Card>
      <section className="card policy-advanced"><button className="policy-advanced-toggle" aria-expanded={advanced} aria-controls="policy-advanced-content" onClick={() => setAdvanced(value => !value)}>高级设置 <ChevronDown size={16} /></button><p className="side-note">项目通用补充要求：用于补充仓库约定，与三类专项提示词一起生效。</p>{advanced && <div id="policy-advanced-content"><label className="field"><span>中文总评提示词</span><textarea aria-label="中文总评提示词" className="prompt-editor" value={promptDraft} onChange={(event) => setPromptDraft(event.target.value)} /></label><div className="prompt-footer"><span><Sparkles size={14} />保存后，对新触发的评审生效</span><Button variant="ghost" icon={<Eye size={14} />} onClick={() => onNotify('保存后，对真实 MR 执行评审即可生成总评。', 'info')}>如何生成总评</Button></div></div>}</section>
      {saveError && <p role="alert" className="form-error">{saveError}</p>}
      <div className="policy-save-bar"><span>{saving ? '正在保存项目策略…' : '提示词用于新评审；阻断门槛按已保存规则计算'}</span><Button disabled={saving || Boolean(promptError)} icon={<Save size={14} />} onClick={() => onSave({ ...policy, prompt: promptDraft })}>保存项目策略</Button></div>
    </fieldset>
    <EngineConfigPanelConnected initial={engineConfig} scope="project" projectName={projectName} onSave={onSaveEngine} verificationBusy={engineSaving} readOnly={readOnly} loadStatus={engineLoadStatus} onReload={onReloadEngine} />
  </div><aside className="settings-side"><Card title="当前策略摘要" subtitle={projectName}><div className="policy-summary">{([{name:'安全性', enabled:policy.security, text:policy.securityPrompt}, {name:'可靠性', enabled:policy.reliability, text:policy.reliabilityPrompt}, {name:'代码风格', enabled:policy.style, text:policy.stylePrompt}]).map(item => <div key={item.name}><span>{item.name}</span><strong>{item.enabled ? '启用' : '关闭'} · {item.text?.trim() ? '项目自定义' : '继承平台'}</strong></div>)}<div><span>自动触发</span><strong>{policy.webhookTrigger ? '已开启' : '已关闭'}</strong></div><div><span>命令入口</span><strong>{[policy.commandReview && '/review', policy.commandImprove && '/improve'].filter(Boolean).join('、') || '无'}</strong></div><div><span>阻断门槛</span><strong className="danger-text">{thresholdLabel(policy)}</strong></div><div><span>评审引擎</span><strong>{engineConfig?.engines.find(engine => engine.id === engineConfig.effective_engine)?.label || '加载中'}</strong></div></div></Card><Card title="提示词生效顺序" subtitle="平台通用 → 当前模式 → 开启的专项 → 项目补充"><div className="inherit-note"><div className="setting-icon blue"><SlidersHorizontal size={16} /></div><div><strong>项目自定义优先</strong><span>每类专项可独立继承或自定义；平台通用要求始终保留。提示词只用于新评审，门槛按保存后的当前规则计算。</span></div></div></Card></aside></div>
}

function RuleCard({ title, description, icon, checked, onChange, tone }: { title: string; description: string; icon: ReactNode; checked: boolean; onChange: (value: boolean) => void; tone: 'green' | 'blue' | 'purple' }) {
  return <div className={cn('rule-card', checked && 'rule-card-checked')}><div className={cn('setting-icon', tone)}>{icon}</div><div><strong>{title}</strong><span>{description}</span></div><Toggle label={title} checked={checked} onChange={onChange} /></div>
}

function Toggle({ label, checked, onChange, disabled = false }: { label: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean }) { return <button type="button" className={cn('toggle', checked && 'toggle-on')} role="switch" aria-label={label} aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}><span /></button> }


type ConfigSave = (payload: Record<string, unknown>) => void | boolean | Promise<void | boolean>

function ConfigInheritance({ initial, scope, onSave, children, editing, onEdit }: { initial: Record<string, unknown> | null; scope: 'global' | 'project'; onSave: ConfigSave; children: ReactNode; editing?: boolean; onEdit?: () => void }) {
  const [localEditing, setLocalEditing] = useState(false)
  const customizing = editing ?? localEditing
  const customize = () => onEdit ? onEdit() : setLocalEditing(true)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  const active = useRef(true)
  const trigger = useRef<HTMLElement | null>(null)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  function close() { setConfirming(false); trigger.current?.focus() }
  async function restore() {
    if (pending.current) return
    pending.current = true; setBusy(true); setError('')
    try {
      const result = await onSave({ inherited: true })
      if (!active.current) return
      if (result === false) setError('恢复继承失败，当前配置未切换，请重试')
      else close()
    } catch { if (active.current) setError('恢复继承失败，当前配置未切换，请重试') }
    finally { pending.current = false; if (active.current) setBusy(false) }
  }
  useEffect(() => setLocalEditing(false), [initial, scope])
  const inherited = scope === 'project' && initial?.inherited === true
  return <>
    {scope === 'project' && <div className="scope-bar">
      <div><strong>{inherited ? '继承平台默认配置' : '使用项目自定义配置'}</strong><span>{inherited ? '继承时只读；点击自定义后编辑，保存才生效。' : '仅影响当前项目；恢复继承不会修改平台默认或删除已保存的覆盖配置。'}</span></div>
      {inherited ? <Button variant="secondary" disabled={customizing || busy} onClick={customize}>{customizing ? '正在编辑项目配置' : '自定义本项目'}</Button> : <Button variant="secondary" disabled={busy} onClick={() => { trigger.current = document.activeElement as HTMLElement; setError(''); setConfirming(true) }}>恢复全局继承</Button>}
    </div>}
    <fieldset disabled={busy || (inherited && !customizing)} style={{border:0, padding:0, margin:0, minWidth:0}}>{children}</fieldset>
    {confirming && <InheritanceDialog busy={busy} error={error} onCancel={close} onConfirm={() => void restore()} />}
  </>
}

export function ModelsPageConnected({ onDirtyChange, onInvalidate, probeMessage = '', initial, modelOptions, scope, projectName, onScopeChange, tested, onTest, onSave, engineConfig, engineLoadStatus = 'loading', onReloadEngines, engineSaving = false, onSaveEngine, onTestEngine }: { onDirtyChange?: (dirty: boolean) => void; onInvalidate?: () => void; probeMessage?: string; initial: Record<string, unknown> | null; modelOptions: string[]; scope: 'global' | 'project'; projectName: string; onScopeChange: (scope: 'global' | 'project') => void; tested: boolean; onTest: (payload: Record<string, unknown>) => void; onSave: ConfigSave; engineConfig?: GlobalEngineConfig | null; engineLoadStatus?: 'loading' | 'ready' | 'error'; onReloadEngines?: () => void; engineSaving?: boolean; onSaveEngine?: ConfigSave; onTestEngine?: (engine: string) => void }) {
  const [editing, setEditing] = useState(false)
  useEffect(() => setEditing(false), [initial, scope])
  const [form, setForm, resetForm] = useDraftState({
    gateway_type: 'CCSwitch / OpenAI 兼容',
    request_timeout_s: '120',
    base_url: '',
    api_key: '',
    model: '',
  }, onDirtyChange)
  useEffect(() => {
    if (!initial) return
    resetForm((current) => ({
      ...current,
      gateway_type: typeof initial.gateway_type === 'string' ? initial.gateway_type : current.gateway_type,
      request_timeout_s: String(initial.request_timeout_s || 120),
      api_key: '',
      base_url: typeof initial.base_url === 'string' ? initial.base_url : '',
      model: typeof initial.model === 'string' ? initial.model : '',
    }))
  }, [initial])
  const update = (key: keyof typeof form, value: string) => { onInvalidate?.(); setForm((current) => ({ ...current, [key]: value })) }
  const selectableModels = Array.from(new Set([form.model, ...modelOptions].filter(Boolean)))
  const { save, saving, saveError } = useConfigSave(onSave, () => resetForm(current => ({ ...current, api_key: '' })))
  const modelHint = modelOptions.length
    ? `已从当前模型网关获取 ${modelOptions.length} 个模型`
    : tested
      ? '当前模型网关未返回模型目录，可直接填写实际模型 ID'
      : '尚未获取真实列表，可先测试连接或手动填写模型 ID'
  const configSource = initial && (initial.api_key_masked || initial.base_url || initial.model) ? '页面保存配置' : appMode() === 'test' ? '环境变量兜底' : '未配置'
  const baseUrlPlaceholder = configSource === '未配置' ? '填写模型网关的兼容 API 地址' : '留空使用平台环境配置的网关地址'
  const testPayload = { ...form, request_timeout_s: Number(form.request_timeout_s), api_key: form.api_key || undefined }
  return <fieldset className="notification-form-boundary" disabled={saving} aria-busy={saving}>{saving && <p role="status">正在保存配置…</p>}{saveError && <p role="alert" className="form-error">{saveError}</p>}<EffectiveConfigSummary kind="model" scope={scope} projectName={projectName} initial={initial} editing={editing} onEdit={() => setEditing(true)} /><ConfigInheritance initial={initial} scope={scope} onSave={onSave} editing={editing} onEdit={() => setEditing(true)}><div className="scope-bar"><div><strong>配置范围</strong><span>{scope === 'global' ? '所有未覆盖项目继承全局模型' : initial?.inherited === true ? `当前继承全局；保存后才覆盖 ${projectName}` : `编辑 ${projectName} 的项目配置`}</span></div><span aria-label="模型配置范围">{scope === 'global' ? '平台全局默认' : `项目配置 · ${projectName}`}</span></div><div className="config-source-banner"><CircleDot size={14} /><span>配置来源：<strong>{configSource}</strong>。页面保存的字段优先，留空字段才回退环境变量。</span></div><div className="settings-layout"><div className="settings-main"><Card className="model-draft-card" title={scope === 'global' ? '全局模型网关' : `${projectName} · 项目模型覆盖`} subtitle={scope === 'global' ? '项目未单独配置时，将继承这里的连接和默认模型' : '仅对当前项目生效，未填写的安全字段沿用平台策略'}><div className="form-grid"><Field label="网关类型"><select value={form.gateway_type} onChange={(event) => update('gateway_type', event.target.value)}><option>CCSwitch / OpenAI 兼容</option><option>Anthropic 兼容</option><option disabled>Gemini 原生协议（未接入）</option></select></Field><Field label="Base URL"><input value={form.base_url} onChange={(event) => update('base_url', event.target.value)} placeholder={baseUrlPlaceholder} /></Field><Field label="API Key" hint="密钥仅在服务端保存，页面只展示掩码"><input type="password" value={form.api_key} onChange={(event) => update('api_key', event.target.value)} placeholder="留空表示保留现有密钥" /></Field><Field label="默认模型" hint={modelHint}><select aria-label="默认模型" value={form.model} onChange={(event) => update('model', event.target.value)}>{!form.model && selectableModels.length > 0 && <option value="">请选择模型</option>}{selectableModels.length ? selectableModels.map((model) => <option key={model} value={model}>{model}{!modelOptions.includes(model) ? '（当前/手动）' : ''}</option>) : <option value="">请先测试连接加载模型</option>}</select></Field><Field label="模型 ID" hint="可直接填写当前模型网关支持的模型 ID"><input aria-label="模型 ID" value={form.model} onChange={(event) => update('model', event.target.value)} placeholder="输入实际模型 ID" /></Field><Field label="模型请求超时（秒）" hint="10–600 秒，大型 MR 建议至少 120 秒"><input aria-label="模型请求超时（秒）" type="number" min="10" max="600" value={form.request_timeout_s} onChange={e => update('request_timeout_s', e.target.value)} /></Field></div><div className="connection-row"><div className={cn('connection-state', tested ? 'connection-ok' : 'connection-idle')}><span className="pulse-dot" />{tested ? modelOptions.length ? '模型目录已同步 · 尚不代表模型可调用' : '网关已连通 · 未返回模型目录，可手动填写模型 ID' : '尚未读取模型目录'}</div><div className="button-group"><Button variant="secondary" icon={<Save size={15} />} onClick={() => void save(testPayload)}>{scope === 'project' ? '保存项目模型配置' : '保存模型配置'}</Button><Button variant="secondary" icon={tested ? <CheckCircle2 size={15} /> : <Zap size={15} />} onClick={() => onTest(testPayload)}>{tested ? '刷新模型列表' : '测试连接'}</Button><Button variant="secondary" disabled={!form.model || probeMessage === '正在验证模型调用…'} onClick={() => onTest({ ...testPayload, probe: true })}>验证模型调用</Button></div></div></Card>{probeMessage && <p role="status" className="side-note">{probeMessage}</p>}{scope === 'global' && onSaveEngine && onTestEngine && <EngineConfigPanelConnected initial={engineConfig || null} scope="global" projectName={projectName} onSave={onSaveEngine} onTest={onTestEngine} verificationBusy={engineSaving} loadStatus={engineLoadStatus} onReload={onReloadEngines} />}</div><aside className="settings-side"><Card title="项目继承关系" subtitle={`当前项目：${projectName}`}><div className="inherit-flow"><div className="inherit-node"><div className="engine-logo blue"><Server size={15} /></div><div><strong>全局模型网关</strong><span>{form.gateway_type}</span></div></div><div className="inherit-arrow"><ChevronDown size={16} />{scope === 'global' ? '按项目策略生效' : initial?.inherited === true ? '继承' : initial ? '覆盖' : '等待加载'}</div><div className="inherit-node active"><div className="engine-logo purple"><Bot size={15} /></div><div><strong>项目审查模型</strong><span>{form.model || '未配置模型'}</span></div><Badge tone={scope === 'project' && initial?.inherited === false ? 'warning' : 'info'}>{scope === 'global' ? '全局默认' : initial?.inherited === true ? '继承全局' : initial ? '项目覆盖' : '等待加载'}</Badge></div></div><div className="side-note"><CircleDot size={14} />项目可以通过项目级配置覆盖模型，API Key 仍由平台统一管理。</div></Card><Card title="安全提示"><div className="security-note"><Lock size={17} /><span>API Key 不会写入浏览器 localStorage，也不会出现在评审日志中。</span></div></Card></aside></div></ConfigInheritance></fieldset>
}

function engineAvailability(item: EngineCatalogItem): { label: string; tone: StatusTone } {
  if (item.selectable) return { label: '可用于评审', tone: 'success' }
  if (!item.installed) return { label: '未安装', tone: 'neutral' }
  if (item.availability === 'model-not-configured') return { label: '模型未配置', tone: 'warning' }
  return { label: '待验证', tone: 'warning' }
}

export function EngineConfigPanelConnected({ initial, scope, projectName, onSave, onTest, verificationBusy = false, readOnly = false, loadStatus = 'ready', onReload }: { initial: GlobalEngineConfig | ProjectEngineConfig | null; scope: 'global' | 'project'; projectName: string; onSave: ConfigSave; onTest?: (engine: string) => void; verificationBusy?: boolean; readOnly?: boolean; loadStatus?: 'loading' | 'ready' | 'error'; onReload?: () => void }) {
  const catalog = initial?.engines || []
  const projectConfig = scope === 'project' ? initial as ProjectEngineConfig | null : null
  const currentEngine = initial?.effective_engine || (scope === 'global' ? (initial as GlobalEngineConfig | null)?.default_engine : undefined) || 'builtin'
  const [selectedEngine, setSelectedEngine] = useState(currentEngine)
  useEffect(() => setSelectedEngine(currentEngine), [currentEngine, initial])
  const currentItem = catalog.find(item => item.id === currentEngine)
  const selectedItem = catalog.find(item => item.id === selectedEngine)
  const { save, saving, saveError } = useConfigSave(onSave, () => undefined)
  const title = scope === 'global' ? '平台默认评审引擎' : `${projectName} · 评审引擎覆盖`
  const selectorLabel = scope === 'global' ? '平台默认评审引擎' : '项目评审引擎'
  const saveLabel = scope === 'global' ? '保存平台默认引擎' : '保存项目引擎配置'
  const editor = <>
    {saving && <p role="status">正在保存评审引擎配置…</p>}
    {verificationBusy && <p role="status">正在运行隔离的合成 MR 推理验证…</p>}
    {saveError && <p role="alert" className="form-error">{saveError}</p>}
    <div className="scope-bar"><div><strong>{scope === 'global' ? '引擎与模型连接分开配置' : '引擎继承与模型继承彼此独立'}</strong><span>{scope === 'global' ? '此处只选择评审运行时；模型网关仍在上方单独管理。' : '项目可单独覆盖平台默认引擎，不会改变项目模型或通知继承。'}</span></div><span>{scope === 'global' ? '平台全局默认' : `项目配置 · ${projectName}`}</span></div>
    <div className="form-grid"><Field label={selectorLabel} hint={selectedItem?.reason || '只有通过合成 MR 推理验证的引擎才可设为默认'}><select aria-label={selectorLabel} value={selectedEngine} onChange={event => setSelectedEngine(event.target.value)}>{catalog.map(item => <option key={item.id} value={item.id} disabled={!item.selectable}>{item.label}{item.selectable ? '' : `（${engineAvailability(item).label}）`}</option>)}</select></Field><div className="connection-state connection-idle"><span className="pulse-dot" />当前生效：<strong>{currentItem?.label || currentEngine}</strong>{scope === 'project' && <span> · {projectConfig?.inherited ? '继承平台默认' : '项目覆盖'}</span>}</div><div className="button-group"><Button variant="secondary" icon={<Save size={15} />} disabled={!selectedItem?.selectable || (selectedEngine === currentEngine && !projectConfig?.inherited)} onClick={() => void save(scope === 'global' ? { default_engine: selectedEngine } : { engine: selectedEngine })}>{saveLabel}</Button></div></div>
  </>
  const runtimeTable = <ResizableTable tableId={`engine-runtime-${scope}`} label={`${title}运行状态`} columns={tableColumns.engineRuntime} stickyLastColumn><tbody>
      {catalog.map(item => {
        const status = engineAvailability(item)
        return <tr key={item.id}>
          <td><div className="engine-name"><div className="engine-logo blue"><Bot size={15} /></div><div><strong>{item.label}</strong><span>{item.version || item.reason || '运行时版本未探测'}</span></div></div></td>
          <td><div className="capability-tags">{item.capabilities.map(capability => <Badge tone="neutral" key={capability}>{capability}</Badge>)}</div></td>
          <td><Badge tone={status.tone} dot>{status.label}</Badge>{item.reason && !item.selectable && <span className="muted-text"> · {item.reason}</span>}</td>
          <td>{item.id === 'builtin' ? <span className="muted-text">内置可用</span> : scope === 'project' ? <span className="muted-text">{item.selectable ? '平台已验证' : '请平台管理员验证'}</span> : <Button variant="ghost" disabled={readOnly || saving || !item.installed || item.availability === 'model-not-configured' || verificationBusy} onClick={() => onTest?.(item.id)}>验证引擎</Button>}</td>
        </tr>
      })}
    </tbody></ResizableTable>
  return <Card title={title} subtitle={scope === 'global' ? 'PR-Agent、OpenCodeReview 和 Pi Agent 均由智审台统一校验并发布 GitLab 行级评论、中文总评与企微通知。' : '当前项目使用独立的引擎继承/覆盖设置；只影响新发起的评审。'}><fieldset disabled={saving || verificationBusy || (readOnly && Boolean(initial))} aria-busy={saving || verificationBusy} className="notification-form-boundary">{!initial ? loadStatus === 'error' ? <div role="status" className="side-note"><span>{scope === 'project' ? '项目评审引擎配置加载失败；其他项目设置仍可独立使用。' : '平台引擎状态加载失败，默认引擎未被修改。'}</span>{onReload && <Button variant="secondary" onClick={onReload}>重新加载引擎配置</Button>}</div> : <p role="status">正在加载引擎运行时状态…</p> : scope === 'project' ? <ConfigInheritance initial={projectConfig as unknown as Record<string, unknown> | null} scope="project" onSave={onSave}>{editor}</ConfigInheritance> : editor}</fieldset>{initial && runtimeTable}</Card>
}

export function NotificationsPageConnected({ onDirtyChange, initial, scope, projectId = null, projects = [], auditRefreshKey = 0, projectName, onScopeChange, tested, testing = false, readOnly = false, onInvalidate, onTest, onReloadAudit, onSave, auditLogs = [], auditStatus = 'ready' }: { onDirtyChange?: (dirty: boolean) => void; initial: Record<string, unknown> | null; scope: 'global' | 'project'; projectId?: string | null; projects?: Project[]; auditRefreshKey?: number; projectName: string; onScopeChange: (scope: 'global' | 'project') => void; tested: boolean; testing?: boolean; readOnly?: boolean; onInvalidate?: () => void; onTest: (payload: Record<string, unknown>) => void; onReloadAudit: () => void; auditStatus?: 'loading' | 'ready' | 'error'; onSave: ConfigSave; auditLogs?: Array<Record<string, unknown>> }) {
  const [editing, setEditing] = useState(false)
  const [webhookError, setWebhookError] = useState('')
  useEffect(() => setEditing(false), [initial, scope])
  const [form, setForm, resetForm] = useDraftState({ name: '', webhook_url: '', language: '中文', enabled: true, notify_suggestions: true, notify_blockers: true, notify_merge_result: true }, onDirtyChange)
  const [historyProject, setHistoryProject] = useState('')
  const [globalRows, setGlobalRows] = useState<Array<Record<string, unknown>>>([])
  const [globalAuditStatus, setGlobalAuditStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [preview, setPreview] = useState<{ review_id: string | null; content: string; will_send: boolean } | null>(null)
  useEffect(() => {
    if (scope !== 'global' || appMode() === 'test') return
    let active = true
    setGlobalAuditStatus('loading')
    void api.auditLogs(historyProject || undefined).then(rows => { if (active) { setGlobalRows(rows); setGlobalAuditStatus('ready') } }).catch(() => { if (active) setGlobalAuditStatus('error') })
    return () => { active = false }
  }, [scope, historyProject, auditRefreshKey])
  useEffect(() => {
    if (scope !== 'project' || !projectId || appMode() === 'test') return
    let active = true
    setPreview(null)
    void api.projectNotificationPreview(projectId).then(value => { if (active) setPreview(value) }).catch(() => { if (active) setPreview({ review_id: null, content: '', will_send: false }) })
    return () => { active = false }
  }, [scope, projectId, auditRefreshKey, initial])
  useEffect(() => {
    if (!initial) return
    setWebhookError('')
    resetForm((current) => ({
      ...current,
      webhook_url: '',
      name: typeof initial.name === 'string' ? initial.name : '',
      language: '中文',
      enabled: initial.enabled !== false,
      notify_suggestions: initial.notify_suggestions !== false,
      notify_blockers: initial.notify_blockers !== false,
      notify_merge_result: initial.notify_merge_result !== false,
    }))
  }, [initial])
  const update = (key: keyof typeof form, value: string | boolean) => { onInvalidate?.(); setWebhookError(''); setForm((current) => ({ ...current, [key]: value })) }
  // Only delivery audit records belong in the “最近发送记录” list.  Configuration
  // changes are audit events too, but they do not mean a message was delivered.
  const displayedAudit = scope === 'global' && appMode() !== 'test' ? globalRows : auditLogs
  const displayedAuditStatus = scope === 'global' && appMode() !== 'test' ? globalAuditStatus : auditStatus
  const remoteHistory = displayedAudit
    .filter((log) => typeof log.action === 'string' && (log.action.includes('发送企业微信评审通知') || log.action.includes('企业微信通知失败') || log.action.includes('测试企业微信通知') || log.action.includes('企业微信投递结果不确定') || log.action === '自动合并结果' || log.action === '重发自动合并结果通知'))
    .slice(0, 6)
    .map((log, index) => {
      const action = typeof log.action === 'string' ? log.action : '通知发送'
      const target = typeof log.target === 'string' && log.target ? log.target : projectName
      const metadata = typeof log.metadata === 'object' && log.metadata !== null ? log.metadata as Record<string, unknown> : {}
      const delivery = notificationDeliveryStatus(action, metadata)
      return {
        key: `remote-${index}-${String(log.created_at || '')}`,
        event: `${target} · ${action.replace('发送企业微信评审通知', '评审通知').replace('企业微信通知失败', '通知异常').replace('测试企业微信通知', '测试消息')}`,
        time: typeof log.created_at === 'string' ? new Date(log.created_at).toLocaleString('zh-CN') : '刚刚',
        ...delivery,
        retryHint: action === '测试企业微信通知' ? '检查机器人配置后再次发送测试' : '请到对应评审详情重新发送',
      }
    })
  const historyRows = remoteHistory
  const { save, saving, saveError } = useConfigSave(onSave, () => resetForm(current => ({ ...current, webhook_url: '' })))
  const testPayload = { ...form, webhook_url: form.webhook_url.trim() || undefined }
  const withValidWebhook = (action: () => void) => {
    if (testPayload.webhook_url) {
      try {
        const address = new URL(testPayload.webhook_url)
        if (!['http:', 'https:'].includes(address.protocol) || !address.hostname) throw new Error('invalid webhook')
      } catch {
        setWebhookError('请输入有效的 HTTP 或 HTTPS Webhook 地址；输入内容已保留')
        return
      }
    }
    setWebhookError('')
    action()
  }
  return <>
    <fieldset className="notification-form-boundary" disabled={testing || readOnly || saving} aria-busy={saving}>
      {saving && <p role="status">正在保存配置…</p>}{(webhookError || saveError) && <p role="alert" className="form-error">{webhookError || saveError}</p>}
      <EffectiveConfigSummary kind="notification" scope={scope} projectName={projectName} initial={initial} editing={editing} onEdit={() => setEditing(true)} /><ConfigInheritance initial={initial} scope={scope} onSave={onSave} editing={editing} onEdit={() => setEditing(true)}>
        <div className="scope-bar"><div><strong>配置范围</strong><span>{scope === 'global' ? '所有未覆盖项目使用这套企微配置' : `仅覆盖 ${projectName}`}</span></div><span aria-label="通知配置范围">{scope === 'global' ? '平台全局默认' : `项目配置 · ${projectName}`}</span></div>
        <div className="settings-layout"><div className="settings-main">
          <Card className="notification-draft-card" title={scope === 'global' ? '企业微信机器人' : `${projectName} · 企业微信覆盖`} subtitle="用于发送中文总评、阻断提醒和自动合并结果">
            <div className="form-grid"><Field label="群名称"><input autoComplete="off" value={form.name} onChange={event => update('name', event.target.value)} placeholder="例如：研发评审群" /></Field><Field label="Webhook 地址" hint="密钥仅在服务端保存"><input autoComplete="new-password" type="password" value={form.webhook_url} onChange={event => update('webhook_url', event.target.value)} placeholder="留空表示保留现有地址" /></Field><Field label="消息语言" hint="当前仅支持中文通知"><select autoComplete="off" disabled value={form.language}><option>中文</option></select></Field></div>
            <div className="notification-timings" role="group" aria-label="发送时机"><strong>发送时机（可多选）</strong><label><input type="checkbox" checked={form.notify_suggestions} onChange={event => update('notify_suggestions', event.target.checked)} />无阻断的评审完成</label><label><input type="checkbox" checked={form.notify_blockers} onChange={event => update('notify_blockers', event.target.checked)} />有阻断的评审完成</label><label><input type="checkbox" checked={form.notify_merge_result} onChange={event => update('notify_merge_result', event.target.checked)} />自动合并结果</label></div>
            <div className="connection-row"><div className={cn('connection-state', tested ? 'connection-ok' : 'connection-idle')}><span className="pulse-dot" />{tested ? '机器人连接正常 · 最近发送成功' : '尚未发送测试消息'}</div><div className="button-group"><Button variant="secondary" icon={<Save size={15} />} onClick={() => withValidWebhook(() => void save(testPayload))}>{scope === 'project' ? '保存项目企微配置' : '保存通知配置'}</Button><Button variant="secondary" icon={tested ? <CheckCircle2 size={15} /> : <Send size={15} />} onClick={() => withValidWebhook(() => onTest(testPayload))}>{testing ? '正在发送…' : tested ? '再次发送测试' : '发送测试消息'}</Button></div></div>
          </Card>
        </div><aside className="settings-side"><Card title="发送规则"><p className="side-note">三个时机互不排斥。评审完成时，根据是否存在未解决阻断选择其中一项；合并结果独立通知。</p><p className="side-note">项目可覆盖平台通知配置，机器人测试消息会直接发送至所填群聊。</p></Card></aside></div>
      </ConfigInheritance>
    </fieldset>
    {scope === 'project' && <Card title="真实通知内容预览" subtitle="基于当前项目最近一次已完成的真实评审；不会发送消息"><WecomPreview preview={preview} /><div className="preview-actions"><Button variant="ghost" disabled={!preview?.content} icon={<Copy size={14} />} onClick={() => { if (preview?.content) notifyClipboard(preview.content) }}>复制通知内容</Button><span>{preview?.review_id ? preview.will_send ? '按当前已保存发送时机将发送' : '按当前已保存发送时机将跳过' : '完成一次评审后显示真实内容'}</span></div></Card>}
    <Card title="最近发送记录" subtitle={scope === 'project' ? '仅当前项目 · 旧版未关联项目的记录保留在平台审计' : '全平台记录 · 可按项目过滤'}>
      {scope === 'global' && <label className="filter-label">项目 <select aria-label="按项目筛选通知" value={historyProject} onChange={event => setHistoryProject(event.target.value)}><option value="">全部项目</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>}
      {displayedAuditStatus === 'loading' ? <p role="status">正在加载发送记录…</p> : displayedAuditStatus === 'error' ? <div role="alert"><p>发送记录加载失败</p><Button variant="secondary" onClick={onReloadAudit}>重试加载记录</Button></div> : <ResizableTable tableId="notification-history" label="通知发送记录" columns={tableColumns.notificationHistory}><tbody>{historyRows.length ? historyRows.map(item => <tr key={item.key}><td><strong>{item.event}</strong></td><td className="muted-text">{item.time}</td><td>{item.response}</td><td><Badge tone={item.tone} dot>{item.result}</Badge></td><td>{item.tone === 'danger' && <span>{item.retryHint}</span>}</td></tr>) : <tr><td colSpan={5}><div className="empty-state"><Send size={22} /><strong>暂无发送记录</strong><span>真实评审完成并发送通知后会显示在这里。</span></div></td></tr>}</tbody></ResizableTable>}
    </Card>
  </>
}
function notifyClipboard(message: string) {
  void navigator.clipboard?.writeText(message)
}

export async function refreshNotificationAudit(
  refresh: () => Promise<Record<string, unknown>>,
  onRefresh: (value: Record<string, unknown>) => void,
): Promise<void> {
  try {
    onRefresh(await refresh())
  } catch {
    // A successful notification test should remain successful even if the
    // optional audit refresh is temporarily unavailable.
  }
}
function WecomPreview({ preview }: { preview: { review_id: string | null; content: string; will_send: boolean } | null }) {
  return <div className="wecom-preview"><div className="wecom-head"><div className="wecom-logo">企</div><div><strong>企业微信 Markdown</strong><span>{preview?.review_id ? '来自真实评审' : '等待评审结果'}</span></div><Badge tone={preview?.review_id ? 'info' : 'neutral'}>{preview?.review_id ? '已生成' : '未生成'}</Badge></div><div className="wecom-body">{preview?.content ? <pre className="wecom-content">{preview.content}</pre> : <p>当前项目尚无已完成的评审，暂无可预览的真实消息。</p>}</div></div>
}

export function GitLabConnectionCard({ onDirtyChange, config, tested, onTest, onSave }: { onDirtyChange?: (dirty: boolean) => void; config: BackendGitLabConfig | null; tested: boolean; onTest: (payload: Record<string, unknown>) => void; onSave: ConfigSave }) {
  const [form, setForm, resetForm] = useDraftState({ base_url: '', api_token: '', webhook_secret: '', enabled: true }, onDirtyChange)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const pending = useRef(false)
  const active = useRef(true)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  useEffect(() => {
    if (!config) return
    resetForm((current) => ({
      ...current,
      base_url: typeof config.base_url === 'string' ? config.base_url : current.base_url,
      enabled: config.enabled !== false,
    }))
  }, [config])
  const update = (key: keyof typeof form, value: string | boolean) => setForm((current) => ({ ...current, [key]: value }))
  const tokenStatus = config?.api_token_configured ? `已配置（${config.api_token_masked || '已掩码'}）` : '未配置页面 Token，将按环境变量兜底'
  const secretStatus = config?.webhook_secret_configured ? '已配置' : '未配置，将按项目或环境变量兜底'
  const save = async () => {
    if (pending.current) return
    pending.current = true; setSaving(true); setSaveError('')
    try {
      const result = await onSave({
      base_url: form.base_url,
      enabled: form.enabled,
      ...(form.api_token ? { api_token: form.api_token } : {}),
      ...(form.webhook_secret ? { webhook_secret: form.webhook_secret } : {}),
      })
      if (!active.current) return
      if (result === false) setSaveError('保存失败，输入内容已保留，请重试')
      else resetForm((current) => ({ ...current, api_token: '', webhook_secret: '' }))
    } catch { if (active.current) setSaveError('保存失败，输入内容已保留，请重试') }
    finally { pending.current = false; if (active.current) setSaving(false) }
  }
  const testPayload = { ...form, api_token: form.api_token || undefined, webhook_secret: form.webhook_secret || undefined }
  return <fieldset className="notification-form-boundary" disabled={saving} aria-busy={saving}><GitLabEffectiveSummary config={config} /><Card className="gitlab-draft-card" title="GitLab 连接配置" subtitle="页面保存优先于环境变量；空白密钥表示保留现有配置"><div className="form-grid"><Field label="GitLab 地址"><input aria-label="GitLab 地址" value={form.base_url} onChange={(event) => update('base_url', event.target.value)} placeholder="https://gitlab.example.com" /></Field><Field label="GitLab API Token" hint={tokenStatus}><input aria-label="GitLab API Token" type="password" value={form.api_token} onChange={(event) => update('api_token', event.target.value)} placeholder={config?.api_token_configured ? '留空保持现有 Token' : 'glpat-…'} /></Field><Field label="Webhook Secret" hint={secretStatus}><input aria-label="Webhook Secret" type="password" value={form.webhook_secret} onChange={(event) => update('webhook_secret', event.target.value)} placeholder={config?.webhook_secret_configured ? '留空保持现有 Secret' : '设置 GitLab Webhook Token'} /></Field><Field label="启用真实 GitLab"><Toggle label="启用真实 GitLab" checked={form.enabled} onChange={(value) => update('enabled', value)} /></Field></div><div className="connection-row"><div className={cn('connection-state', tested ? 'connection-ok' : 'connection-idle')}><span className="pulse-dot" />{tested ? 'GitLab 连接测试成功' : config?.api_token_configured ? '已配置页面连接，尚未测试' : '尚未配置页面连接'}</div><div className="button-group"><Button variant="secondary" icon={<Save size={15} />} onClick={() => void save()}>{saving ? '正在保存 GitLab 配置…' : '保存 GitLab 配置'}</Button><Button variant="secondary" icon={tested ? <CheckCircle2 size={15} /> : <Zap size={15} />} onClick={() => onTest(testPayload)}>{tested ? '重新测试连接' : '测试 GitLab 连接'}</Button></div></div><div className="side-note"><Lock size={14} />密钥只写入服务端数据库，接口只返回掩码和配置状态；环境变量仍可作为首次启动兜底。</div>{saveError && <p role="alert" className="form-error">{saveError}</p>}</Card></fieldset>
}

function SystemPage({ stats, onRetry }: { stats: Record<string, unknown> | null; onRetry: () => void }) {
  const queue = (stats?.queue || {}) as Record<string, number>
  const reviews = (stats?.reviews || {}) as Record<string, number>
  const runtime = (stats?.runtime || {}) as Record<string, number>
  const running = queue.running || 0
  const queued = queue.queued || 0
  const failed = queue.failed || 0
  const completed = queue.completed_today || reviews.completed_today || 0
  const remoteAudits = Array.isArray(stats?.audit_logs) ? stats.audit_logs as Array<Record<string, unknown>> : []
  const auditLogs = remoteAudits.map((log) => ({ actor: typeof log.actor === 'string' ? log.actor : 'system', action: typeof log.action === 'string' ? log.action : '平台操作', target: typeof log.target === 'string' ? log.target : '', time: typeof log.created_at === 'string' ? new Date(log.created_at).toLocaleString('zh-CN') : '时间未记录' }))
  const webhookAudits = remoteAudits.filter((log) => log.action === 'Webhook 触发评审')
  const webhookHistory = webhookAudits.length ? webhookAudits.slice(0, 6).map((log, index) => {
    const target = typeof log.target === 'string' ? log.target : 'GitLab Webhook'
    const metadata = typeof log.metadata === 'object' && log.metadata !== null ? log.metadata as Record<string, unknown> : {}
    return {
      project: target,
      event: typeof metadata.source === 'string' ? metadata.source : 'merge_request',
      time: typeof log.created_at === 'string' ? new Date(log.created_at).toLocaleString('zh-CN') : '时间未记录',
      result: '已接收',
      tone: 'success' as const,
      key: `webhook-${index}-${String(log.created_at || '')}`,
    }
  }) : []
  return <><div className="metrics-grid system-metrics"><MetricCard label="Webhook 健康度" value={stats?.webhooks_received ? '有接收记录' : '尚无接收记录'} detail={stats ? `${stats.webhooks_received || 0} 个事件已记录` : '等待后端数据'} icon={<Webhook size={20} />} tone="green" /><MetricCard label="队列任务" value={`${running + queued}`} detail={`${running} 个运行中 · ${queued} 个等待`} icon={<Activity size={20} />} tone="blue" /><MetricCard label="失败待重试" value={`${failed}`} detail={failed ? '需要及时处理' : '暂无失败任务'} icon={<RefreshCw size={20} />} tone="orange" /><MetricCard label="今日完成" value={`${completed}`} detail={`${reviews.completed_today || 0} 条评审完成`} icon={<Zap size={20} />} tone="purple" /></div><div className="settings-layout system-layout"><div className="settings-main"><Card title="Webhook 健康" subtitle="最近接收到的 GitLab 事件"><div className="webhook-list" id="system-webhook-logs">{webhookHistory.length ? webhookHistory.map((item) => <div className="webhook-row" key={item.key}><div className={cn('webhook-status', item.tone)}><Webhook size={15} /></div><div className="webhook-copy"><strong>{item.project}</strong><span>{item.event} · {item.time}</span></div><Badge tone={item.tone === 'success' ? 'success' : 'danger'} dot>{item.result}</Badge><ChevronRight size={16} className="muted-icon" /></div>) : <EmptyLine>暂无 Webhook 事件</EmptyLine>}</div><Button variant="soft" className="full-button" onClick={() => document.getElementById('system-webhook-logs')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>定位当前 Webhook 记录 <ArrowUpRight size={14} /></Button></Card><Card title="任务队列与重试" subtitle="失败任务会自动指数退避重试，达到上限后可手动重新排队"><div className="queue-overview"><div><span>运行中</span><strong className="blue-text">{running}</strong></div><div><span>等待中</span><strong>{queued}</strong></div><div><span>失败</span><strong className="danger-text">{failed}</strong></div><div><span>今日完成</span><strong className="success-text">{completed}</strong></div></div><div className="queue-item"><div className="queue-icon danger"><Bug size={16} /></div><div><strong>任务队列</strong><span>{failed ? '存在失败任务，可手动重试' : '当前没有失败任务'}</span></div><Badge tone={failed ? 'danger' : 'success'}>{failed ? '待重试' : '正常'}</Badge><Button variant="soft" icon={<RefreshCw size={14} />} onClick={onRetry}>立即重试</Button></div></Card></div><aside className="settings-side"><Card title="运行时默认值"><div className="runtime-values"><div><span>GitLab 请求超时</span><strong>{runtime.gitlab_timeout_s != null ? `${runtime.gitlab_timeout_s}s` : '—'}</strong></div><div><span>模型请求超时</span><strong>{runtime.model_timeout_s != null ? `${runtime.model_timeout_s}s` : '—'}</strong></div><div><span>企业微信超时</span><strong>{runtime.wecom_timeout_s != null ? `${runtime.wecom_timeout_s}s` : '—'}</strong></div><div><span>最大并发评审</span><strong>{runtime.max_concurrency != null ? `${runtime.max_concurrency}` : '—'}</strong></div><div><span>失败重试上限</span><strong>{runtime.max_retries != null ? `${runtime.max_retries} 次` : '—'}</strong></div></div><div className="side-note"><CircleDot size={14} />页面配置优先，实际运行时值来自后端配置；修改环境变量后需重启 worker。</div></Card><Card title="操作审计" subtitle="谁在什么时候改变了什么"><div className="audit-list" id="system-audit-logs">{auditLogs.length ? auditLogs.map((log) => <div className="audit-item" key={`${log.actor}-${log.time}-${log.action}`}><div className="audit-avatar">{log.actor === 'system' ? <Zap size={13} /> : log.actor.slice(0, 1)}</div><div><strong>{log.action}</strong><span>{log.target}</span><small>{log.actor} · {log.time}</small></div></div>) : <EmptyLine>暂无操作审计记录</EmptyLine>}</div><Button variant="ghost" className="full-button" onClick={() => document.getElementById('system-audit-logs')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>定位当前操作审计 <ArrowUpRight size={14} /></Button></Card></aside></div></>
}
export function MergeGatePage({
  dryRun, dryRunPassed, enabled, selectedReviewId, selectedReview, reviews, assessment, action, error,
  projectName, onSelectReview, onDryRun, onEnable, onDisable, onOpenDetail,
  automationEnabled = true, projectEnabled = true, onOpenPolicy,
}: {
  dryRun: boolean
  dryRunPassed: boolean
  enabled: boolean
  selectedReviewId: string | null
  selectedReview: Review | null
  reviews: Review[]
  assessment: GateAssessment | null
  action: 'dry-run' | 'toggle' | null
  error: string
  projectName: string
  onSelectReview: (reviewId: string) => void
  onDryRun: () => void
  onEnable: () => void
  onDisable: () => void
  onOpenDetail: () => void
  automationEnabled?: boolean
  projectEnabled?: boolean
  onOpenPolicy?: () => void
}) {
  const [showCheck, setShowCheck] = useState(false)
  const check = (key: string, fallback: boolean) => assessment?.checks?.[key] === true || (!assessment ? fallback : false)
  const reviewReady = check('review_ready', Boolean(selectedReview && ['已完成', '有阻断'].includes(selectedReview.status)))
  const unresolvedBlockers = assessment?.blocking_count ?? selectedReview?.blockers ?? 0
  const blockersReady = check('blockers_resolved', reviewReady && unresolvedBlockers === 0)
  const summaryReady = check('summary_ready', false)
  const shaReady = check('sha_matches', false)
  const gitlabReady = check('gitlab_ready', false)
  const busy = action !== null
  return <div className="settings-layout project-auto-merge-layout">
    <div className="settings-main">
      <Card title="项目自动合并策略" subtitle="开启一次，持续作用于当前项目后续完成完整评审的 MR">
        <div className="project-auto-merge-scope"><Folder size={14} /><span>{projectName}</span><Badge tone="neutral">项目级 · 持续生效</Badge></div>
        <div className="gate-switch-row project-auto-merge-switch">
          <div className="gate-switch-copy"><div className="setting-icon green"><GitMerge size={18} /></div><div><strong>项目自动合并</strong><span>{enabled ? '已开启 · 持续处理后续所有 MR，直到手动关闭。' : '已关闭 · 开启后，每条 MR 满足合并条件时自动合并。'}</span></div></div>
          <Toggle label="项目自动合并" checked={enabled} disabled={busy} onChange={value => value ? onEnable() : onDisable()} />
        </div>
        {action === 'toggle' && <p role="status" className="project-auto-merge-saving">正在保存项目开关…</p>}
        {error && <div className="form-error" role="alert"><AlertTriangle size={14} />{error}</div>}
        <p className="project-auto-merge-note">开关保存在项目中，刷新页面、切换 MR 或某条 MR 未通过检查，都不会关闭它。每次合并前由系统自动执行以下检查。</p>
        <div className="gate-rule-list project-auto-merge-rules">
          <div className="gate-rule"><FileCheck size={17} /><div><strong>完整评审成功完成</strong><span>按项目当前检查要求评审，只有生成建议不触发合并。</span></div></div>
          <div className="gate-rule"><ShieldCheck size={17} /><div><strong>没有未解决的阻断问题</strong><span>按项目配置的阻断门槛判断；问题修复后复审可继续合并。</span></div></div>
          <div className="gate-rule"><MessageSquare size={17} /><div><strong>中文总评已同步到 GitLab</strong><span>同步失败时保留该 MR，后续 MR 仍按项目策略处理。</span></div></div>
          <div className="gate-rule"><GitMerge size={17} /><div><strong>当前提交一致且 GitLab 允许合并</strong><span>检查最新 SHA、草稿、冲突和 GitLab 状态，再提交合并。</span></div></div>
        </div>
        {!projectEnabled && <div className="project-auto-merge-warning"><AlertTriangle size={16} /><span>项目已暂停，系统不会执行自动合并；恢复项目后仍沿用已保存的开关。</span></div>}
        {!automationEnabled && <div className="project-auto-merge-warning"><Webhook size={16} /><span>自动评审未开启：当前仅对手动触发的完整评审执行自动合并。</span>{onOpenPolicy && <Button variant="ghost" onClick={onOpenPolicy}>配置自动评审</Button>}</div>}
      </Card>
      <div className="project-auto-merge-preview">
        <button className="project-auto-merge-disclosure" type="button" aria-expanded={showCheck} aria-controls="project-mr-check" onClick={() => setShowCheck(value => !value)}><span><Search size={17} />检查某个 MR（可选）</span><ChevronDown size={17} className={showCheck ? 'expanded' : ''} /></button>
        <p>只预览这条 MR 的合并条件，不执行合并，也不改变项目开关。</p>
      </div>
      {showCheck && <section id="project-mr-check" aria-label="单个 MR 检查预览">
      <Card title="选择要检查的 MR" subtitle="该检查结果只属于所选评审及其提交">
        <label className="field gate-target-field"><span className="field-label">选择评审记录<em>*</em></span><select aria-label="目标评审" value={selectedReviewId || ''} onChange={(event) => onSelectReview(event.target.value)} disabled={busy}>
          <option value="">请选择目标评审</option>
          {reviews.map(review => <option key={review.id} value={review.id}>{review.mr} · {review.project} · {review.status}</option>)}
        </select><small>切换评审会清除旧检查结果；项目自动合并开关保持不变。</small></label>
        {selectedReview ? <div className="gate-target-meta"><div><strong>目标 MR {selectedReview.mr}</strong><span>{selectedReview.branch} · {selectedReview.source}</span></div><Button variant="ghost" onClick={onOpenDetail}>查看行级评论 <ArrowUpRight size={14} /></Button></div> : <div className="gate-target-empty">请先选择一条当前项目的评审记录。</div>}
        {assessment && <div className="gate-sha-meta"><span>评审 SHA <code>{assessment.reviewed_sha || '未记录'}</code></span><span>当前 SHA <code>{assessment.current_sha || '未读取'}</code></span></div>}
      </Card>
      <Card title="所选 MR 的检查结果" subtitle="仅检查此 MR，系统在实际合并前仍会重新核验">
        <div className="gate-rule-list">
          <div className="gate-rule"><CheckCircle2 size={17} className={reviewReady ? 'success-text' : 'muted-icon'} /><div><strong>评审任务执行完成</strong><span>{reviewReady ? '当前选择的评审已完成，可继续校验。' : '请选择一条已完成或有阻断的评审记录。'}</span></div><Badge tone={reviewReady ? 'success' : 'neutral'}>{reviewReady ? '通过' : '待评审'}</Badge></div>
          <div className="gate-rule"><AlertTriangle size={17} className={blockersReady ? 'success-text' : 'danger-text'} /><div><strong>当前门槛内的行级评论已解决</strong><span>{reviewReady ? unresolvedBlockers ? `当前还有 ${unresolvedBlockers} 个阻断评论，必须逐条处理。` : '当前门槛内没有未解决的阻断评论。' : '完成评审后才会计算阻断评论。'}</span></div><Badge tone={!reviewReady ? 'neutral' : blockersReady ? 'success' : 'danger'}>{!reviewReady ? '待评审' : blockersReady ? '通过' : '未通过'}</Badge><Button variant="ghost" onClick={onOpenDetail}>查看评论 <ArrowUpRight size={14} /></Button></div>
          <div className="gate-rule"><CheckCircle2 size={17} className={summaryReady ? 'success-text' : 'muted-icon'} /><div><strong>中文总评已同步</strong><span>{summaryReady ? '总评已同步到 GitLab，结果可用于门禁。' : '等待当前评审的中文总评同步完成。'}</span></div><Badge tone={summaryReady ? 'success' : 'neutral'}>{summaryReady ? '通过' : '待生成'}</Badge></div>
          <div className="gate-rule"><CheckCircle2 size={17} className={shaReady && gitlabReady ? 'success-text' : 'muted-icon'} /><div><strong>提交与 GitLab 状态匹配</strong><span>{shaReady && gitlabReady ? '当前 SHA 与评审一致，GitLab 报告可合并。' : '执行检查会重新读取当前 SHA、草稿和合并状态。'}</span></div><Badge tone={shaReady && gitlabReady ? 'success' : 'neutral'}>{shaReady && gitlabReady ? '通过' : '待校验'}</Badge></div>
        </div>
      </Card>
      <Card title="检查预览" subtitle="无需手动检查也能开启项目策略；系统会为每条后续 MR 自动检查">
        <div className={cn('dry-run-panel', dryRun ? dryRunPassed ? 'dry-run-done' : 'dry-run-failed' : 'dry-run-idle')}><div className="dry-run-icon">{dryRun ? dryRunPassed ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} /> : <Play size={22} />}</div><div><strong>{dryRun ? dryRunPassed ? '检查通过' : '检查未通过' : '尚未检查'}</strong><span>{dryRun ? assessment?.reason || `${projectName} 的 ${selectedReview?.mr || '所选 MR'} 已完成检查；项目开关保持不变。` : '选择一条评审记录，预览是否满足合并条件。'}</span></div><Button variant={dryRun ? 'secondary' : 'primary'} disabled={!selectedReview || busy} onClick={onDryRun} icon={<Play size={14} />}>{action === 'dry-run' ? '检查中…' : dryRun ? '重新检查' : '执行检查'}</Button></div>
      </Card>
      </section>}
    </div>
    <aside className="settings-side"><Card title="项目运行状态"><div className={cn('gate-status-card', enabled && 'enabled')}><div className="gate-status-icon">{enabled ? <CheckCircle2 size={22} /> : <Lock size={22} />}</div><strong>{enabled ? '项目自动合并已开启' : '项目自动合并已关闭'}</strong><span>{enabled ? '持续作用于后续 MR，无需逐条开启。' : '开启项目开关后持续生效。'}</span></div><p className="project-auto-merge-note">接入 GitLab Webhook 并开启自动评审后，MR 创建或 push 会自动进入评审及合并检查。</p></Card><Card title="阻断与失败处理"><div className="fallback-note"><div className="setting-icon orange"><AlertTriangle size={16} /></div><div><strong>只影响对应 MR，项目开关保持开启</strong><span>有阻断或检查失败时，此 MR 等待处理；后续 MR 继续独立检查。合并响应不确定时只核对状态，不重复提交。</span></div></div><div className="side-note"><Bell size={14} />合并结果按项目通知配置投递。</div></Card></aside>
  </div>
}

function Field({ label, hint, required, children }: { label: string; hint?: string; required?: boolean; children: ReactNode }) { return <label className="field"><span className="field-label">{label}{required && <em>*</em>}</span>{children}{hint && <small>{hint}</small>}</label> }

export default App





export function diffForFile(diff: string, path?: string): string {
  if (!path || !diff.includes('diff --git ')) return diff
  return diff.split(/(?=^diff --git )/m).filter(block => {
    const header = block.split('\n')[0]
    return header.endsWith(' b/' + path) || header.endsWith(' b/' + path + '\r')
  }).join('')
}
