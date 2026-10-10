import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { api } from './api'
import { PROJECTS } from './test-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })
function setup() {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', `/projects/${PROJECTS[0].id}/settings/policy`)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'listReviews').mockResolvedValue([])
  for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig', 'projectModelConfig', 'projectNotificationConfig', 'mergeGate'] as const) vi.spyOn(api, name).mockResolvedValue({})
  vi.spyOn(api, 'getPolicy').mockResolvedValue({ summary_prompt: 'original' })
}

it('labels the action as saving the whole project policy, not only its prompt', async () => {
  setup()
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '高级设置' }))
  await screen.findByLabelText('中文总评提示词')
  expect(screen.getByRole('button', { name: '保存项目策略' })).toBeEnabled()
})

it.each(['success', 'failure'])('ignores policy save %s after confirmed navigation away', async result => {
  setup()
  let resolve!: (value: Record<string, unknown>) => void
  let reject!: (reason: Error) => void
  vi.spyOn(api, 'updatePolicy').mockImplementation(() => new Promise((done, fail) => { resolve = done; reject = fail }))
  const user = userEvent.setup()
  render(<App />)
  await user.click(await screen.findByRole('button', { name: '高级设置' }))
  await user.type(await screen.findByLabelText('中文总评提示词'), '-draft')
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  await user.click(screen.getByRole('button', { name: '评审历史' }))
  await user.click(screen.getByRole('button', { name: '放弃修改并离开' }))
  await act(async () => { if (result === 'success') resolve({ summary_prompt: 'original-draft' }); else reject(new Error('offline')) })
  expect(screen.queryByText('项目策略已保存')).not.toBeInTheDocument()
  expect(screen.queryByText('策略已在页面更新，但后端保存失败')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '模型与引擎' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('keeps newer policy edits dirty when an earlier save succeeds', async () => {
  setup()
  let resolve!: (value: Record<string, unknown>) => void
  vi.spyOn(api, 'updatePolicy').mockImplementation(() => new Promise(done => { resolve = done }))
  const user = userEvent.setup()
  render(<App />)
  await user.click(await screen.findByRole('button', { name: '高级设置' }))
  await user.type(await screen.findByLabelText('中文总评提示词'), '-saved')
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  await user.type(screen.getByLabelText('中文总评提示词'), '-newer')
  await act(async () => resolve({ summary_prompt: 'original-saved' }))
  expect(screen.getByLabelText('中文总评提示词')).toHaveValue('original-saved-newer')
  await user.click(screen.getByRole('button', { name: '评审历史' }))
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

it.each(['success', 'failure'])('prevents concurrent policy writes and releases the save action after %s', async outcome => {
  setup()
  let resolve!: (value: Record<string, unknown>) => void
  let reject!: (reason: Error) => void
  const save = vi.spyOn(api, 'updatePolicy').mockImplementation(() => new Promise((done, fail) => { resolve = done; reject = fail }))
  const user = userEvent.setup()
  render(<App />)
  await user.click(await screen.findByRole('button', { name: '高级设置' }))
  await user.type(await screen.findByLabelText('中文总评提示词'), '-submitted')
  await user.dblClick(screen.getByRole('button', { name: '保存项目策略' }))
  expect(save).toHaveBeenCalledOnce()
  expect(screen.getByRole('button', { name: '保存项目策略' })).toBeDisabled()
  await user.type(screen.getByLabelText('中文总评提示词'), '-newer')
  await act(async () => {
    if (outcome === 'success') resolve({ summary_prompt: 'original-submitted' })
    else reject(new Error('offline'))
  })
  expect(screen.getByRole('button', { name: '保存项目策略' })).toBeEnabled()
  expect(screen.getByLabelText('中文总评提示词')).toHaveValue('original-submitted-newer')
  await user.click(screen.getByRole('button', { name: '保存项目策略' }))
  expect(save).toHaveBeenCalledTimes(2)
  expect(save.mock.calls[1][1]).toEqual(expect.objectContaining({ summary_prompt: 'original-submitted-newer' }))
  await act(async () => resolve({ summary_prompt: 'original-submitted-newer' }))
  await user.click(screen.getByRole('button', { name: '评审历史' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
