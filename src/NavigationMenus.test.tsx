import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { api } from './api'
import App from './test-app'

beforeEach(() => window.history.replaceState({}, '', '/'))

it('dismisses the project switcher when clicking the page outside it', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByLabelText('切换项目'))
  expect(screen.getByRole('button', { name: '管理项目' })).toBeInTheDocument()

  await user.click(screen.getByRole('heading', { name: '总览', level: 1 }))
  expect(screen.queryByRole('button', { name: '管理项目' })).not.toBeInTheDocument()
})

it('dismisses help when focus moves to another topbar control', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByLabelText('帮助'))
  expect(screen.getByRole('heading', { name: '使用帮助' })).toBeInTheDocument()

  await user.click(screen.getByLabelText('全局搜索'))
  expect(screen.queryByRole('heading', { name: '使用帮助' })).not.toBeInTheDocument()
})

it('dismisses a project row menu without navigating to the row', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: '项目管理' }))
  await user.click(screen.getByLabelText('demo-api 更多操作'))
  expect(screen.getByRole('button', { name: '配置项目策略' })).toBeInTheDocument()

  await user.click(screen.getByRole('heading', { name: '项目管理', level: 1 }))
  expect(screen.queryByRole('button', { name: '配置项目策略' })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '项目管理', level: 1 })).toBeInTheDocument()
})

it('opens the MR list advertised by a favorite project entry', async () => {
  vi.spyOn(api, 'listMergeRequests').mockResolvedValue({ results: [], has_next: false })
  localStorage.setItem('cw-cr:favorites', JSON.stringify(['demo-api']))
  render(<App />)
  await userEvent.click(screen.getByRole('button', { name: '项目管理' }))
  await userEvent.click(screen.getByRole('button', { name: '进入常用项目 demo-api' }))
  expect(await screen.findByRole('heading', { name: 'MR 列表', level: 1 })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'MR 列表' })).toHaveAttribute('aria-current', 'page')
  localStorage.clear()
  vi.restoreAllMocks()
})
