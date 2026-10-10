import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'
import { api } from './api'
import { SessionBoundary } from './SessionBoundary'
import { TEST_ADMIN } from './test-app'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('opens the workspace after login over HTTP without crypto.randomUUID', async () => {
  vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
  vi.spyOn(api, 'session').mockResolvedValue({ user: null, csrf_token: 'csrf', needs_setup: false })
  vi.spyOn(api, 'login').mockResolvedValue({ user: TEST_ADMIN, csrf_token: 'csrf', needs_setup: false })
  const user = userEvent.setup()
  render(<SessionBoundary>{session => <App {...session} />}</SessionBoundary>)

  await user.type(await screen.findByLabelText('用户名'), 'test-admin')
  await user.type(screen.getByLabelText('密码'), 'test-password')
  await user.click(screen.getByRole('button', { name: '登录' }))

  expect(await screen.findByRole('heading', { name: '总览', level: 1 })).toBeInTheDocument()
  const owner = window.history.state.cwCr.owner
  expect(owner).toEqual(expect.any(String))
  expect(owner.length).toBeGreaterThan(0)
  await user.click(screen.getByRole('button', { name: '项目管理' }))
  expect(screen.getByRole('heading', { name: '项目管理', level: 1 })).toBeInTheDocument()
  expect(window.history.state.cwCr.owner).toBe(owner)
  await user.click(screen.getByRole('button', { name: '总览' }))
  expect(screen.getByRole('heading', { name: '总览', level: 1 })).toBeInTheDocument()
  expect(window.history.state.cwCr.owner).toBe(owner)
})
