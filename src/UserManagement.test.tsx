import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from './api'
import type { ManagedUser } from './api'
import { UserManagement } from './UserManagement'

const account: ManagedUser = { id: 7, username: 'alice', display_name: 'Alice', platform_role: 'member', is_active: true, last_login: null }
beforeEach(() => { vi.spyOn(api, 'listUsers').mockResolvedValue([account]) })
afterEach(() => vi.restoreAllMocks())

it('loads actual accounts and filters them without invented login dates', async () => {
  render(<UserManagement currentUserId={1} onAccessChange={() => {}} />)
  expect(await screen.findByText('Alice')).toBeInTheDocument()
  expect(screen.getByText('尚未登录')).toBeInTheDocument()
  await userEvent.setup().type(screen.getByRole('searchbox'), 'missing')
  expect(screen.getByText('没有匹配的账号')).toBeInTheDocument()
})

it('opens a real password dialog directly from the current account row', async () => {
  render(<UserManagement currentUserId={7} onAccessChange={() => {}} />)
  await screen.findByText('Alice')
  await userEvent.click(screen.getByRole('button', { name: '修改当前账号密码' }))
  expect(screen.getByRole('dialog', { name: '修改密码' })).toBeInTheDocument()
  expect(screen.getByLabelText('当前密码')).toBeEnabled()
})

it('retries load errors without presenting an empty account directory', async () => {
  vi.mocked(api.listUsers).mockRejectedValueOnce(new Error('offline'))
  render(<UserManagement currentUserId={1} onAccessChange={() => {}} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('offline')
  expect(screen.queryByText('暂无内置账号')).not.toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole('button', { name: '重试加载' }))
  expect(await screen.findByText('Alice')).toBeInTheDocument()
})

it('creates an account once from an empty drawer and clears credentials on close', async () => {
  let resolve!: (value: ManagedUser) => void
  const create = vi.spyOn(api, 'createUser').mockImplementation(() => new Promise(r => { resolve = r }))
  const user = userEvent.setup()
  render(<UserManagement currentUserId={1} onAccessChange={() => {}} />)
  await screen.findByText('Alice')
  await user.click(screen.getByRole('button', { name: '添加内置用户' }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).getByLabelText('初始密码')).toHaveValue('')
  await user.type(within(dialog).getByLabelText('用户名'), 'bob')
  await user.type(within(dialog).getByLabelText('显示名'), 'Bob')
  await user.type(within(dialog).getByLabelText('初始密码'), 'disposable-password')
  await user.type(within(dialog).getByLabelText('确认密码'), 'disposable-password')
  await user.dblClick(within(dialog).getByRole('button', { name: '确认创建' }))
  expect(create).toHaveBeenCalledOnce()
  expect(create).toHaveBeenCalledWith({ username: 'bob', display_name: 'Bob', password: 'disposable-password', platform_role: 'member' })
  expect(within(dialog).getByRole('button', { name: '取消' })).toBeDisabled()
  await act(async () => resolve({ ...account, id: 8, username: 'bob', display_name: 'Bob' }))
  expect(await screen.findByText('Bob')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '添加内置用户' }))
  expect(screen.getByLabelText('初始密码')).toHaveValue('')
})

it('confirms disable and preserves last-admin errors without optimistic success', async () => {
  const update = vi.spyOn(api, 'updateUser').mockRejectedValue(new Error('不能停用最后一名管理员'))
  const user = userEvent.setup()
  render(<UserManagement currentUserId={1} onAccessChange={() => {}} />)
  await screen.findByText('Alice')
  await user.click(screen.getByRole('button', { name: '停用 alice' }))
  expect(update).not.toHaveBeenCalled()
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认停用' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('不能停用最后一名管理员')
  expect(screen.getByText('已启用')).toBeInTheDocument()
  await user.keyboard('{Escape}')
  expect(screen.getByRole('button', { name: '停用 alice' })).toHaveFocus()
})

it('clears reset passwords on rejection and requires matching confirmation', async () => {
  const reset = vi.spyOn(api, 'resetUserPassword').mockRejectedValue(new Error('密码太常见'))
  const user = userEvent.setup()
  render(<UserManagement currentUserId={1} onAccessChange={() => {}} />)
  await screen.findByText('Alice')
  await user.click(screen.getByRole('button', { name: '重置 alice 的密码' }))
  await user.type(screen.getByLabelText('新密码'), 'disposable-password')
  await user.type(screen.getByLabelText('确认密码'), 'different-password')
  await user.click(screen.getByRole('button', { name: '确认重置' }))
  expect(reset).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('两次输入的密码不一致')
  await user.clear(screen.getByLabelText('确认密码'))
  await user.type(screen.getByLabelText('确认密码'), 'disposable-password')
  await user.click(screen.getByRole('button', { name: '确认重置' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('密码太常见')
  expect(screen.getByLabelText('新密码')).toHaveValue('')
  expect(screen.getByLabelText('确认密码')).toHaveValue('')
})

it('keeps own password changes in account security and refreshes authority after self editing', async () => {
  vi.spyOn(api, 'updateUser').mockResolvedValue({ ...account, display_name: 'Updated' })
  const refresh = vi.fn()
  const user = userEvent.setup()
  render(<UserManagement currentUserId={7} onAccessChange={refresh} />)
  await screen.findByText('Alice')
  expect(screen.queryByRole('button', { name: '重置 alice 的密码' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '编辑 alice' }))
  const name = within(screen.getByRole('dialog')).getByLabelText('显示名')
  await user.clear(name); await user.type(name, 'Updated')
  await user.click(screen.getByRole('button', { name: '确认修改' }))
  expect(await screen.findByText('Updated')).toBeInTheDocument()
  expect(refresh).toHaveBeenCalledOnce()
})

it('enables a disabled account only after confirmation', async () => {
  vi.mocked(api.listUsers).mockResolvedValue([{ ...account, is_active: false }])
  const update = vi.spyOn(api, 'updateUser').mockResolvedValue(account)
  const user = userEvent.setup()
  render(<UserManagement currentUserId={1} onAccessChange={() => {}} />)
  await screen.findByText('Alice')
  await user.click(screen.getByRole('button', { name: '启用 alice' }))
  expect(update).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '确认启用' }))
  expect(await screen.findByText('已启用')).toBeInTheDocument()
  expect(update).toHaveBeenCalledWith(7, { is_active: true })
})

it('successfully resets another account without displaying the password', async () => {
  vi.spyOn(api, 'resetUserPassword').mockResolvedValue({ detail: 'ok' })
  const user = userEvent.setup()
  render(<UserManagement currentUserId={1} onAccessChange={() => {}} />)
  await screen.findByText('Alice')
  await user.click(screen.getByRole('button', { name: '重置 alice 的密码' }))
  await user.type(screen.getByLabelText('新密码'), 'disposable-password')
  await user.type(screen.getByLabelText('确认密码'), 'disposable-password')
  await user.click(screen.getByRole('button', { name: '确认重置' }))
  expect(await screen.findByRole('status')).toHaveTextContent('原有会话将在后续请求时失效')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByText('disposable-password')).not.toBeInTheDocument()
})

it.each(['success', 'failure'])('does not refresh session or emit feedback from late %s after unmount', async result => {
  let resolve!: (value: ManagedUser) => void
  let reject!: (reason: Error) => void
  vi.spyOn(api, 'updateUser').mockImplementation(() => new Promise((done, fail) => { resolve = done; reject = fail }))
  const refresh = vi.fn()
  const user = userEvent.setup()
  const view = render(<UserManagement currentUserId={7} onAccessChange={refresh} />)
  await screen.findByText('Alice')
  await user.click(screen.getByRole('button', { name: '编辑 alice' }))
  await user.selectOptions(within(screen.getByRole('dialog')).getByRole('combobox', { name: '平台角色' }), 'admin')
  await user.click(screen.getByRole('button', { name: '确认修改' }))
  view.unmount()
  await act(async () => { if (result === 'success') resolve(account); else reject(new Error('late error')) })
  expect(refresh).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('changes only the selected role without resubmitting legacy display names', async () => {
  const legacy = { ...account, display_name: 'A'.repeat(200) }
  vi.mocked(api.listUsers).mockResolvedValue([legacy])
  const update = vi.spyOn(api, 'updateUser').mockResolvedValue({ ...legacy, platform_role: 'admin' })
  const user = userEvent.setup()
  render(<UserManagement currentUserId={1} onAccessChange={() => {}} />)
  await screen.findByText(legacy.display_name)
  await user.click(screen.getByRole('button', { name: '编辑 alice' }))
  expect(screen.getByRole('button', { name: '确认修改' })).toBeEnabled()
  await user.selectOptions(within(screen.getByRole('dialog')).getByRole('combobox', { name: '平台角色' }), 'admin')
  await user.click(screen.getByRole('button', { name: '确认修改' }))
  expect(update).toHaveBeenCalledWith(7, { platform_role: 'admin' })
})
