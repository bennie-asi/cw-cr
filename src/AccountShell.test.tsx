import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'
import { api } from './api'
import type { SessionUser } from './api'
import { PROJECTS } from './test-fixtures'

const member: SessionUser = { id: 2, username: 'member-a', display_name: '项目成员甲', platform_role: 'member' }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })

it.each(['member', 'admin'] as const)('uses project role %s independently of platform role', async role => {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', `/projects/${PROJECTS[0].id}/settings/models`)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue([{ ...PROJECTS[0], currentUserRole: role }])
  vi.spyOn(api, 'listReviews').mockResolvedValue([])
  for (const key of ['overview', 'getPolicy', 'projectNotificationConfig', 'mergeGate'] as const) vi.spyOn(api, key).mockResolvedValue({})
  vi.spyOn(api, 'projectModelConfig').mockResolvedValue({ inherited: false, model: 'test-model' })
  vi.spyOn(api, 'projectAuditLogs').mockResolvedValue([])
  const save = vi.spyOn(api, 'updateProjectModelConfig').mockResolvedValue({})
  render(<App user={member} onLogout={vi.fn()} />)
  const button = await screen.findByRole('button', { name: '保存项目模型配置' }, { timeout: 5000 })
  if (role === 'member') {
    expect(button).toBeDisabled()
    expect(screen.getByLabelText('Base URL')).toBeDisabled()
    await userEvent.click(button)
    expect(save).not.toHaveBeenCalled()
    expect(screen.getByText('当前为项目成员，只能查看项目配置')).toBeInTheDocument()
  } else {
    expect(button).toBeEnabled()
    expect(screen.getByLabelText('Base URL')).toBeEnabled()
  }
  expect(screen.queryByRole('button', { name: '系统设置' })).not.toBeInTheDocument()
})

it('uses real identity and account controls in both user menus', async () => {
  render(<App user={member} onLogout={vi.fn()} />)
  for (const name of ['用户菜单', '底部用户菜单']) {
    const trigger = screen.getByLabelText(name)
    expect(trigger).toHaveTextContent('项目成员甲')
    await userEvent.click(trigger)
    const menu = within(screen.getByRole('dialog', { name: `${name}选项` }))
    expect(menu.getByText('member-a')).toBeInTheDocument()
    expect(menu.getByText('平台成员')).toBeInTheDocument()
    expect(menu.getByRole('button', { name: '修改密码' })).toBeInTheDocument()
    expect(menu.getByRole('button', { name: '退出登录' })).toBeInTheDocument()
    expect(menu.queryByRole('button', { name: '系统设置' })).not.toBeInTheDocument()
    await userEvent.click(trigger)
  }
})

it('hides platform settings and project creation from a platform member', async () => {
  render(<App user={member} onLogout={vi.fn()} />)
  for (const name of ['模型与引擎', '通知配置', '系统设置']) {
    expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
  }
  await userEvent.click(screen.getByRole('button', { name: '项目管理' }))
  expect(screen.queryByRole('button', { name: '添加项目' })).not.toBeInTheDocument()
})

it('denies a member global configuration deep link without rendering its form', () => {
  window.history.replaceState({}, '', '/platform/settings/models')
  render(<App user={member} onLogout={vi.fn()} />)
  expect(screen.getByText('无权访问平台配置')).toBeInTheDocument()
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument()
})

it('hydrates and polls member data without any platform-admin requests', async () => {
  vi.stubEnv('MODE', 'development')
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue([])
  const reviews = vi.spyOn(api, 'listReviews').mockResolvedValue([])
  vi.spyOn(api, 'overview').mockResolvedValue({})
  const globals = ['systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig'] as const
  for (const name of globals) vi.spyOn(api, name).mockRejectedValue(new Error('must not request'))
  let tick: (() => void) | undefined
  const original = window.setInterval.bind(window)
  vi.spyOn(window, 'setInterval').mockImplementation(((callback: () => void, delay?: number) => {
    if (delay === 5000) { tick = callback; return 234 }
    return original(callback, delay)
  }) as typeof window.setInterval)
  render(<App user={member} onLogout={vi.fn()} />)
  await screen.findByText('Django 后端已连接')
  await waitFor(() => expect(tick).toBeDefined())
  await act(async () => { tick!() })
  await waitFor(() => expect(reviews).toHaveBeenCalledTimes(2))
  for (const name of globals) expect(api[name]).not.toHaveBeenCalled()
})
