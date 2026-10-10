import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

it('uses separate platform account and project membership endpoints', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('[]', { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  await api.listUsers()
  expect(fetcher.mock.lastCall?.[0]).toMatch(/\/api\/users\/$/)
  fetcher.mockResolvedValue(new Response('[]', { status: 200 }))
  await api.listProjectMembers('project/a')
  expect(fetcher.mock.lastCall?.[0]).toMatch(/\/projects\/project%2Fa\/members\/$/)
})

it('submits account and membership mutations to their own scope', async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}', { status: 200 })))
  vi.stubGlobal('fetch', fetcher)
  await api.createUser({ username: 'test', display_name: '测试', password: 'disposable', platform_role: 'member' })
  expect(fetcher.mock.lastCall?.[0]).toMatch(/\/users\/$/)
  expect(fetcher.mock.lastCall?.[1].method).toBe('POST')
  expect(JSON.parse(fetcher.mock.lastCall?.[1].body)).toEqual({ username: 'test', display_name: '测试', password: 'disposable', platform_role: 'member' })
  await api.updateUser(7, { is_active: false })
  expect(fetcher.mock.lastCall?.[0]).toMatch(/\/users\/7\/$/)
  expect(fetcher.mock.lastCall?.[1].method).toBe('PATCH')
  expect(JSON.parse(fetcher.mock.lastCall?.[1].body)).toEqual({ is_active: false })
  await api.resetUserPassword(7, 'disposable')
  expect(fetcher.mock.lastCall?.[0]).toMatch(/\/users\/7\/reset-password\/$/)
  expect(fetcher.mock.lastCall?.[1].method).toBe('POST')
  expect(JSON.parse(fetcher.mock.lastCall?.[1].body)).toEqual({ new_password: 'disposable' })
  await api.addProjectMember('p', { username: 'test', role: 'admin' })
  expect(fetcher.mock.lastCall?.[0]).toMatch(/\/projects\/p\/members\/$/)
  expect(fetcher.mock.lastCall?.[1].method).toBe('POST')
  expect(JSON.parse(fetcher.mock.lastCall?.[1].body)).toEqual({ username: 'test', role: 'admin' })
  await api.updateProjectMember('p', 8, 'member')
  expect(fetcher.mock.lastCall?.[0]).toMatch(/\/projects\/p\/members\/8\/$/)
  expect(fetcher.mock.lastCall?.[1].method).toBe('PATCH')
  expect(JSON.parse(fetcher.mock.lastCall?.[1].body)).toEqual({ role: 'member' })
})

it('accepts the empty 204 response after removing a project member', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(api.removeProjectMember('p', 8)).resolves.toBeUndefined()
  expect(fetcher.mock.lastCall?.[0]).toMatch(/\/projects\/p\/members\/8\/$/)
  expect(fetcher.mock.lastCall?.[1].method).toBe('DELETE')
})

it.each([null, { unexpected: ['internal detail'] }, { username: { nested: 'internal detail' } }, '<html>upstream error</html>'])('does not expose unsupported error bodies', async body => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(typeof body === 'string' ? body : JSON.stringify(body), { status: 500 })))
  await expect(api.listUsers()).rejects.toThrow('请求失败（HTTP 500）')
})

it('keeps explicit API errors ahead of field validation messages', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '不能移除最后一位管理员', role: ['invalid'] }), { status: 409 })))
  await expect(api.removeProjectMember('p', 8)).rejects.toThrow('不能移除最后一位管理员')
})

it.each([{ username: ['用户名已存在'] }, ['不能修改此字段']])('preserves actionable server validation messages', async body => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 400 })))
  await expect(api.createUser({ username: 'test', display_name: '测试', password: 'disposable', platform_role: 'member' })).rejects.toThrow(Array.isArray(body) ? body[0] : body.username[0])
})
