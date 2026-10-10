import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'
import { MergeRequests } from './MergeRequests'

afterEach(() => vi.restoreAllMocks())
it('loads remote MRs and triggers the selected project and MR', async () => {
  vi.spyOn(api, 'listMergeRequests').mockResolvedValue({ results: [{ iid: 357, title: 'Remote MR', source_branch: 'feature', target_branch: 'main', web_url: 'https://gitlab.example/mr/357' }], has_next: false })
  const trigger = vi.spyOn(api, 'triggerReview').mockResolvedValue({ job_id: 'job', job_status: 'queued', review: null })
  const refresh = vi.fn(); const user = userEvent.setup()
  render(<MergeRequests projectId="real-project-id" onReviewed={refresh} />)
  const link = await screen.findByRole('link', { name: '!357 · Remote MR' })
  await user.click(within(link.closest('tr')!).getByRole('button', { name: '/review' }))
  await waitFor(() => expect(trigger).toHaveBeenCalledWith('real-project-id', { mr_iid: 357, mode: 'review' }))
  expect(refresh).toHaveBeenCalledOnce()
})
it('shows loading errors instead of implying there are no MRs', async () => {
  vi.spyOn(api, 'listMergeRequests').mockRejectedValue(new Error('offline'))
  render(<MergeRequests projectId="project" onReviewed={() => {}} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('无法加载 MR')
  expect(screen.queryByText('当前没有待合并 MR。')).not.toBeInTheDocument()
})

it('can select a repository, list merged MRs, and submit a review for the selected repository', async () => {
  const list = vi.spyOn(api, 'listMergeRequests').mockResolvedValue({
    results: [{ iid: 7, title: 'Merged change', state: 'merged', source_branch: 'feature', target_branch: 'main', web_url: 'https://gitlab.example/mr/7' }],
    has_next: false,
  })
  const trigger = vi.spyOn(api, 'triggerReview').mockResolvedValue({ job_id: 'job', job_status: 'queued', review: null })
  const user = userEvent.setup()
  render(<MergeRequests projectId="project" repositories={[{ path: 'team/api' }, { path: 'team/ui' }]} onReviewed={() => {}} />)
  await user.selectOptions(screen.getByRole('combobox', { name: 'GitLab 仓库' }), 'team/ui')
  await user.click(screen.getByRole('button', { name: '已合并' }))
  expect(await screen.findByRole('link', { name: '!7 · Merged change' })).toBeInTheDocument()
  expect(list).toHaveBeenCalledWith('project', 1, 'team/ui', 'merged')
  await user.click(screen.getByRole('button', { name: '/review' }))
  await waitFor(() => expect(trigger).toHaveBeenCalledWith('project', { mr_iid: 7, mode: 'review', repository_path: 'team/ui' }))
})

it('distinguishes an unreviewed MR from an obsolete review and prevents duplicate active work', async () => {
  const base = { source_branch: 'feature', target_branch: 'main', web_url: '' }
  vi.spyOn(api, 'listMergeRequests').mockResolvedValue({ results: [
    { ...base, iid: 1, title: 'New', review_state: 'unreviewed', issue_count: null },
    { ...base, iid: 2, title: 'Old', review_state: 'stale', review_sha: 'abc123', issue_count: null },
    { ...base, iid: 3, title: 'Busy', review_state: 'running', issue_count: null },
  ], has_next: false })
  render(<MergeRequests projectId="p" onReviewed={vi.fn()} />)
  await screen.findByText(/!1 · New/)
  expect(screen.getByText('未评审')).toBeInTheDocument()
  expect(screen.getByText('已有新提交 · 需重新评审')).toBeInTheDocument()
  const busyRow = screen.getByText(/!3 · Busy/).closest('tr')!
  expect(within(busyRow).getByRole('button', { name: '/review' })).toBeDisabled()
  expect(within(busyRow).getByRole('button', { name: '/improve' })).toBeDisabled()
  expect(screen.queryByText('0 个问题')).not.toBeInTheDocument()
})

it('locks a submission immediately and ignores completion after switching repositories', async () => {
  vi.spyOn(api, 'listMergeRequests').mockResolvedValue({ results: [{ iid: 1, title: 'Change', source_branch: 'f', target_branch: 'main', web_url: '' }], has_next: false })
  let finish!: (value: Awaited<ReturnType<typeof api.triggerReview>>) => void
  const trigger = vi.spyOn(api, 'triggerReview').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const reviewed = vi.fn()
  render(<MergeRequests projectId="p" repositories={[{ path: 't/a' }, { path: 't/b' }]} onReviewed={reviewed} />)
  const button = await screen.findByRole('button', { name: '/review' })
  act(() => { fireEvent.click(button); fireEvent.click(button) })
  expect(trigger).toHaveBeenCalledTimes(1)
  await userEvent.selectOptions(screen.getByLabelText('GitLab 仓库'), 't/b')
  await act(async () => { finish({ job_id: 'old', job_status: 'queued', review: null }) })
  expect(reviewed).not.toHaveBeenCalled()
  expect(screen.queryByText(/评审任务已提交/)).not.toBeInTheDocument()
})
