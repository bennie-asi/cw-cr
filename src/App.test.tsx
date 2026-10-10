import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { normalizeGateAssessment, OverviewPage, refreshNotificationAudit } from './App'
import App from './test-app'

beforeEach(() => { vi.spyOn(api, 'checkProjectConnection').mockResolvedValue({ webhook_url: '/api/webhooks/gitlab/', repositories: [{ path: 'team/api', connection: { state: 'unconfigured', detail: '待配置连接' }, webhook: { state: 'unknown', detail: '尚未核对' } }] }) })
afterEach(() => vi.restoreAllMocks())

describe('智审台 shell', () => {
  it('treats an empty persisted gate assessment as no assessment', () => {
    expect(normalizeGateAssessment({})).toBeNull()
    expect(normalizeGateAssessment(null)).toBeNull()
  })

  it('renders the platform title and overview navigation', () => {
    render(<App />)

    expect(screen.getByText('团队智能代码审查平台')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '总览' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '需要关注的严重问题' })).toBeInTheDocument()
  })

  it('navigates to project management and creates a project from the drawer', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '项目管理' }))
    expect(screen.getByRole('heading', { name: '项目管理', level: 1 })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '添加项目' }))
    await user.type(screen.getByPlaceholderText('例如 demo-api'), 'cw-cr-demo')
    await user.type(screen.getByPlaceholderText('example-team/project'), 'rd/demo/cw-cr-demo')
    await user.click(screen.getByRole('button', { name: '下一步：连接检查' }))
    await screen.findByText('待配置连接')
    await user.click(screen.getByRole('button', { name: '下一步：确认创建' }))
    await user.click(screen.getByRole('button', { name: '创建项目' }))
    await screen.findByRole('heading', { name: '项目已创建' })
    await user.click(screen.getByRole('button', { name: '完成' }))

    expect(screen.getByText('cw-cr-demo')).toBeInTheDocument()
    expect(screen.getByText('项目 cw-cr-demo 已接入评审平台')).toBeInTheDocument()
  })

  it('shows the GitLab webhook endpoint on project management', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '项目管理' }))

    expect(screen.getByText(/\/api\/webhooks\/gitlab\//)).toBeInTheDocument()
  })

  it('adds multiple repositories and can edit then pause the project', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '项目管理' }))
    await user.click(screen.getByRole('button', { name: '添加项目' }))
    await user.type(screen.getByPlaceholderText('例如 demo-api'), 'team-review')
    await user.type(screen.getByRole('textbox', { name: 'GitLab 项目路径 1' }), 'team/api')
    await user.click(screen.getByRole('button', { name: '添加仓库' }))
    await user.type(screen.getByRole('textbox', { name: 'GitLab 项目路径 2' }), 'team/ui')
    await user.click(screen.getByRole('button', { name: '下一步：连接检查' }))
    await screen.findByText('待配置连接')
    await user.click(screen.getByRole('button', { name: '下一步：确认创建' }))
    await user.click(screen.getByRole('checkbox', { name: /继承平台通知/ }))
    await user.click(screen.getByRole('button', { name: '创建项目' }))
    await screen.findByRole('heading', { name: '项目已创建' })
    await user.click(screen.getByRole('button', { name: '完成' }))
    expect(screen.getByText('team/api · team/ui')).toBeInTheDocument()
    await user.click(screen.getByLabelText('team-review 更多操作'))
    await user.click(screen.getByRole('button', { name: '编辑项目与仓库' }))
    const name = screen.getByPlaceholderText('例如 demo-api')
    await user.clear(name)
    await user.type(name, 'team-review-updated')
    await user.click(screen.getByRole('button', { name: '保存项目' }))
    expect(screen.getByText('team-review-updated')).toBeInTheDocument()
    await user.click(screen.getByLabelText('team-review-updated 更多操作'))
    await user.click(screen.getByRole('button', { name: '暂停项目' }))
    expect(screen.getAllByText('已暂停').length).toBeGreaterThan(0)
  })

  it('filters review history by project', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '评审历史' }))
    await user.selectOptions(screen.getByRole('combobox', { name: '按项目筛选评审' }), 'demo-web')
    expect(screen.getByText('!356 · demo-web')).toBeInTheDocument()
    expect(screen.queryByText('!357 · demo-api')).not.toBeInTheDocument()
  })

  it('shows exact daily severe issue counts on the quality overview', () => {
    render(<OverviewPage stats={{ daily_severe_issues: [0, 2], daily_labels: ['09-23', '09-24'] }} reviews={[]} projects={[]} unresolvedBlockers={0} onNavigate={() => {}} onOpenReview={() => {}} />)
    expect(screen.getByRole('img', { name: '近 7 天每日新发现严重问题：0、2' })).toBeInTheDocument()
  })

  it('publishes a pending line comment from the MR detail page', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '评审历史' }))
    await user.click(screen.getByText('!357 · demo-api'))
    await user.click(screen.getAllByRole('button', { name: '发布到 GitLab' })[0])

    expect(screen.getAllByText('已同步到 GitLab').length).toBeGreaterThan(0)
    expect(screen.getByText('行级评论已发布到 GitLab Discussion')).toBeInTheDocument()
  })

  it('shows ungraded findings as explicit merge-gate blockers', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '评审历史' }))
    await user.click(screen.getByText('!357 · demo-api'))
    await user.click(screen.getByRole('button', { name: /行级评论/ }))

    expect(screen.getByText('1 个未评级（门禁阻断）')).toBeInTheDocument()
    expect(screen.getByText('UNRATED')).toBeInTheDocument()
  })

  it('switches the active changed file in the MR diff view', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '评审历史' }))
    await user.click(screen.getByText('!357 · demo-api'))
    await user.click(screen.getByRole('button', { name: /src\/service\/webhook\.ts/ }))

    expect(screen.getByRole('heading', { name: 'src/service/webhook.ts', level: 2 })).toBeInTheDocument()
  })

  it('opens a finding beside its changed code', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '评审历史' }))
    await user.click(screen.getByText('!357 · demo-api'))
    await user.click(screen.getByRole('button', { name: /缺少幂等键校验/ }))
    expect(screen.getByRole('heading', { name: 'src/service/webhook.ts', level: 2 })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '问题说明' })).toHaveTextContent('缺少幂等键校验')
  })

  it('saves the project policy and tests the model gateway', async () => {
    const user = userEvent.setup()
    render(<App />)

    if (!screen.queryByRole('button', {name:'返回平台'})) { await user.click(screen.getByLabelText('切换项目')); await user.click(screen.getByRole('button', {name:'demo-api'})) }
    await user.click(screen.getByRole('button', { name: '项目策略' }))
    await user.click(screen.getByRole('button', { name: '保存项目策略' }))
    expect(screen.getByText('项目策略已保存')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '模型与引擎' }))
    await user.click(screen.getByRole('button', { name: '测试连接' }))
    expect(screen.getByText('模型配置已读取，模型列表已同步')).toBeInTheDocument()
    expect(screen.getByText('网关已连通 · 未返回模型目录，可手动填写模型 ID')).toBeInTheDocument()
  })

  it('persists the edited Chinese summary prompt through the policy save action', async () => {
    const user = userEvent.setup()
    render(<App />)

    if (!screen.queryByRole('button', {name:'返回平台'})) { await user.click(screen.getByLabelText('切换项目')); await user.click(screen.getByRole('button', {name:'demo-api'})) }
    await user.click(screen.getByRole('button', { name: '项目策略' }))
    await user.click(screen.getByRole('button', { name: '高级设置' }))
    const prompt = screen.getByRole('textbox', { name: '中文总评提示词' })
    await user.clear(prompt)
    await user.type(prompt, '请用中文输出风险清单')
    await user.click(screen.getByRole('button', { name: '保存项目策略' }))

    expect(screen.getByText('项目策略已保存')).toBeInTheDocument()
  })

  it('keeps the project switch enabled when an optional MR check fails', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByLabelText('切换项目')); await user.click(screen.getByRole('button', {name:'demo-api'}))
    await user.click(screen.getByRole('button', { name: '自动合并门禁' }))
    const toggle = screen.getByRole('switch', { name: '项目自动合并' })
    await user.click(toggle)
    expect(toggle).toBeChecked()
    await user.click(screen.getByRole('button', { name: '检查某个 MR（可选）' }))
    const target = screen.getByRole('combobox', { name: '目标评审' })
    await user.selectOptions(target, 'review-357')
    expect(screen.getByText('目标 MR !357')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '执行检查' }))

    expect(screen.getByText('检查未通过')).toBeInTheDocument()
    expect(toggle).toBeChecked()
  })

  it('clears a dry-run result when the target review changes', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByLabelText('切换项目')); await user.click(screen.getByRole('button', {name:'demo-api'}))
    await user.click(screen.getByRole('button', { name: '自动合并门禁' }))
    await user.click(screen.getByRole('button', { name: '检查某个 MR（可选）' }))
    const target = screen.getByRole('combobox', { name: '目标评审' })
    await user.selectOptions(target, 'review-357')
    await user.click(screen.getByRole('button', { name: '执行检查' }))
    expect(screen.getByText('检查未通过')).toBeInTheDocument()
    await user.selectOptions(target, '')
    expect(screen.getByText('尚未检查')).toBeInTheDocument()
  })

  it('opens the selected review instead of always showing the demo MR', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '评审历史' }))
    await user.click(screen.getByText('!356 · demo-web'))

    expect(screen.getByRole('heading', { name: '!356' })).toBeInTheDocument()
    expect(screen.getAllByText(/demo-web/).length).toBeGreaterThan(0)
  })

  it('opens a review selected from the overview table', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByText('!357 · demo-api'))

    expect(screen.getByRole('heading', { name: '!357' })).toBeInTheDocument()
  })

  it('offers a persisted save action for model configuration', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '模型与引擎' }))
    await user.click(screen.getByRole('button', { name: '保存模型配置' }))

    expect(screen.getByText('模型配置已保存')).toBeInTheDocument()
  })

  it('shows page-managed GitLab connection settings in system settings', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '系统设置' }))
    expect(screen.getByText('GitLab 连接配置')).toBeInTheDocument()
    await user.type(screen.getByLabelText('GitLab 地址'), 'https://gitlab.example.com')
    await user.type(screen.getByLabelText('GitLab API Token'), 'glpat-page-secret')
    await user.click(screen.getByRole('button', { name: '保存 GitLab 配置' }))

    expect(screen.getByText('GitLab 连接配置已保存')).toBeInTheDocument()
    expect(screen.getByText(/密钥只写入服务端/)).toBeInTheDocument()
  })

  it('shows whether CCSwitch uses page configuration or environment fallback', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '模型与引擎' }))

    expect(screen.getByText(/配置来源：/)).toBeInTheDocument()
    expect(screen.getByText(/环境变量兜底/)).toBeInTheDocument()
  })

  it('allows entering a custom model id when CCSwitch has not returned a catalog', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '模型与引擎' }))
    const modelId = screen.getByLabelText('模型 ID')
    await user.clear(modelId)
    await user.type(modelId, 'company-review-model')

    expect(modelId).toHaveValue('company-review-model')
  })

  it('allows saving a project-specific model override', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '模型与引擎' }))
    await user.click(screen.getByLabelText('切换项目')); await user.click(screen.getByRole('button', {name:'demo-api'}))
    await user.click(screen.getByRole('button', { name: '保存项目模型配置' }))

    expect(screen.getByText('项目模型配置已保存')).toBeInTheDocument()
  })

  it('keeps model connection and engine runtime configuration separate', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '模型与引擎' }))
    expect(screen.queryByLabelText('审查引擎')).not.toBeInTheDocument()
    expect(screen.getByLabelText('平台默认评审引擎')).toHaveValue('builtin')
    expect(screen.getByRole('option', { name: 'Pi Agent（待验证）' })).toBeDisabled()
    expect(screen.getByRole('option', { name: 'PR-Agent（待验证）' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '保存模型配置' }))

    expect(screen.getByText('模型配置已保存')).toBeInTheDocument()
  })

  it('keeps the selected project as the policy configuration context', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '项目管理' }))
    await user.click(screen.getByText('demo-web'))
    if (!screen.queryByRole('button', {name:'返回平台'})) { await user.click(screen.getByLabelText('切换项目')); await user.click(screen.getByRole('button', {name:'demo-api'})) }
    await user.click(screen.getByRole('button', { name: '项目策略' }))

    expect(screen.getByText(/当前项目：demo-web/)).toBeInTheDocument()
  })

  it('allows saving a project-specific notification override', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '通知配置' }))
    await user.click(screen.getByLabelText('切换项目')); await user.click(screen.getByRole('button', {name:'demo-api'}))
    await user.click(screen.getByRole('button', { name: '保存项目企微配置' }))

    expect(screen.getByText('项目企微配置已保存')).toBeInTheDocument()
  })

  it('allows independent notification timing choices', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '通知配置' }))
    await user.click(screen.getByRole('checkbox', { name: '无阻断的评审完成' }))
    expect(screen.getByRole('checkbox', { name: '无阻断的评审完成' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: '有阻断的评审完成' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '自动合并结果' })).toBeChecked()
  })

  it('explains that page configuration is preferred over environment defaults', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '系统设置' }))
    await user.click(screen.getByRole('button', { name: '运行与审计' }))

    expect(screen.getByText(/页面配置优先/)).toBeInTheDocument()
  })

  it('refreshes notification audit data after a successful test send', async () => {
    let auditState: Record<string, unknown> | null = null

    await refreshNotificationAudit(
      async () => ({ audit_logs: [{ action: '测试企业微信通知', metadata: { ok: true } }] }),
      (value) => { auditState = value },
    )

    expect(auditState).toEqual({ audit_logs: [{ action: '测试企业微信通知', metadata: { ok: true } }] })
  })
})
