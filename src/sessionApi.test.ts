import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, subscribeSessionExpired } from './api'

afterEach(() => vi.unstubAllGlobals())

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('session API transport', () => {
  it('treats password endpoint401 as expiration, not wrong login credentials', async () => {
    const expired = vi.fn()
    const unsubscribe = subscribeSessionExpired(expired)
    try {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: '请先登录' }, 401)))
      await expect(api.changePassword('old', 'new')).rejects.toMatchObject({ status: 401 })
      expect(expired).toHaveBeenCalledTimes(1)
    } finally { unsubscribe() }
  })

  it('ignores a stale password response token after login rotation', async () => {
    let finish!: (response: Response) => void
    const fetcher = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
      .mockResolvedValueOnce(json({ user: { id: 2 }, csrf_token: 'new-login' }))
      .mockResolvedValueOnce(json({}))
    vi.stubGlobal('fetch', fetcher)
    const pending = api.changePassword('old', 'new')
    await api.login('second', 'password')
    finish(json({ detail: 'updated', csrf_token: 'old-password-response' }))
    await pending
    await api.updatePolicy('p', {})
    expect(new Headers(fetcher.mock.calls[2][1].headers).get('X-CSRFToken')).toBe('new-login')
  })

  it('expires on business401 once, not on forbidden or login errors', async () => {
    const expired = vi.fn()
    const unsubscribe = subscribeSessionExpired(expired)
    try {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'denied' }, 403)))
      await expect(api.listProjects()).rejects.toMatchObject({ status: 403 })
      expect(expired).not.toHaveBeenCalled()
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'wrong' }, 401)))
      await expect(api.login('test', 'wrong')).rejects.toMatchObject({ status: 401 })
      expect(expired).not.toHaveBeenCalled()
      let finish!: (response: Response) => void
      vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { finish = resolve })))
      const pending = api.listProjects()
      const rejection = expect(pending).rejects.toMatchObject({ status: 401 })
      finish(json({ detail: 'expired' }, 401))
      await rejection
      expect(expired).toHaveBeenCalledTimes(1)
    } finally { unsubscribe() }
  })

  it('ignores an old business401 after another login succeeds', async () => {
    const expired = vi.fn()
    const unsubscribe = subscribeSessionExpired(expired)
    let finish!: (response: Response) => void
    const fetcher = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
      .mockResolvedValueOnce(json({ user: { id: 2 }, csrf_token: 'new' }))
    vi.stubGlobal('fetch', fetcher)
    try {
      const pending = api.listProjects()
      const rejection = expect(pending).rejects.toMatchObject({ status: 401 })
      await api.login('second', 'password')
      finish(json({ detail: 'old session' }, 401))
      await rejection
      expect(expired).not.toHaveBeenCalled()
    } finally { unsubscribe() }
  })

  it('does not let a late session response overwrite rotated CSRF', async () => {
    let finish!: (response: Response) => void
    const fetcher = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
      .mockResolvedValueOnce(json({ user: { id: 2 }, csrf_token: 'current' }))
      .mockResolvedValueOnce(json({ id: 'p' }))
    vi.stubGlobal('fetch', fetcher)
    const stale = api.session()
    await api.login('second', 'password')
    finish(json({ user: null, csrf_token: 'stale' }))
    await stale
    await api.updatePolicy('p', {})
    expect(new Headers(fetcher.mock.calls[2][1].headers).get('X-CSRFToken')).toBe('current')
  })

  it('includes cookies and uses the current CSRF token for writes, including after login rotation', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(json({ user: null, needs_setup: false, csrf_token: 'before-login' }))
      .mockResolvedValueOnce(json({ user: { id: 1, username: 'tester', platform_role: 'admin' }, csrf_token: 'after-login' }))
      .mockResolvedValueOnce(json({ id: 'p' }))
    vi.stubGlobal('fetch', fetcher)
    await api.session()
    await api.login('tester', 'only-test-password')
    await api.updatePolicy('p', { command_review: false })
    expect(fetcher.mock.calls[0][1].credentials).toBe('include')
    expect(new Headers(fetcher.mock.calls[1][1].headers).get('X-CSRFToken')).toBe('before-login')
    expect(new Headers(fetcher.mock.calls[2][1].headers).get('X-CSRFToken')).toBe('after-login')
    expect(fetcher.mock.calls[1][1].body).toBe(JSON.stringify({ username: 'tester', password: 'only-test-password' }))
  })

  it('preserves HTTP status to distinguish unauthenticated from forbidden and rate limited', async () => {
    for (const status of [401, 403, 429]) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: '安全提示' }, status)))
      await expect(api.session()).rejects.toMatchObject({ status, message: '安全提示' })
    }
  })

  it('clears the old token on an anonymous session without a token', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ user: null, csrf_token: 'old' })))
    await api.session()
    const fetcher = vi.fn().mockResolvedValueOnce(json({ user: null })).mockResolvedValueOnce(json({ user: null }))
    vi.stubGlobal('fetch', fetcher)
    await api.session()
    await api.logout()
    expect(new Headers(fetcher.mock.calls[1][1].headers).has('X-CSRFToken')).toBe(false)
  })

  it('never renders raw upstream HTML errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>secret</html>', { status: 502 })))
    await expect(api.session()).rejects.toMatchObject({ status: 502, message: '请求失败（HTTP 502）' })
  })
})
