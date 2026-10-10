import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { ModelsPageConnected, NotificationsPageConnected } from './App'

for (const kind of ['models', 'notifications'] as const) {
  it.each(['false', 'reject', 'success'])(`${kind} serializes saves and handles %s without losing unsaved credentials`, async outcome => {
    let resolve!: (value: boolean) => void
    let reject!: (reason: Error) => void
    const onSave = vi.fn(() => new Promise<boolean>((done, fail) => { resolve = done; reject = fail }))
    const onDirtyChange = vi.fn(), onTest = vi.fn(), user = userEvent.setup()
    const props = { initial: { inherited: false }, scope: 'project' as const, projectName: 'chaos', onScopeChange: vi.fn(), tested: false, onTest, onSave, onDirtyChange }
    render(kind === 'models' ? <ModelsPageConnected {...props} modelOptions={[]} /> : <NotificationsPageConnected {...props} onReloadAudit={vi.fn()} />)
    const field = kind === 'models' ? '模型 ID' : '群名称'
    const secret = kind === 'models' ? /^API Key/ : /^Webhook 地址/
    const secretValue = kind === 'models' ? 'test-only-secret' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only-secret'
    const saveLabel = kind === 'models' ? '保存项目模型配置' : '保存项目企微配置'
    const testLabel = kind === 'models' ? '测试连接' : '发送测试消息'
    await user.type(screen.getByLabelText(field), 'draft')
    await user.type(screen.getByLabelText(secret), secretValue)
    await user.dblClick(screen.getByRole('button', { name: saveLabel }))
    expect(onSave).toHaveBeenCalledOnce()
    expect(screen.getByLabelText(field)).toBeDisabled()
    expect(screen.getByLabelText(secret)).toBeDisabled()
    expect(screen.getByRole('button', { name: '恢复全局继承' })).toBeDisabled()
    expect(screen.getByRole('button', { name: testLabel })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('正在保存')
    await user.type(screen.getByLabelText(field), '-newer')
    expect(screen.getByLabelText(field)).toHaveValue('draft')
    await act(async () => { if (outcome === 'reject') reject(new Error('offline')); else resolve(outcome === 'success') })
    expect(screen.getByLabelText(field)).toBeEnabled()
    expect(screen.getByLabelText(field)).toHaveValue('draft')
    expect(onTest).not.toHaveBeenCalled()
    if (outcome === 'success') {
      expect(screen.getByLabelText(secret)).toHaveValue('')
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(onDirtyChange).toHaveBeenLastCalledWith(false)
    } else {
      expect(screen.getByRole('alert')).toHaveTextContent('输入内容已保留')
      expect(screen.getByLabelText(secret)).toHaveValue(secretValue)
      expect(onDirtyChange).toHaveBeenLastCalledWith(true)
      await user.click(screen.getByRole('button', { name: saveLabel }))
      expect(onSave).toHaveBeenCalledTimes(2)
      await act(async () => resolve(true))
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(screen.getByLabelText(secret)).toHaveValue('')
    }
  })
}
