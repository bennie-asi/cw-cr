import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { api } from './api'

beforeEach(() => window.history.replaceState({}, '', '/'))
afterEach(() => vi.restoreAllMocks())

it('opens real membership management only inside the selected project', async () => {
  const list = vi.spyOn(api, 'listProjectMembers').mockResolvedValue([])
  const user = userEvent.setup()
  render(<App />)
  expect(screen.queryByRole('button', { name: '成员权限' })).not.toBeInTheDocument()
  await user.click(screen.getByLabelText('切换项目'))
  await user.click(screen.getByRole('button', { name: 'demo-api' }))
  await user.click(screen.getByRole('button', { name: '成员权限' }))
  expect(window.location.pathname).toBe('/projects/demo-api/settings/members')
  expect(await screen.findByText('尚未添加项目成员')).toBeInTheDocument()
  expect(list).toHaveBeenCalledWith('demo-api')
})

it('keeps the platform outside projects and enters an explicit project space', async () => {
  const user = userEvent.setup()
  render(<App />)
  expect(screen.getByRole('button', {name:'总览'})).toBeInTheDocument()
  expect(screen.queryByRole('button', {name:'项目策略'})).not.toBeInTheDocument()
  await user.click(screen.getByLabelText('切换项目'))
  await user.click(screen.getByRole('button', {name:'demo-api'}))
  expect(window.location.pathname).toBe('/projects/demo-api/merge-requests')
  expect(screen.getByRole('button', {name:'MR 列表'})).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('heading', {name:'MR 列表'})).toBeInTheDocument()
  expect(screen.queryByRole('button', {name:'总览'})).not.toBeInTheDocument()
  expect(screen.getByRole('button', {name:'返回平台'})).toBeInTheDocument()
  await user.click(screen.getByRole('button', {name:'模型与引擎'}))
  expect(window.location.pathname).toBe('/projects/demo-api/settings/models')
  expect(screen.getByRole('button', {name:'保存项目模型配置'})).toBeInTheDocument()
  expect(screen.queryByRole('button', {name:'保存模型配置'})).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', {name:'返回平台'}))
  expect(window.location.pathname).toBe('/platform/overview')
})

it('restores project configuration scope from a direct link', () => {
  window.history.replaceState({}, '', '/projects/demo-web/settings/models')
  render(<App />)
  expect(screen.getByRole('button', {name:'保存项目模型配置'})).toBeInTheDocument()
  expect(screen.getByRole('heading', {name:'demo-web · 项目模型覆盖'})).toBeInTheDocument()
})

it('opens project history from the gate when no review is selected', async () => {
  window.history.replaceState({}, '', '/projects/demo-api/settings/merge-gate')
  render(<App />)
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', {name:'检查某个 MR（可选）'}))
  await user.click(screen.getByRole('button', {name:'查看评论'}))
  expect(window.location.pathname).toBe('/projects/demo-api/reviews')
})

it('restores platform and project scope on browser navigation', () => {
  render(<App />)
  act(() => {
    window.history.replaceState({}, '', '/projects/demo-web/settings/notifications')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  expect(screen.getByRole('button', {name:'保存项目企微配置'})).toBeInTheDocument()
  act(() => {
    window.history.replaceState({}, '', '/platform/settings/notifications')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  expect(screen.queryByRole('button', {name:'保存项目企微配置'})).not.toBeInTheDocument()
  expect(screen.getByRole('button', {name:'总览'})).toBeInTheDocument()
})
