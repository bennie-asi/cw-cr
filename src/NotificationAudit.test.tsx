import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import AuthenticatedApp from './App'
import { api } from './api'
import { PROJECTS } from './test-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })
function setup() {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', `/projects/${PROJECTS[0].id}/settings/notifications`)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue(PROJECTS.slice(0, 2))
  vi.spyOn(api, 'listReviews').mockResolvedValue([])
  for (const name of ['overview', 'modelConfig', 'gitlabConfig', 'notificationConfig', 'getPolicy', 'projectModelConfig', 'projectNotificationConfig', 'mergeGate'] as const) vi.spyOn(api, name).mockResolvedValue({})
  vi.spyOn(api, 'systemStatus').mockResolvedValue({ audit_logs: [{ action: '测试企业微信通知', target: 'other-project-private', metadata: { ok: true } }] })
}

it.each(['success', 'failure'] as const)('refreshes global delivery records immediately after a notification test %s', async outcome => {
  setup()
  window.history.replaceState({}, '', '/platform/settings/notifications')
  vi.spyOn(api, 'auditLogs').mockResolvedValueOnce([]).mockResolvedValue([
    { action: '测试企业微信通知', target: '最新全局', metadata: { ok: outcome === 'success' } },
  ])
  const send = vi.spyOn(api, 'testNotificationConfig')
  if (outcome === 'success') send.mockResolvedValue({ ok: true })
  else send.mockRejectedValue(new Error('企业微信拒绝测试消息'))
  render(<App />)
  await screen.findByText('暂无发送记录')
  await userEvent.click(screen.getByRole('button', { name: '发送测试消息' }))
  expect(await screen.findByText('最新全局 · 测试消息')).toBeInTheDocument()
  expect(api.auditLogs).toHaveBeenCalledTimes(2)
})

it('uses scoped audit rows, never the global feed, and ignores stale project responses', async () => {
  setup()
  let finish!: (rows: Record<string, unknown>[]) => void
  const logs = vi.spyOn(api, 'projectAuditLogs').mockImplementation(id => id === PROJECTS[0].id
    ? new Promise(resolve => { finish = resolve })
    : Promise.resolve([{ action: '测试企业微信通知', target: 'current-project', metadata: { ok: true } }]))
  render(<App />)
  await screen.findByRole('button', { name: '保存项目企微配置' })
  expect(screen.queryByText(/other-project-private/)).not.toBeInTheDocument()
  expect(screen.getByText('正在加载发送记录…')).toBeInTheDocument()
  await userEvent.click(screen.getByLabelText('切换项目'))
  await userEvent.click(screen.getByRole('button', { name: PROJECTS[1].name }))
  expect(await screen.findByText('current-project · 测试消息')).toBeInTheDocument()
  await act(async () => finish([{ action: '测试企业微信通知', target: 'stale-private', metadata: { ok: true } }]))
  expect(screen.queryByText(/stale-private|other-project-private/)).not.toBeInTheDocument()
  expect(logs).toHaveBeenCalledWith(PROJECTS[1].id)
})

it('shows a retryable audit error without hiding the configuration form', async () => {
  setup()
  vi.spyOn(api, 'projectAuditLogs').mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([])
  render(<App />)
  expect(await screen.findByText('发送记录加载失败')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '保存项目企微配置' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '重试加载记录' }))
  expect(await screen.findByText('暂无发送记录')).toBeInTheDocument()
})

it.each(['member', 'inherited'] as const)('allows audit retry when notification configuration is %s', async mode => {
  setup()
  vi.mocked(api.listProjects).mockResolvedValue([{ ...PROJECTS[0], currentUserRole: mode === 'member' ? 'member' : 'admin' }])
  vi.mocked(api.projectNotificationConfig).mockResolvedValue({ inherited: mode === 'inherited' })
  const logs = vi.spyOn(api, 'projectAuditLogs').mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([])
  const save = vi.spyOn(api, 'updateProjectNotificationConfig')
  const send = vi.spyOn(api, 'testProjectNotificationConfig')
  render(<AuthenticatedApp user={{ id: 2, username: 'reader', display_name: '项目用户', platform_role: 'member' }} onLogout={vi.fn()} />)
  await screen.findByText('发送记录加载失败')
  const retry = screen.getByRole('button', { name: '重试加载记录' })
  expect(retry).toBeEnabled()
  expect(screen.getByRole('button', { name: '保存项目企微配置' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '发送测试消息' })).toBeDisabled()
  await userEvent.click(retry)
  expect(await screen.findByText('暂无发送记录')).toBeInTheDocument()
  expect(logs).toHaveBeenCalledTimes(2)
  expect(save).not.toHaveBeenCalled()
  expect(send).not.toHaveBeenCalled()
})
