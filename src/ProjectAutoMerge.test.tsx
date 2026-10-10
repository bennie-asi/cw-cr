import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'
import type { MergeGateResponse } from './api'
import App from './test-app'
import ActualApp from './App'
import { PROJECTS, REVIEWS } from './test-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })

function setup(enabled = false, reviews = [REVIEWS[0]], renderApp = true) {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', `/projects/${PROJECTS[0].id}/settings/merge-gate`)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'listReviews').mockResolvedValue(reviews.map(review => ({ ...review, projectId: PROJECTS[0].id })))
  for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig', 'projectModelConfig', 'projectNotificationConfig'] as const) vi.spyOn(api, name).mockResolvedValue({})
  vi.spyOn(api, 'getPolicy').mockResolvedValue({ webhook_trigger: true })
  vi.spyOn(api, 'mergeGate').mockResolvedValue({ enabled, assessment: {}, dry_run_passed: false } as MergeGateResponse)
  const save = vi.spyOn(api, 'updateMergeGate').mockImplementation(async (_project, payload) => ({ enabled: payload.auto_merge_enabled === true, assessment: {} } as MergeGateResponse))
  const check = vi.spyOn(api, 'mergeGateDryRun').mockResolvedValue({ project_id: PROJECTS[0].id, review_id: REVIEWS[0].id, mr_iid: 357,
    passed: false, checks: { review_ready: true, blockers_resolved: false }, blocking_count: 2, reason: '仍有阻断', completed_at: '2026-10-09T01:00:00Z', reviewed_sha: 'a', current_sha: 'a' })
  if (renderApp) render(<App />)
  return { user: userEvent.setup(), save, check }
}

it('enables the project before any review or optional check', async () => {
  const { user, save, check } = setup(false, [])
  const toggle = await screen.findByRole('switch', { name: '项目自动合并' })
  expect(toggle).not.toBeChecked()
  await user.click(toggle)
  await waitFor(() => expect(save).toHaveBeenCalledWith(PROJECTS[0].id, { auto_merge_enabled: true }))
  await waitFor(() => expect(toggle).toBeChecked())
  expect(screen.getByText(/持续作用于当前项目后续/)).toBeInTheDocument()
  expect(check).not.toHaveBeenCalled()
})

it('shows the persisted enabled switch without selecting an MR', async () => {
  setup(true, [])
  expect(await screen.findByRole('switch', { name: '项目自动合并' })).toBeChecked()
  expect(screen.getByText('项目自动合并已开启')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '完成前置条件后启用' })).not.toBeInTheDocument()
})

it('keeps the project enabled when an optional MR check fails or selection changes', async () => {
  const { user, save, check } = setup(true)
  const toggle = await screen.findByRole('switch', { name: '项目自动合并' })
  await user.click(screen.getByText('检查某个 MR（可选）'))
  const target = screen.getByRole('combobox', { name: '目标评审' })
  await user.selectOptions(target, REVIEWS[0].id)
  await user.click(screen.getByRole('button', { name: '执行检查' }))
  await waitFor(() => expect(check).toHaveBeenCalled())
  await screen.findByText('检查未通过')
  expect(toggle).toBeChecked()
  await user.selectOptions(target, '')
  expect(toggle).toBeChecked()
  expect(save).not.toHaveBeenCalled()
})

it('disables the persistent project switch with one action', async () => {
  const { user, save } = setup(true)
  const toggle = await screen.findByRole('switch', { name: '项目自动合并' })
  await user.click(toggle)
  await waitFor(() => expect(save).toHaveBeenCalledWith(PROJECTS[0].id, { auto_merge_enabled: false }))
  await waitFor(() => expect(toggle).not.toBeChecked())
})

it('retains the saved enabled state when closing fails', async () => {
  const { user, save } = setup(true)
  save.mockRejectedValue(new Error('开关保存失败'))
  const toggle = await screen.findByRole('switch', { name: '项目自动合并' })
  await user.click(toggle)
  expect(await screen.findByRole('alert')).toHaveTextContent('开关保存失败')
  expect(toggle).toBeChecked()
})

it('disables the switch while saving to prevent duplicate changes', async () => {
  const { user, save } = setup(false)
  let finish!: (value: MergeGateResponse) => void
  save.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const toggle = await screen.findByRole('switch', { name: '项目自动合并' })
  await user.click(toggle)
  expect(toggle).toBeDisabled()
  await user.click(toggle)
  expect(save).toHaveBeenCalledTimes(1)
  finish({ enabled: true, assessment: {} } as MergeGateResponse)
  await waitFor(() => expect(toggle).toBeChecked())
  expect(toggle).not.toBeDisabled()
})

it('keeps project members unable to change the switch', async () => {
  setup(false, [], false)
  vi.mocked(api.listProjects).mockResolvedValue([{ ...PROJECTS[0], currentUserRole: 'member' }])
  render(<ActualApp user={{ id: 8, username: 'member', display_name: '项目成员', platform_role: 'member' }} onLogout={async () => undefined} />)
  expect(await screen.findByRole('switch', { name: '项目自动合并' })).toBeDisabled()
  expect(api.updateMergeGate).not.toHaveBeenCalled()
})
