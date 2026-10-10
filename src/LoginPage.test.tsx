import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'

describe('Stitch built-in login', () => {
  it('starts empty with no dashboard or public default password, and supports password visibility', async () => {
    render(<LoginPage onLogin={vi.fn()} />)
    expect(screen.getByRole('heading', { name: '账号登录' })).toBeInTheDocument()
    expect(screen.getByLabelText('用户名')).toHaveValue('')
    const password = screen.getByLabelText('密码', { exact: true })
    expect(password).toHaveValue('')
    expect(password).toHaveAttribute('type', 'password')
    expect(screen.queryByText('项目管理')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '显示密码' }))
    expect(password).toHaveAttribute('type', 'text')
    await userEvent.click(screen.getByRole('button', { name: '隐藏密码' }))
    expect(password).toHaveAttribute('type', 'password')
  })

  it('submits credentials once, disables pending form, and clears password on failure', async () => {
    let rejectLogin!: (reason: Error) => void
    const onLogin = vi.fn(() => new Promise<void>((_resolve, reject) => { rejectLogin = reject }))
    render(<LoginPage onLogin={onLogin} />)
    await userEvent.type(screen.getByLabelText('用户名'), 'tester')
    await userEvent.type(screen.getByLabelText('密码', { exact: true }), 'only-test')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(onLogin).toHaveBeenCalledTimes(1)
    expect(onLogin).toHaveBeenCalledWith('tester', 'only-test')
    expect(screen.getByRole('button', { name: '正在登录…' })).toBeDisabled()
    await act(async () => rejectLogin(new Error('账号或密码错误，或账号已停用')))
    expect(screen.getByRole('alert')).toHaveTextContent('账号或密码错误')
    expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('')
    expect(screen.getByRole('button', { name: '登录' })).toBeEnabled()
  })

  it('shows setup instructions without offering anonymous account creation', () => {
    render(<LoginPage onLogin={vi.fn()} needsSetup />)
    expect(screen.getByText(/createsuperuser/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '登录' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '注册' })).not.toBeInTheDocument()
  })

  it('shows expired session and safe recovery guidance', () => {
    render(<LoginPage onLogin={vi.fn()} expired />)
    expect(screen.getByRole('status')).toHaveTextContent('会话已过期，请重新登录')
    expect(screen.getByText(/忘记密码.*联系平台管理员/)).toBeInTheDocument()
  })
})
