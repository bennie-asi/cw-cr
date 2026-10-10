import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { api, mapComment, mapReview, type BackendReview, type TriggerResponse } from './api'
import AuthenticatedApp from './App'
import { PROJECTS } from './test-fixtures'

const original: BackendReview = { id: 'original', project: PROJECTS[0].id, project_name: PROJECTS[0].name, mr_iid: 357, title: '原始评审', source: '手动触发', status: 'completed', comments: [], summary: '原总评' }
const newer: BackendReview = { ...original, id: 'new-job', title: '新任务结果' }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })
function setup() {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', `/projects/${original.project}/reviews/${original.id}`)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'listReviews').mockResolvedValue([])
  vi.spyOn(api, 'getReview').mockImplementation(id => Promise.resolve(id === newer.id ? newer : original))
  for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig', 'getPolicy', 'projectModelConfig', 'projectNotificationConfig', 'mergeGate'] as const) vi.spyOn(api, name).mockResolvedValue({})
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

it('uses verified file lines in navigation, selected diff row and the explanation', async () => {
  setup()
  const source = 'assert first_session.request.call_args[1]["headers"]["X-Csrftoken"] is None'
  vi.mocked(api.getReview).mockResolvedValue({ ...original, changed_files: ['test_login.py'],
    diff_text: 'diff --git a/test_login.py b/test_login.py\n@@ -147 +157,3 @@\n-old\n+assert result == expected\n+assert flush.called\n+' + source,
    comments: [{ id: 'csrf', file_path: 'test_login.py', new_line: 157, title: 'CSRF 断言错误', body: '`' + source + '` 需核对', level: 'P1', status: 'published',
      location: { status: 'relocated', new_line: 159, old_line: null, original_new_line: 157, original_old_line: null, reason: '已按原代码定位' } }],
  })
  const { container } = render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '查看问题：CSRF 断言错误' }))
  await waitFor(() => expect(container.querySelector('.code-comment-active code')).toHaveTextContent(source))
  expect([...container.querySelectorAll('.code-comment-active .line-no')].map(cell => cell.textContent)).toEqual(['', '159'])
  expect(container.querySelector('.reader-issue-choice small')).toHaveTextContent('test_login.py:159')
  expect(container.querySelector('.comment-location')).toHaveTextContent('test_login.py:159')
  expect(screen.getByText(/GitLab 原发布位置：157/)).toBeInTheDocument()
})

it('shows actual backend blockers in the summary panel separately from model prose', async () => {
  setup()
  vi.mocked(api.getReview).mockResolvedValue({ ...original, status: 'blocked', blocking_comment_count: 1,
    gate_reason: '还有 1 个 P0/P1 行级评论未解决', summary: '未发现阻断级问题。', notification_state: 'done' })
  render(<App />)
  await screen.findByRole('heading', { name: '!357 原始评审' })
  await userEvent.setup().click(screen.getByRole('button', { name: '中文总评' }))
  expect(screen.getByText('1 条未解决阻断')).toBeInTheDocument()
  expect(screen.getByText('需要修改')).toBeInTheDocument()
  expect(screen.getByText('还有 1 个 P0/P1 行级评论未解决')).toBeInTheDocument()
  expect(screen.getByText(/模型说明不参与门禁判定/)).toBeInTheDocument()
})

it('serializes review triggers from detail and disables both actions while pending', async () => {
  setup()
  const pending = deferred<TriggerResponse>()
  vi.spyOn(api, 'triggerReview').mockReturnValue(pending.promise)
  render(<App />)
  await userEvent.dblClick(await screen.findByRole('button', { name: '/review 重新审查' }))
  expect(api.triggerReview).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: /提交中/ })).toBeDisabled()
  expect(screen.getByRole('button', { name: '/improve 生成建议' })).toBeDisabled()
  await act(async () => pending.reject(new Error('failed')))
  expect(screen.getByRole('button', { name: '/review 重新审查' })).toBeEnabled()
})

it.each(['queued', 'running'])('does not trigger another task from a %s detail', async status => {
  setup()
  vi.mocked(api.getReview).mockResolvedValue({ ...original, status })
  vi.spyOn(api, 'triggerReview')
  render(<App />)
  const trigger = await screen.findByRole('button', { name: '/review 重新审查' })
  expect(trigger).toBeDisabled()
  expect(screen.getByRole('button', { name: '/improve 生成建议' })).toBeDisabled()
  await userEvent.click(trigger)
  expect(api.triggerReview).not.toHaveBeenCalled()
})

it('keeps both detail actions disabled after an unlinked task is queued and while polling', async () => {
  setup()
  let tick!: () => Promise<void>
  const realInterval = window.setInterval.bind(window)
  vi.spyOn(window, 'setInterval').mockImplementation(((callback: () => Promise<void>, delay?: number) => {
    if (delay === 5000) { tick = callback; return 987 }
    return realInterval(callback, delay)
  }) as typeof window.setInterval)
  vi.spyOn(api, 'triggerReview').mockResolvedValue({ job_id: 'job', job_status: 'queued', review: null })
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '/review 重新审查' }))
  await screen.findByText('已触发 /review，评审任务进入队列')
  expect(screen.getByRole('button', { name: '/review 重新审查' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '/improve 生成建议' })).toBeDisabled()
  await waitFor(() => expect(tick).toBeDefined())
  vi.mocked(api.getReview).mockResolvedValue({ ...original, execution_state: 'running' })
  await act(async () => { await tick() })
  expect(screen.getByRole('button', { name: '/review 重新审查' })).toBeDisabled()
  vi.mocked(api.getReview).mockResolvedValue({ ...original, execution_state: 'succeeded' })
  await act(async () => { await tick() })
  expect(screen.getByRole('button', { name: '/review 重新审查' })).toBeEnabled()
  expect(api.triggerReview).toHaveBeenCalledTimes(1)
})

it('discards a polling response started before a new task was submitted', async () => {
  setup()
  let tick!: () => Promise<void>
  const realInterval = window.setInterval.bind(window)
  vi.spyOn(window, 'setInterval').mockImplementation(((callback: () => Promise<void>, delay?: number) => {
    if (delay === 5000) { tick = callback; return 987 }
    return realInterval(callback, delay)
  }) as typeof window.setInterval)
  const previousRead = deferred<BackendReview>()
  vi.spyOn(api, 'triggerReview').mockResolvedValue({ job_id: 'job', job_status: 'queued', review: null })
  render(<App />)
  await screen.findByRole('heading', { name: '!357 原始评审' })
  await waitFor(() => expect(tick).toBeDefined())
  vi.mocked(api.getReview).mockReturnValueOnce(previousRead.promise)
  let polling!: Promise<void>
  act(() => { polling = tick() })
  await waitFor(() => expect(api.getReview).toHaveBeenCalledTimes(2))
  await userEvent.click(screen.getByRole('button', { name: '/review 重新审查' }))
  await screen.findByText('已触发 /review，评审任务进入队列')
  await act(async () => {
    previousRead.resolve({ ...original, execution_state: 'succeeded' })
    await polling
  })
  expect(screen.getByRole('button', { name: '/review 重新审查' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '/improve 生成建议' })).toBeDisabled()
})

it('moves the URL with the newly triggered review so refresh opens the same record', async () => {
  setup()
  vi.spyOn(api, 'triggerReview').mockResolvedValue({ job_id: 'job', job_status: 'queued', review: newer })
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '/review 重新审查' }))
  await screen.findByRole('heading', { name: '!357 新任务结果' })
  expect(window.location.pathname).toBe(`/projects/${original.project}/reviews/new-job`)
})

it.each(['success', 'failure'] as const)('ignores trigger %s after leaving and returning to the same review', async outcome => {
  setup()
  const pending = deferred<TriggerResponse>()
  vi.spyOn(api, 'triggerReview').mockReturnValue(pending.promise)
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '/review 重新审查' }))
  await userEvent.click(screen.getByRole('button', { name: '项目策略' }))
  await act(async () => {
    window.history.replaceState({}, '', `/projects/${original.project}/reviews/${original.id}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await screen.findByRole('heading', { name: '!357 原始评审' })
  await act(async () => {
    if (outcome === 'success') pending.resolve({ job_id: 'job', job_status: 'queued', review: newer })
    else pending.reject(new Error('late failure'))
  })
  expect(screen.getByRole('heading', { name: '!357 原始评审' })).toBeInTheDocument()
  expect(screen.queryByText(/已触发 \/review|触发评审失败/)).not.toBeInTheDocument()
  expect(window.location.pathname).toBe(`/projects/${original.project}/reviews/${original.id}`)
})

it('ignores a saved summary response after navigating to another review', async () => {
  setup()
  const pending = deferred<BackendReview>()
  vi.spyOn(api, 'updateSummary').mockReturnValue(pending.promise)
  render(<App />)
  await screen.findByRole('heading', { name: '!357 原始评审' })
  await userEvent.click(screen.getByRole('button', { name: '中文总评' }))
  await userEvent.click(screen.getByRole('button', { name: '保存并同步' }))
  await act(async () => {
    window.history.replaceState({}, '', `/projects/${original.project}/reviews/${newer.id}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await screen.findByRole('heading', { name: '!357 新任务结果' })
  await act(async () => pending.resolve({ ...original, summary: '旧页面保存结果' }))
  expect(screen.getByRole('heading', { name: '!357 新任务结果' })).toBeInTheDocument()
  expect(screen.queryByText('中文总评已保存并同步')).not.toBeInTheDocument()
})

it('keeps retry on history without silently selecting a detail record', async () => {
  setup()
  window.history.replaceState({}, '', `/projects/${original.project}/reviews`)
  vi.mocked(api.listReviews).mockResolvedValue([mapReview({ ...original, status: 'failed' })])
  vi.spyOn(api, 'retryReview').mockResolvedValue({ job_id: 'job', job_status: 'queued', review: { ...original, status: 'queued' } })
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '重试失败阶段' }))
  await screen.findByText('已重新加入评审队列')
  expect(window.location.pathname).toBe(`/projects/${original.project}/reviews`)
  expect(api.getReview).not.toHaveBeenCalled()
  expect(screen.getByText('排队中')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重试失败阶段' })).not.toBeInTheDocument()
})

it.each(['success', 'failure'] as const)('ignores retry %s after switching projects', async outcome => {
  setup()
  window.history.replaceState({}, '', `/projects/${original.project}/reviews`)
  vi.mocked(api.listReviews).mockImplementation(id => Promise.resolve(id === original.project ? [mapReview({ ...original, status: 'failed' })] : []))
  const pending = deferred<TriggerResponse>()
  vi.spyOn(api, 'retryReview').mockReturnValue(pending.promise)
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '重试失败阶段' }))
  await userEvent.click(screen.getByLabelText('切换项目'))
  await userEvent.click(screen.getByRole('button', { name: PROJECTS[1].name }))
  await screen.findByText('没有符合条件的评审')
  await act(async () => {
    if (outcome === 'success') pending.resolve({ job_id: 'job', job_status: 'queued', review: original })
    else pending.reject(new Error('late error'))
  })
  expect(api.getReview).not.toHaveBeenCalled()
  expect(screen.queryByText(/已重新加入评审队列|重新评审失败/)).not.toBeInTheDocument()
  expect(window.location.pathname).toBe(`/projects/${PROJECTS[1].id}/reviews`)
})

it.each(['publishComment', 'resolveComment'] as const)('ignores late %s feedback after navigation', async operation => {
  setup()
  const comment = { id: 'line-note', file_path: 'a.py', new_line: 1, level: 'P1' as const, title: '测试建议', body: '说明', status: 'pending', status_label: '待发布' as const }
  vi.mocked(api.getReview).mockResolvedValue({ ...original, comments: [comment] })
  const pending = deferred<ReturnType<typeof mapComment>>()
  vi.spyOn(api, operation).mockReturnValue(pending.promise)
  render(<App />)
  await screen.findByRole('heading', { name: '!357 原始评审' })
  await userEvent.click(screen.getByRole('button', { name: '行级评论 1' }))
  await userEvent.click(screen.getByRole('button', { name: operation === 'publishComment' ? '发布到 GitLab' : '标记已解决' }))
  await userEvent.click(screen.getByRole('button', { name: '评审历史' }))
  await act(async () => pending.resolve(mapComment({ ...comment, status: 'resolved', status_label: '已解决' })))
  expect(screen.queryByText(/行级评论已发布到 GitLab Discussion|已标记为解决，门禁状态将重新计算/)).not.toBeInTheDocument()
})

it.each(['success', 'failure'] as const)('ignores late manual notification %s and avoids global refresh', async outcome => {
  setup()
  const pending = deferred<Record<string, unknown>>()
  vi.spyOn(api, 'notifyReview').mockReturnValue(pending.promise)
  render(<App />)
  await screen.findByRole('heading', { name: '!357 原始评审' })
  await userEvent.click(screen.getByRole('button', { name: '中文总评' }))
  await userEvent.click(screen.getByRole('button', { name: '发送到企业微信' }))
  await userEvent.click(screen.getByRole('button', { name: '评审历史' }))
  vi.mocked(api.systemStatus).mockClear()
  await act(async () => {
    if (outcome === 'success') pending.resolve({ ok: true })
    else pending.reject(new Error('late error'))
  })
  expect(screen.queryByText(/企业微信已接收通知|企业微信通知发送失败/)).not.toBeInTheDocument()
  expect(api.systemStatus).not.toHaveBeenCalled()
})

it('does not request platform administration data after a member sends a review notification', async () => {
  setup()
  vi.spyOn(api, 'notifyReview').mockResolvedValue({ ok: true })
  render(<AuthenticatedApp user={{ id: 2, username: 'member', display_name: '成员', platform_role: 'member' }} onLogout={vi.fn()} />)
  await screen.findByRole('heading', { name: '!357 原始评审' })
  await userEvent.click(screen.getByRole('button', { name: '中文总评' }))
  await userEvent.click(screen.getByRole('button', { name: '发送到企业微信' }))
  await screen.findByText('企业微信已接收通知')
  expect(api.systemStatus).not.toHaveBeenCalled()
})

it('rejects a different review returned when hydrating a deep link', async () => {
  setup()
  vi.mocked(api.getReview).mockResolvedValue(newer)
  render(<App />)
  expect(await screen.findByText('页面不存在或项目不可访问')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: '!357 新任务结果' })).not.toBeInTheDocument()
})

it.each(['review', 'project'] as const)('does not replace detail with a polling response for another %s', async wrong => {
  setup()
  let tick!: () => Promise<void>
  const realInterval = window.setInterval.bind(window)
  vi.spyOn(window, 'setInterval').mockImplementation(((callback: () => Promise<void>, delay?: number) => {
    if (delay === 5000) { tick = callback; return 987 }
    return realInterval(callback, delay)
  }) as typeof window.setInterval)
  render(<App />)
  await screen.findByRole('heading', { name: '!357 原始评审' })
  await waitFor(() => expect(tick).toBeDefined())
  vi.mocked(api.getReview).mockResolvedValue(wrong === 'review' ? newer : { ...original, project: PROJECTS[1].id, title: '错误项目详情' })
  await act(async () => { await tick() })
  expect(screen.getByRole('heading', { name: '!357 原始评审' })).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: /新任务结果|错误项目详情/ })).not.toBeInTheDocument()
})

it('discards a detail load from before leaving and reopening through browser navigation', async () => {
  setup()
  window.history.replaceState({}, '', `/projects/${original.project}/reviews`)
  vi.mocked(api.listReviews).mockResolvedValue([mapReview(original)])
  const pending = deferred<BackendReview>()
  vi.mocked(api.getReview).mockImplementation(id => id === original.id ? pending.promise : Promise.resolve(newer))
  render(<App />)
  await userEvent.click(await screen.findByText(`!357 · ${PROJECTS[0].name}`))
  await userEvent.click(screen.getByRole('button', { name: '项目策略' }))
  await act(async () => {
    window.history.replaceState({}, '', `/projects/${original.project}/reviews/${newer.id}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await screen.findByRole('heading', { name: '!357 新任务结果' })
  await act(async () => pending.resolve(original))
  expect(screen.getByRole('heading', { name: '!357 新任务结果' })).toBeInTheDocument()
})
