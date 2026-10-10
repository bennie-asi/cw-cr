import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import ActualApp from './App'
import { api } from './api'
import { PROJECTS, REVIEWS } from './test-fixtures'
import { DEFAULT_POLICY } from './test-fixtures'
import { ProjectReviewPolicy } from './ProjectReviewPolicy'

const base = { security_checks: true, reliability_checks: true, style_checks: true, block_on_p1: true,
  block_on_p2: false, summary_prompt: '项目通用要求', security_prompt: '', reliability_prompt: '', style_prompt: '',
  effective_prompts: { security_prompt: '平台安全', reliability_prompt: '平台可靠', style_prompt: '平台风格' } }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })
function setup(showApp = true) {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', `/projects/${PROJECTS[0].id}/settings/policy`)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'listReviews').mockResolvedValue([])
  for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig', 'projectModelConfig', 'projectNotificationConfig', 'mergeGate'] as const) vi.spyOn(api, name).mockResolvedValue({})
  vi.spyOn(api, 'getPolicy').mockResolvedValue(base)
  vi.spyOn(api, 'projectSync').mockResolvedValue({ enabled: false, interval_seconds: 60,
    activated_at: null, next_sync_at: null, last_sync_at: null, last_success_at: null,
    sync_state: 'idle', last_error: '', last_result: {}, consecutive_failures: 0, baseline_mrs: 0, in_progress: false })
  const save = vi.spyOn(api, 'updatePolicy').mockImplementation(async (_id, payload) => ({ ...base, ...payload }))
  if (showApp) render(<App />)
  return { user: userEvent.setup(), save }
}

it('edits three project prompts independently and saves their actual API fields', async () => {
  const { user, save } = setup()
  expect(await screen.findByRole('textbox', { name: '平台安全性提示词' })).toHaveValue('平台安全')
  expect(screen.getByRole('textbox', { name: '平台安全性提示词' })).toHaveAttribute('readonly')
  for (const [tab, value] of [['安全性', '项目安全检查'], ['可靠性', '项目可靠性检查'], ['代码风格', '项目风格检查']]) {
    await user.click(screen.getByRole('tab', { name: tab }))
    await user.click(screen.getByRole('radio', { name: '自定义本项目' }))
    await user.clear(screen.getByRole('textbox', { name: `项目${tab}提示词` }))
    await user.type(screen.getByRole('textbox', { name: `项目${tab}提示词` }), value)
  }
  await user.click(screen.getByRole('tab', { name: '安全性' }))
  expect(screen.getByRole('textbox', { name: '项目安全性提示词' })).toHaveValue('项目安全检查')
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(PROJECTS[0].id, expect.objectContaining({
    security_prompt: '项目安全检查', reliability_prompt: '项目可靠性检查', style_prompt: '项目风格检查',
  })))
})

it.each([['仅 P0 · 宽松', false, false], ['P0 / P1 · 标准', true, false], ['P0 / P1 / P2 · 严格', true, true]] as const)('maps %s to the saved project threshold', async (label, p1, p2) => {
  const { user, save } = setup()
  await user.click(await screen.findByRole('radio', { name: new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }))
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(PROJECTS[0].id, expect.objectContaining({ block_on_p1: p1, block_on_p2: p2 })))
})

it('restores one inherited prompt while retaining the other project override', async () => {
  const { user, save } = setup()
  await user.click(await screen.findByRole('radio', { name: '自定义本项目' }))
  await user.type(screen.getByRole('textbox', { name: '项目安全性提示词' }), '补充安全')
  await user.click(screen.getByRole('tab', { name: '可靠性' }))
  await user.click(screen.getByRole('radio', { name: '自定义本项目' }))
  await user.type(screen.getByRole('textbox', { name: '项目可靠性提示词' }), '补充可靠')
  await user.click(screen.getByRole('tab', { name: '安全性' }))
  await user.click(screen.getByRole('radio', { name: '继承平台提示词' }))
  expect(screen.getByRole('textbox', { name: '平台安全性提示词' })).toHaveValue('平台安全')
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(PROJECTS[0].id, expect.objectContaining({ security_prompt: '', reliability_prompt: '平台可靠补充可靠' })))
})

it('rejects blank custom prompt before calling save and preserves the draft', async () => {
  const { user, save } = setup()
  await user.click(await screen.findByRole('radio', { name: '自定义本项目' }))
  await user.clear(screen.getByRole('textbox', { name: '项目安全性提示词' }))
  expect(screen.getByRole('alert')).toHaveTextContent('安全性提示词不能为空')
  expect(screen.getByRole('button', { name: '保存项目策略' })).toBeDisabled()
  expect(save).not.toHaveBeenCalled()
  expect(screen.getByRole('radio', { name: '自定义本项目' })).toBeChecked()
})

it('retains failed custom draft and allows a retry', async () => {
  const { user, save } = setup()
  save.mockRejectedValueOnce(new Error('项目策略保存失败'))
  await user.click(await screen.findByRole('radio', { name: '自定义本项目' }))
  await user.type(screen.getByRole('textbox', { name: '项目安全性提示词' }), '失败草稿')
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  await screen.findByText(/保存失败/)
  expect(screen.getByRole('textbox', { name: '项目安全性提示词' })).toHaveValue('平台安全失败草稿')
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
})

it('hydrates saved project overrides and shows actual platform defaults when restoring inheritance', async () => {
  const onChange = vi.fn(), validity = vi.fn(), user = userEvent.setup()
  const view = render(<ProjectReviewPolicy policy={{ ...DEFAULT_POLICY, effectivePrompts: {} }} onChange={onChange} onValidityChange={validity} saving={false} readOnly={false} />)
  view.rerender(<ProjectReviewPolicy policy={{ ...DEFAULT_POLICY, securityPrompt: '已保存项目安全',
    effectivePrompts: { security_prompt: '已保存项目安全' }, platformPrompts: { security_prompt: '最新平台安全' },
  }} onChange={onChange} onValidityChange={validity} saving={false} readOnly={false} />)
  expect(screen.getByRole('radio', { name: '自定义本项目' })).toBeChecked()
  expect(screen.getByRole('textbox', { name: '项目安全性提示词' })).toHaveValue('已保存项目安全')
  await user.click(screen.getByRole('radio', { name: '继承平台提示词' }))
  expect(screen.getByRole('textbox', { name: '平台安全性提示词' })).toHaveValue('最新平台安全')
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ securityPrompt: '' }))
})

it('lets read-only members inspect every check but prevents configuration writes', async () => {
  const onChange = vi.fn(), user = userEvent.setup()
  render(<ProjectReviewPolicy policy={{ ...DEFAULT_POLICY, effectivePrompts: base.effective_prompts }} onChange={onChange} onValidityChange={vi.fn()} saving={false} readOnly />)
  await user.click(screen.getByRole('tab', { name: '可靠性' }))
  expect(screen.getByRole('textbox', { name: '平台可靠性提示词' })).toHaveValue('平台可靠')
  expect(screen.getByRole('radio', { name: '自定义本项目' })).toBeDisabled()
  expect(screen.getByRole('checkbox', { name: '启用可靠性检查' })).toBeDisabled()
  expect(onChange).not.toHaveBeenCalled()
})

it('keeps project check tabs usable in the complete read-only application page', async () => {
  const { user } = setup(false)
  render(<ActualApp user={{ id: 7, username: 'test-member', display_name: '只读成员', platform_role: 'member' }} onLogout={async () => undefined} />)
  await screen.findByText('当前为项目成员，只能查看项目配置')
  await user.click(screen.getByRole('tab', { name: '可靠性' }))
  expect(screen.getByRole('textbox', { name: '平台可靠性提示词' })).toHaveValue('平台可靠')
  expect(screen.getByRole('radio', { name: '自定义本项目' })).toBeDisabled()
})

it.each([
  ['P0 / P1 / P2 · 严格', 2, '当前还有 2 个阻断评论，必须逐条处理。'],
  ['仅 P0 · 宽松', 0, '当前门槛内没有未解决的阻断评论。'],
] as const)('refreshes review counts after saving %s without reloading configuration drafts', async (label, blockers, copy) => {
  const { user } = setup(false)
  const original = { ...REVIEWS[0], id: 'policy-review', projectId: PROJECTS[0].id, blockers: 1, status: '有阻断' as const }
  const latest = { ...original, blockers, status: blockers ? '有阻断' as const : '已完成' as const }
  vi.mocked(api.listReviews).mockResolvedValueOnce([original]).mockResolvedValue([latest])
  const realInterval = window.setInterval.bind(window)
  vi.spyOn(window, 'setInterval').mockImplementation(((callback: () => void, delay?: number) => delay === 5000 ? 123 : realInterval(callback, delay)) as typeof window.setInterval)
  render(<App />)
  await screen.findByRole('textbox', { name: '平台安全性提示词' })
  await user.click(screen.getByRole('radio', { name: new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }))
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  await screen.findByText('项目策略已保存')
  await waitFor(() => expect(api.listReviews).toHaveBeenCalledTimes(2))
  expect(api.getPolicy).toHaveBeenCalledTimes(1)
  await user.click(screen.getByRole('button', { name: '自动合并门禁' }))
  await user.click(screen.getByRole('button', { name: '检查某个 MR（可选）' }))
  await user.selectOptions(screen.getByRole('combobox', { name: '目标评审' }), original.id)
  expect(screen.getByText(copy)).toBeInTheDocument()
  expect(screen.getByText('尚未检查')).toBeInTheDocument()
})
