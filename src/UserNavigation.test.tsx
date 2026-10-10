import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { TEST_ADMIN } from './test-app'
import { api } from './api'
import { parseWorkspaceRoute } from './workspaceRoutes'

beforeEach(() => { window.history.replaceState({}, '', '/platform/settings/system') })
afterEach(() => vi.restoreAllMocks())

it('separates system tabs without putting platform accounts into project navigation', async () => {
  vi.spyOn(api, 'listUsers').mockResolvedValue([])
  const user = userEvent.setup()
  render(<App user={TEST_ADMIN} onLogout={async () => {}} />)
  const tabs = screen.getByRole('navigation', { name: '系统设置分类' })
  expect(screen.getByRole('button', { name: '保存 GitLab 配置' })).toBeInTheDocument()
  expect(screen.queryByText('任务队列与重试')).not.toBeInTheDocument()
  await user.click(within(tabs).getByRole('button', { name: '用户管理' }))
  expect(window.location.pathname).toBe('/platform/settings/users')
  expect(await screen.findByText('暂无内置账号')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '系统设置' })).toHaveAttribute('aria-current', 'page')
  await user.click(within(tabs).getByRole('button', { name: '运行与审计' }))
  expect(window.location.pathname).toBe('/platform/settings/runtime')
  expect(screen.getByText('任务队列与重试')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '保存 GitLab 配置' })).not.toBeInTheDocument()
  expect(parseWorkspaceRoute('/projects/chaos/settings/users')).toBeNull()
  expect(parseWorkspaceRoute('/projects/chaos/settings/runtime')).toBeNull()
})

it.each(['users', 'runtime'])('denies member deep links to platform %s before any account query', page => {
  window.history.replaceState({}, '', `/platform/settings/${page}`)
  const list = vi.spyOn(api, 'listUsers')
  render(<App user={{ ...TEST_ADMIN, platform_role: 'member' }} onLogout={async () => {}} />)
  expect(screen.getByText('无权访问平台配置')).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '系统设置分类' })).not.toBeInTheDocument()
  expect(list).not.toHaveBeenCalled()
})
