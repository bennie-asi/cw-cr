import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { NotificationsPageConnected } from './App'
import { api } from './api'
import { PROJECTS } from './test-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })
const common = {
  initial: { name: '测试群', enabled: true, notify_suggestions: true, notify_blockers: true, notify_merge_result: true },
  projectName: 'chaos', onScopeChange: () => {}, tested: false, onTest: () => {}, onReloadAudit: () => {}, onSave: () => true,
}

it('offers only the implemented notification language and normalizes an unsupported saved language', async () => {
  const onSave = vi.fn().mockResolvedValue(true)
  render(<NotificationsPageConnected {...common} initial={{ ...common.initial, language: 'English' }} scope="global" onSave={onSave} />)
  expect(screen.getByRole('combobox', { name: /消息语言/ })).toHaveValue('中文')
  expect(screen.queryByRole('option', { name: 'English' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '保存通知配置' }))
  expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ language: '中文' }))
})

it('loads the selected project notification history from the platform audit endpoint', async () => {
  vi.stubEnv('MODE', 'development')
  const logs = vi.spyOn(api, 'auditLogs').mockResolvedValue([])
  render(<NotificationsPageConnected {...common} scope="global" projects={PROJECTS.slice(0, 2)} />)
  await waitFor(() => expect(logs).toHaveBeenCalledWith(undefined))
  await userEvent.selectOptions(screen.getByRole('combobox', { name: '按项目筛选通知' }), PROJECTS[1].id)
  await waitFor(() => expect(logs).toHaveBeenCalledWith(PROJECTS[1].id))
  expect(screen.queryByText('真实通知内容预览')).not.toBeInTheDocument()
})

it('shows actual project review notification content and copy action', async () => {
  vi.stubEnv('MODE', 'development')
  vi.spyOn(api, 'projectNotificationPreview').mockResolvedValue({ review_id: 'review-35', repository_path: 'team/api', will_send: true, content: '### 智审台 · MR !35\n真实的中文总评' })
  render(<NotificationsPageConnected {...common} scope="project" projectId="project-1" />)
  expect(await screen.findByText(/真实的中文总评/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '复制通知内容' })).toBeEnabled()
  expect(screen.getByText('按当前已保存发送时机将发送')).toBeInTheDocument()
})

it('refreshes the notification decision after saved configuration changes', async () => {
  vi.stubEnv('MODE', 'development')
  const preview = vi.spyOn(api, 'projectNotificationPreview')
    .mockResolvedValueOnce({ review_id: 'review-35', content: '已完成评审', will_send: true })
    .mockResolvedValueOnce({ review_id: 'review-35', content: '已完成评审', will_send: false })
  const view = render(<NotificationsPageConnected {...common} scope="project" projectId="project-1" />)
  expect(await screen.findByText('按当前已保存发送时机将发送')).toBeInTheDocument()
  view.rerender(<NotificationsPageConnected {...common} initial={{ ...common.initial, notify_suggestions: false }} scope="project" projectId="project-1" />)
  expect(await screen.findByText('按当前已保存发送时机将跳过')).toBeInTheDocument()
  expect(preview).toHaveBeenCalledTimes(2)
})

it.each([false, true])('allows copying an inherited preview with readOnly=%s without enabling edits', async (readOnly) => {
  vi.stubEnv('MODE', 'development')
  vi.spyOn(api, 'projectNotificationPreview').mockResolvedValue({ review_id: 'review-35', content: '已完成评审', will_send: true })
  render(<NotificationsPageConnected {...common} initial={{ ...common.initial, inherited: true }} readOnly={readOnly} scope="project" projectId="project-1" />)
  await screen.findByText('已完成评审')
  expect(screen.getByRole('button', { name: '复制通知内容' })).toBeEnabled()
  expect(screen.getByRole('textbox', { name: '群名称' })).toBeDisabled()
})

it('explains an invalid webhook before saving and keeps the entered draft', async () => {
  const onSave = vi.fn().mockResolvedValue(true)
  render(<NotificationsPageConnected {...common} scope="project" onSave={onSave} />)
  const webhook = screen.getByLabelText(/Webhook 地址/)
  await userEvent.type(webhook, 'invalid-webhook')
  await userEvent.click(screen.getByRole('button', { name: '保存项目企微配置' }))
  expect(screen.getByRole('alert')).toHaveTextContent('请输入有效的 HTTP 或 HTTPS Webhook 地址')
  expect(webhook).toHaveValue('invalid-webhook')
  expect(onSave).not.toHaveBeenCalled()
  await userEvent.clear(webhook)
  await userEvent.type(webhook, 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test')
  await userEvent.click(screen.getByRole('button', { name: '保存项目企微配置' }))
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
