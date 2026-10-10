import { act, render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { diffForFile, ModelsPageConnected, OverviewPage } from './App'
import App from './test-app'
import { api } from './api'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })

describe('previously disconnected controls', () => {
  it('uses the backend severe issue count rather than the historical blocker count', () => {
    render(<OverviewPage stats={{ recent_severe_count: 2, reviews: 1, blockers: 3 }} onNavigate={vi.fn()} onOpenReview={vi.fn()} reviews={[]} projects={[]} unresolvedBlockers={3} />)
    expect(within(screen.getByRole('region', { name: '近七天严重问题' })).getByText('2')).toBeInTheDocument()
  })
  it('polls review status without reloading configuration forms', async () => {
    vi.stubEnv('MODE', 'development')
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    const reviews = vi.spyOn(api, 'listReviews').mockResolvedValue([])
    for (const key of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig'] as const) vi.spyOn(api, key).mockResolvedValue({})
    let tick: (() => void) | undefined
    const realSetInterval = window.setInterval.bind(window)
    vi.spyOn(window, 'setInterval').mockImplementation(((callback: () => void, delay?: number) => {
      // Testing Library also uses intervals; capture only the application's poll.
      if (delay === 5000) { tick = callback; return 123 }
      return realSetInterval(callback, delay)
    }) as typeof window.setInterval)
    render(<App />)
    await screen.findByText('Django 后端已连接')
    await waitFor(() => expect(tick).toBeDefined())
    await act(async () => { tick!() })
    await waitFor(() => expect(reviews).toHaveBeenCalledTimes(2))
    expect(api.modelConfig).toHaveBeenCalledTimes(1)
  })
  it('does not label an inherited model as overridden just by opening project scope', () => {
    render(<ModelsPageConnected initial={{ inherited: true, model: 'inherited-model' }} modelOptions={[]} scope="project" projectName="audit" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={vi.fn()} />)
    expect(screen.getByText('当前继承全局；保存后才覆盖 audit')).toBeInTheDocument()
    expect(screen.queryByText('项目覆盖', { exact: true })).not.toBeInTheDocument()
  })
  it('keeps other files out of the selected diff', () => {
    const diff = 'diff --git a/a.py b/a.py\n+++ b/a.py\n+one\ndiff --git a/b.py b/b.py\n+++ b/b.py\n+two'
    expect(diffForFile(diff, 'b.py')).toContain('+two')
    expect(diffForFile(diff, 'b.py')).not.toContain('+one')
  })
  it('opens both user menus with truthful access information', async () => {
    const user = userEvent.setup(); render(<App />)
    for (const label of ['用户菜单', '底部用户菜单']) {
      const trigger = screen.getByLabelText(label)
      await user.click(trigger)
      const menu = screen.getByRole('dialog', { name: `${label}选项` })
      expect(await within(menu).findByText('test-admin')).toBeInTheDocument()
      expect(within(menu).getByText('平台管理员')).toBeInTheDocument()
      await user.click(within(menu).getByRole('button', { name: '修改密码' }))
      expect(screen.getByRole('dialog', { name: '修改密码' })).toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: `${label}选项` })).not.toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '取消' }))
    }
  })

  it('switches project context and opens project-specific settings through the row menu', async () => {
    const user = userEvent.setup(); render(<App />)
    await user.click(screen.getByLabelText('切换项目'))
    await user.click(await screen.findByRole('button', { name: '管理项目' }))
    await user.click(screen.getByLabelText('demo-api 更多操作'))
    await user.click(await screen.findByRole('button', { name: '模型配置' }))
    expect(screen.getByLabelText('模型配置范围')).toHaveTextContent('项目配置 · demo-api')
    expect(screen.getByRole('heading', { name: 'demo-api · 项目模型覆盖' })).toBeInTheDocument()
  })

  it('searches and navigates to a matching review', async () => {
    const user = userEvent.setup(); render(<App />)
    await user.type(screen.getByLabelText('全局搜索'), '!357')
    await user.click(screen.getByRole('button', { name: '!357 · demo-api' }))
    expect(screen.getByRole('heading', { level: 1, name: 'MR 行级审查详情' })).toBeInTheDocument()
    expect(screen.getByLabelText('全局搜索')).toHaveValue('')
  })

  it('sends the unsaved model form via the production request path', async () => {
    vi.stubEnv('MODE', 'development')
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    vi.spyOn(api, 'listReviews').mockResolvedValue([])
    for (const key of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig'] as const) vi.spyOn(api, key).mockResolvedValue({})
    const request = vi.spyOn(api, 'testModelConfig').mockResolvedValue({ ok: true, model_ids: ['review-model'] })
    const user = userEvent.setup(); render(<App />)
    await screen.findByText('Django 后端已连接')
    await user.click(screen.getByRole('button', { name: '模型与引擎' }))
    await user.type(screen.getByLabelText('Base URL'), 'http://localhost:9999/v1')
    await user.type(screen.getByLabelText('API Key', { exact: false }), 'test-only-key')
    await user.click(screen.getByRole('button', { name: '测试连接' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({ base_url: 'http://localhost:9999/v1', api_key: 'test-only-key' })))
    expect(await screen.findByRole('option', { name: 'review-model' })).toBeInTheDocument()
  })

  it('explains an empty CCSwitch catalog while keeping the saved model selectable', async () => {
    vi.stubEnv('MODE', 'development')
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    vi.spyOn(api, 'listReviews').mockResolvedValue([])
    for (const key of ['overview', 'systemStatus', 'gitlabConfig', 'notificationConfig'] as const) vi.spyOn(api, key).mockResolvedValue({})
    vi.spyOn(api, 'modelConfig').mockResolvedValue({ base_url: 'http://127.0.0.1:15721/v1', model: 'review-model', api_key_masked: '••••' })
    const request = vi.spyOn(api, 'testModelConfig').mockResolvedValue({ ok: true, model_ids: [] })
    const user = userEvent.setup(); render(<App />)

    await screen.findByText('Django 后端已连接')
    await user.click(screen.getByRole('button', { name: '模型与引擎' }))
    await user.click(screen.getByRole('button', { name: '测试连接' }))

    await waitFor(() => expect(request).toHaveBeenCalled())
    expect(await screen.findByText('当前模型网关已连通，但未返回模型列表，可直接填写模型 ID')).toBeInTheDocument()
    expect(screen.getByText('网关已连通 · 未返回模型目录，可手动填写模型 ID')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /review-model/ })).toBeInTheDocument()
  })
})
