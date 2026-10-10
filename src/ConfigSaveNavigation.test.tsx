import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { api } from './api'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })

const configs = [
  { page: 'models', field: '模型 ID', save: '保存模型配置', nav: '模型与引擎', method: 'updateModelConfig', key: 'model', success: '模型配置已保存', failure: '模型配置保存失败，请检查后端服务' },
  { page: 'notifications', field: '群名称', save: '保存通知配置', nav: '通知配置', method: 'updateNotificationConfig', key: 'name', success: '企微通知配置已保存', failure: '企微通知配置保存失败，请检查后端服务' },
  { page: 'system', field: 'GitLab 地址', save: '保存 GitLab 配置', nav: '系统设置', method: 'updateGitlabConfig', key: 'base_url', success: 'GitLab 连接配置已保存', failure: 'GitLab 连接配置保存失败，请检查后端服务' },
] as const

for (const config of configs) {
  it.each(['navigate-success', 'navigate-failure', 'refresh-success', 'refresh-failure'])(`${config.page} ignores obsolete save after %s`, async scenario => {
    vi.stubEnv('MODE', 'development')
    window.history.replaceState({}, '', `/platform/settings/${config.page}`)
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    vi.spyOn(api, 'listReviews').mockResolvedValue([])
    for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig'] as const) vi.spyOn(api, name).mockResolvedValue({})
    let resolve!: (value: Record<string, unknown>) => void
    let reject!: (reason: Error) => void
    vi.spyOn(api, config.method).mockImplementation(() => new Promise((done, fail) => { resolve = done; reject = fail }))
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('Django 后端已连接')
    await user.type(await screen.findByLabelText(config.field), 'old-draft')
    await user.click(screen.getByRole('button', { name: config.save }))
    await user.click(screen.getByRole('button', { name: scenario.startsWith('navigate') ? '项目管理' : '刷新数据' }))
    await user.click(screen.getByRole('button', { name: '放弃修改并离开' }))
    if (scenario.startsWith('navigate')) await user.click(screen.getByRole('button', { name: config.nav }))
    await user.type(await screen.findByLabelText(config.field), 'new-draft')
    await act(async () => {
      if (scenario.endsWith('success')) resolve({ [config.key]: 'old-draft' })
      else reject(new Error('offline'))
    })
    expect(screen.getByLabelText(config.field)).toHaveValue('new-draft')
    expect(screen.queryByText(config.success)).not.toBeInTheDocument()
    expect(screen.queryByText(config.failure)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '项目管理' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('未保存的修改')
  })
}
