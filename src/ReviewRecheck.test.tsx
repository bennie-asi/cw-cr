import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { api, type BackendReview } from './api'
import { PROJECTS } from './test-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs() })

function setup(recheck: Record<string, unknown>, blockers = 0) {
  vi.stubEnv('MODE', 'development')
  const review = { id: 'rechecked', project: PROJECTS[0].id, project_name: PROJECTS[0].name,
    mr_iid: 357, title: '复审记录', source: '/review', status: blockers ? 'blocked' : 'completed',
    blocking_comment_count: blockers, comments: [], summary: '已检查当前变更。', recheck_result: recheck } as BackendReview
  window.history.replaceState({}, '', `/projects/${review.project}/reviews/${review.id}`)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok' })
  vi.spyOn(api, 'listProjects').mockResolvedValue(PROJECTS)
  vi.spyOn(api, 'listReviews').mockResolvedValue([])
  vi.spyOn(api, 'getReview').mockResolvedValue(review)
  for (const name of ['overview', 'systemStatus', 'modelConfig', 'gitlabConfig', 'notificationConfig', 'getPolicy', 'projectModelConfig', 'projectNotificationConfig', 'mergeGate'] as const) vi.spyOn(api, name).mockResolvedValue({})
  render(<App />)
}

it('shows server-confirmed resolution and allows opening the resolved issue evidence', async () => {
  setup({ applied: true, new: 0, repeated: 0, resolved: 1, retained: 0,
    resolved_issues: [{ issue_id: 'fixed', title: '缺失输入导致异常', file_path: 'src/api.py', level: 'P1' }] })
  expect(await screen.findByText('复审已解决 1 条')).toBeInTheDocument()
  await userEvent.click(screen.getByText('查看本轮解决的问题'))
  expect(screen.getByText('缺失输入导致异常')).toBeInTheDocument()
  expect(screen.getByText('src/api.py')).toBeInTheDocument()
})

it('explains retained blockers without claiming they were fixed', async () => {
  setup({ applied: true, new: 0, repeated: 1, resolved: 0, retained: 2 }, 2)
  expect(await screen.findByText('保留待核验 2 条')).toBeInTheDocument()
  expect(screen.getByText(/完整复审确认修复后才会解除阻断/)).toBeInTheDocument()
  expect(screen.queryByText('复审已解决 1 条')).not.toBeInTheDocument()
  expect(screen.getByText('复用原讨论 1 条')).toBeInTheDocument()
})

it('does not manufacture a reconciliation result for old review records', async () => {
  setup({})
  await screen.findByRole('heading', { name: '!357 复审记录' })
  expect(screen.queryByText('本轮问题变化')).not.toBeInTheDocument()
})
