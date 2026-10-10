import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import * as AppModule from './App'
import { mapProject } from './api'

const calls = vi.hoisted(() => ({ read: vi.fn(), save: vi.fn(), run: vi.fn() }))
vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return { ...actual, api: { ...actual.api, projectSync: calls.read, saveProjectSync: calls.save, runProjectSync: calls.run } }
})
const base = {
  enabled: false, interval_seconds: 60, activated_at: null, next_sync_at: null,
  last_sync_at: null, last_success_at: null, sync_state: 'idle', last_error: '',
  last_result: {}, consecutive_failures: 0, baseline_mrs: 3, in_progress: false,
}
beforeEach(() => {
  vi.resetAllMocks()
  calls.read.mockResolvedValue(base)
  calls.save.mockResolvedValue({ ...base, enabled: true, interval_seconds: 120 })
  calls.run.mockResolvedValue({ ...base, enabled: true })
})
function mount(readOnly = false) {
  expect(AppModule.ProjectSync).toBeTypeOf('function')
  return render(<AppModule.ProjectSync projectId="project-a" readOnly={readOnly} />)
}

it('shows automatic review as available when polling is enabled without Webhooks', () => {
  const project = mapProject({ id: 'poll-only', name: 'Polling', gitlab_project_path: 'team/project',
    policy: { webhook_trigger: false }, sync_enabled: true })
  expect(project.automationEnabled).toBe(true)
  expect(project.syncEnabled).toBe(true)
})

it('loads actual configuration and explains that existing MR commits are baselined', async () => {
  mount()
  expect(await screen.findByRole('checkbox', { name: '启用定时同步' })).not.toBeChecked()
  expect(screen.getByRole('spinbutton', { name: '同步间隔（秒）' })).toHaveValue(60)
  expect(screen.getByText(/开启时记录现有 MR 的提交/)).toBeInTheDocument()
  expect(calls.read).toHaveBeenCalledWith('project-a')
  expect(calls.save).not.toHaveBeenCalled()
})

it('saves the selected toggle and interval together', async () => {
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('checkbox', { name: '启用定时同步' }))
  const interval = screen.getByRole('spinbutton', { name: '同步间隔（秒）' })
  await user.clear(interval)
  await user.type(interval, '120')
  await user.click(screen.getByRole('button', { name: '保存同步设置' }))
  await waitFor(() => expect(calls.save).toHaveBeenCalledWith('project-a', true, 120))
  expect(await screen.findByText('同步设置已保存')).toBeInTheDocument()
})

it('retains edited values after activation fails', async () => {
  calls.save.mockRejectedValueOnce(new Error('无法完整读取 GitLab MR'))
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('checkbox', { name: '启用定时同步' }))
  await user.click(screen.getByRole('button', { name: '保存同步设置' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('无法完整读取 GitLab MR')
  expect(screen.getByRole('checkbox', { name: '启用定时同步' })).toBeChecked()
  expect(screen.getByRole('button', { name: '保存同步设置' })).toBeEnabled()
})

it('requests immediate synchronization without silently saving configuration edits', async () => {
  calls.read.mockResolvedValue({ ...base, enabled: true })
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: '立即同步' }))
  await waitFor(() => expect(calls.run).toHaveBeenCalledWith('project-a'))
  expect(calls.save).not.toHaveBeenCalled()
  expect(await screen.findByText('已请求后台同步')).toBeInTheDocument()
})

it('allows members to inspect status and prevents configuration and manual sync writes', async () => {
  calls.read.mockResolvedValue({ ...base, enabled: true, last_error: '仓库权限不足', sync_state: 'error' })
  mount(true)
  expect(await screen.findByRole('checkbox', { name: '启用定时同步' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '保存同步设置' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '立即同步' })).toBeDisabled()
  expect(screen.getByText('仓库权限不足')).toBeInTheDocument()
})

it('rejects invalid intervals before calling the API', async () => {
  mount()
  const user = userEvent.setup()
  const field = await screen.findByRole('spinbutton', { name: '同步间隔（秒）' })
  await user.clear(field)
  await user.type(field, '29')
  expect(screen.getByRole('button', { name: '保存同步设置' })).toBeDisabled()
  expect(screen.getByText('同步间隔须为 30–3600 秒的整数')).toBeInTheDocument()
  expect(calls.save).not.toHaveBeenCalled()
})

it('does not apply a late save response to a different project', async () => {
  let finish!: (value: typeof base) => void
  calls.save.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const view = mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('checkbox', { name: '启用定时同步' }))
  await user.click(screen.getByRole('button', { name: '保存同步设置' }))
  await waitFor(() => expect(calls.save).toHaveBeenCalledTimes(1))
  view.rerender(<AppModule.ProjectSync projectId="project-b" readOnly={false} />)
  expect(await screen.findByRole('checkbox', { name: '启用定时同步' })).not.toBeChecked()
  await act(async () => finish({ ...base, enabled: true }))
  expect(screen.getByRole('checkbox', { name: '启用定时同步' })).not.toBeChecked()
  expect(screen.queryByText('同步设置已保存')).not.toBeInTheDocument()
})

it('fails closed when configuration cannot load and supports an explicit reload', async () => {
  calls.read.mockRejectedValueOnce(new Error('暂时无法加载'))
  mount()
  expect(await screen.findByRole('alert')).toHaveTextContent('暂时无法加载')
  expect(screen.queryByRole('button', { name: '保存同步设置' })).not.toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole('button', { name: '重新加载同步设置' }))
  expect(await screen.findByRole('checkbox', { name: '启用定时同步' })).not.toBeChecked()
})
