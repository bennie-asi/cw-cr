import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { AccountMenu } from './AccountMenu'
import { api } from './api'
const account = { id: 1, username: 'alice', display_name: 'Alice', platform_role: 'admin' as const }
afterEach(() => vi.restoreAllMocks())
async function openPassword() {
  await userEvent.click(screen.getByLabelText('用户菜单'))
  await userEvent.click(screen.getByRole('button', { name: '修改密码' }))
  return screen.getByRole('dialog', { name: '修改密码' })
}
it('validates confirmation, submits once, then clears and closes password form', async () => {
  let finish!: (data: { detail: string; csrf_token: string }) => void
  const change = vi.spyOn(api, 'changePassword').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  render(<AccountMenu user={account} onLogout={vi.fn()} label="用户菜单" />)
  const dialog = await openPassword()
  await userEvent.type(within(dialog).getByLabelText('当前密码'), 'old-password')
  await userEvent.type(within(dialog).getByLabelText('新密码'), 'new-password-strong')
  await userEvent.type(within(dialog).getByLabelText('确认新密码'), 'mismatch')
  await userEvent.click(within(dialog).getByRole('button', { name: '保存密码' }))
  expect(within(dialog).getByRole('alert')).toHaveTextContent('两次输入的新密码不一致')
  expect(change).not.toHaveBeenCalled()
  await userEvent.clear(within(dialog).getByLabelText('确认新密码'))
  await userEvent.type(within(dialog).getByLabelText('确认新密码'), 'new-password-strong')
  await userEvent.click(within(dialog).getByRole('button', { name: '保存密码' }))
  expect(change).toHaveBeenCalledTimes(1)
  expect(change).toHaveBeenCalledWith('old-password', 'new-password-strong')
  expect(within(dialog).getByRole('button', { name: '正在保存…' })).toBeDisabled()
  await act(async () => finish({ detail: '密码已更新', csrf_token: 'new' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('密码已更新')
  expect(screen.getByLabelText('用户菜单')).toHaveFocus()
  const reopened = await openPassword()
  for (const input of within(reopened).getAllByLabelText(/密码/)) expect(input).toHaveValue('')
})

it('keeps server errors visible, clears sensitive fields, and cancels with focus restored', async () => {
  vi.spyOn(api, 'changePassword').mockRejectedValue(new Error('当前密码不正确'))
  render(<AccountMenu user={account} onLogout={vi.fn()} label="用户菜单" />)
  const dialog = await openPassword()
  for (const label of ['当前密码', '新密码', '确认新密码']) await userEvent.type(within(dialog).getByLabelText(label), 'test-password')
  await userEvent.click(within(dialog).getByRole('button', { name: '保存密码' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('当前密码不正确')
  expect(within(dialog).getByLabelText('当前密码')).toHaveValue('')
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByLabelText('用户菜单')).toHaveFocus()
})

it('does not pretend logout succeeded when the request fails', async () => {
  const logout = vi.fn().mockRejectedValue(new Error('退出失败，请重试'))
  render(<AccountMenu user={account} onLogout={logout} label="用户菜单" />)
  await userEvent.click(screen.getByLabelText('用户菜单'))
  await userEvent.click(screen.getByRole('button', { name: '退出登录' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('退出失败，请重试')
  expect(screen.getByLabelText('用户菜单')).toHaveTextContent('Alice')
})

it('closes account actions after clicking outside the menu', async () => {
  render(<><AccountMenu user={account} onLogout={vi.fn()} label="用户菜单" /><button>页面内容</button></>)
  await userEvent.click(screen.getByLabelText('用户菜单'))
  expect(screen.getByRole('button', { name: '修改密码' })).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: '页面内容' }))
  expect(screen.queryByRole('button', { name: '修改密码' })).not.toBeInTheDocument()
})
