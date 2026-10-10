import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { api } from './api'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })
const cases = [
  { page: 'models', field: '模型 ID', method: 'modelConfig', key: 'model', save: '保存模型配置' },
  { page: 'notifications', field: '群名称', method: 'notificationConfig', key: 'name', save: '保存通知配置' },
  { page: 'system', field: 'GitLab 地址', method: 'gitlabConfig', key: 'base_url', save: '保存 GitLab 配置' },
] as const

function setup(page: string) {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', `/platform/settings/${page}`)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue([])
  vi.spyOn(api, 'listReviews').mockResolvedValue([])
  for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig'] as const) vi.spyOn(api, name).mockResolvedValue({})
}

for (const config of cases) {
  it(`${config.page} waits for saved values before exposing the editable form, including refresh`, async () => {
    setup(config.page)
    let resolve!: (value: Record<string, unknown>) => void
    vi.mocked(api[config.method]).mockImplementation(() => new Promise(done => { resolve = done }))
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('Django 后端已连接')
    expect(screen.queryByLabelText(config.field)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: config.save })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载平台配置')
    await waitFor(() => expect(api[config.method]).toHaveBeenCalledOnce())
    await act(async () => resolve({ [config.key]: 'saved-value' }))
    expect(screen.getByLabelText(config.field)).toHaveValue('saved-value')
    await user.click(screen.getByRole('button', { name: '刷新数据' }))
    expect(screen.queryByLabelText(config.field)).not.toBeInTheDocument()
    await act(async () => resolve({ [config.key]: 'new-server-value' }))
    expect(screen.getByLabelText(config.field)).toHaveValue('new-server-value')
  })

  it(`${config.page} shows a failed read and retries instead of presenting an empty configuration`, async () => {
    setup(config.page)
    vi.mocked(api[config.method]).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ [config.key]: 'recovered' })
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('平台配置加载失败')
    expect(screen.queryByLabelText(config.field)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: config.save })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新加载配置' }))
    expect(await screen.findByLabelText(config.field)).toHaveValue('recovered')
  })
}

it('allows leaving a hydrated notification form without an unsaved-changes prompt', async () => {
  setup('notifications')
  vi.mocked(api.notificationConfig).mockResolvedValue({
    name: 'test-group',
    language: '中文',
    enabled: true,
    notify_suggestions: true,
    notify_blockers: true,
    notify_merge_result: true,
  })
  const user = userEvent.setup()
  render(<App />)
  await screen.findByRole('button', { name: '保存通知配置' })
  expect(screen.getByLabelText('群名称')).toHaveAttribute('autocomplete', 'off')
  await user.click(screen.getByRole('button', { name: '系统设置' }))
  expect(await screen.findByRole('heading', { name: '系统设置', level: 1 })).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
