import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { SessionBoundary } from './SessionBoundary'

afterEach(() => vi.unstubAllGlobals())
const user = { id: 1, username: 'tester', display_name: '测试用户', platform_role: 'admin' }
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
function mount() {
  return render(<SessionBoundary>{({ user: current, onLogout }) => <div>
    <p>业务内容：{current.display_name}</p>
    <button onClick={() => { void onLogout().catch(() => undefined) }}>退出测试</button>
  </div>}</SessionBoundary>)
}

describe('session boundary', () => {
  it('does not mount business content while loading or anonymous', async () => {
    let finish!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { finish = resolve })))
    mount()
    expect(screen.getByRole('status')).toHaveTextContent('正在确认登录状态')
    expect(screen.queryByText(/业务内容/)).not.toBeInTheDocument()
    await act(async () => finish(json({ user: null, csrf_token: 'a' })))
    expect(await screen.findByRole('heading', { name: '账号登录' })).toBeInTheDocument()
    expect(screen.queryByText(/业务内容/)).not.toBeInTheDocument()
  })

  it('shows initialization errors and retries without pretending to be anonymous', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('连接失败'))
      .mockResolvedValueOnce(json({ user, csrf_token: 'a' })))
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('连接失败')
    expect(screen.queryByRole('heading', { name: '账号登录' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('业务内容：测试用户')).toBeInTheDocument()
  })

  it('logs in, preserves content on logout failure, and removes it on successful logout', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(json({ user: null, csrf_token: 'a' }))
      .mockResolvedValueOnce(json({ user, csrf_token: 'b' }))
      .mockResolvedValueOnce(json({ detail: '暂时不可用' }, 503))
      .mockResolvedValueOnce(json({ user: null, csrf_token: 'c' })))
    mount()
    await screen.findByRole('heading', { name: '账号登录' })
    await userEvent.type(screen.getByLabelText('用户名'), 'tester')
    await userEvent.type(screen.getByLabelText('密码', { exact: true }), 'test-password')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('业务内容：测试用户')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '退出测试' }))
    expect(screen.getByText('业务内容：测试用户')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '退出测试' }))
    expect(await screen.findByRole('heading', { name: '账号登录' })).toBeInTheDocument()
    expect(screen.queryByText(/业务内容/)).not.toBeInTheDocument()
  })

  it('unmounts sensitive content on current401 and refreshes CSRF for relogin', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json({ user, csrf_token: 'a' }))
      .mockResolvedValueOnce(json({ detail: 'expired' }, 401))
      .mockResolvedValueOnce(json({ user: null, csrf_token: 'fresh' }))
    vi.stubGlobal('fetch', fetcher)
    mount()
    await screen.findByText('业务内容：测试用户')
    await act(async () => { await expect(api.listProjects()).rejects.toMatchObject({ status: 401 }) })
    expect(await screen.findByRole('heading', { name: '账号登录' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('会话已过期')
    expect(screen.queryByText(/业务内容/)).not.toBeInTheDocument()
    expect(fetcher).toHaveBeenCalledTimes(3)
  })
})
