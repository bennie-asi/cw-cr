import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { api } from './api'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })

it('waits for the real API result before clearing the GitLab token draft', async () => {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', '/platform/settings/system')
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue([])
  vi.spyOn(api, 'listReviews').mockResolvedValue([])
  for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig'] as const) vi.spyOn(api, name).mockResolvedValue({})
  let reject!: (reason: Error) => void
  vi.spyOn(api, 'updateGitlabConfig').mockImplementation(() => new Promise((_resolve, fail) => { reject = fail }))
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('Django 后端已连接')
  await user.type(await screen.findByLabelText('GitLab API Token'), 'test-token')
  await user.click(screen.getByRole('button', { name: '保存 GitLab 配置' }))
  expect(screen.getByRole('button', { name: '正在保存 GitLab 配置…' })).toBeDisabled()
  await act(async () => reject(new Error('offline')))
  expect(screen.getByRole('alert')).toHaveTextContent('输入内容已保留')
  expect(screen.getByLabelText('GitLab API Token')).toHaveValue('test-token')
})
