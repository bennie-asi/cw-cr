import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { api, type BackendReview } from './api'
import { PROJECTS, REVIEWS } from './test-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })

it('returns from review detail to the previous platform history filter and scroll position', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: '评审历史' }))
  await user.selectOptions(screen.getByLabelText('按项目筛选评审'), 'demo-web')
  document.documentElement.scrollTop = 460
  await user.click(screen.getByText('!356 · demo-web'))
  expect(screen.getByRole('button', { name: '评审历史' })).toHaveAttribute('aria-current', 'page')
  await user.click(screen.getByRole('button', { name: '返回评审历史' }))
  expect(screen.getByLabelText('按项目筛选评审')).toHaveValue('demo-web')
  expect(screen.getByLabelText('按项目筛选评审')).not.toBeDisabled()
  expect(screen.queryByText('!357 · demo-api')).not.toBeInTheDocument()
  await waitFor(() => expect(document.documentElement.scrollTop).toBe(460))
})

it('restores the full history before applying scroll while the return refresh is delayed', async () => {
  vi.stubEnv('MODE', 'development')
  window.history.replaceState({}, '', '/platform/reviews')
  const rows = REVIEWS.slice(0, 2).map((row, index) => ({ ...row, id: `record-${index}`, projectId: PROJECTS[index].id }))
  const detail: BackendReview = { id: rows[0].id, project: PROJECTS[0].id, project_name: PROJECTS[0].name,
    mr_iid: 357, title: '历史返回测试', source: '手动触发', status: 'completed', comments: [] }
  let completeReturn!: (value: typeof rows) => void
  const delayedReturn = new Promise<typeof rows>(resolve => { completeReturn = resolve })
  let globalReads = 0
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'listReviews').mockImplementation(projectId => {
    if (projectId) return Promise.resolve(rows.slice(0, 1))
    return ++globalReads === 1 ? Promise.resolve(rows) : delayedReturn
  })
  vi.spyOn(api, 'getReview').mockResolvedValue(detail)
  for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig', 'getPolicy', 'projectModelConfig', 'projectNotificationConfig', 'mergeGate'] as const) vi.spyOn(api, name).mockResolvedValue({})
  const originalDescriptor = Object.getOwnPropertyDescriptor(document.documentElement, 'scrollTop')
  let actualScroll = 900
  Object.defineProperty(document.documentElement, 'scrollTop', {
    configurable: true, get: () => actualScroll,
    // Model browser clamping when a shortened list cannot fill the saved height.
    set: (value: number) => { actualScroll = Math.min(value, screen.queryAllByRole('button', { name: '查看记录' }).length > 1 ? 1000 : 0) },
  })
  try {
    render(<App />)
    await screen.findByText('!356 · demo-web')
    await userEvent.click(screen.getByText('!357 · demo-api'))
    await screen.findByRole('heading', { name: '!357 历史返回测试' })
    await waitFor(() => expect(api.getReview).toHaveBeenCalledTimes(2))
    await userEvent.click(screen.getByRole('button', { name: '返回评审历史' }))
    expect(screen.getByText('!356 · demo-web')).toBeInTheDocument()
    expect(document.documentElement.scrollTop).toBe(900)
    completeReturn(rows)
  } finally {
    if (originalDescriptor) Object.defineProperty(document.documentElement, 'scrollTop', originalDescriptor)
    else delete (document.documentElement as unknown as Record<string, unknown>).scrollTop
  }
})
