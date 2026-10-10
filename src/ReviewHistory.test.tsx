import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { ReviewHistory } from './ReviewHistory'
import { REVIEWS } from './test-fixtures'

it.each(['failed', 'pending', 'unknown'])('does not declare no blockers while reconciliation/publication is %s', publicationState => {
  render(<ReviewHistory reviews={[{ ...REVIEWS[0], status: '失败', blockers: 0, comments: 0,
    generationState: 'completed', publicationState }]} projects={[]} activeProjectId={null}
    filters={{ project: '', source: '全部', exception: 'all' }} onFiltersChange={vi.fn()}
    onRefresh={vi.fn()} onRetry={vi.fn()} onOpenDetail={vi.fn()} />)
  expect(screen.getByText('结果待核验')).toBeInTheDocument()
  expect(screen.queryByText('无阻断')).not.toBeInTheDocument()
})

it('separates generation, publication, and uncertain notification without allowing a blind resend', () => {
  render(<ReviewHistory reviews={[{ ...REVIEWS[0], generationState: 'completed', publicationState: 'failed', notificationState: 'unknown' }]} projects={[]} activeProjectId={null} filters={{ project: '', source: '全部', exception: 'all' }} onFiltersChange={vi.fn()} onRefresh={vi.fn()} onRetry={vi.fn()} onOpenDetail={vi.fn()} />)
  expect(screen.getByRole('columnheader', { name: '生成' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '发布' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '通知' })).toBeInTheDocument()
  expect(screen.getByText('发布失败', { selector: 'span' })).toBeInTheDocument()
  expect(screen.getByText('结果待确认')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /发送通知/ })).not.toBeInTheDocument()
})

it('filters failures by their actual stage and opens details with a keyboard-accessible control', async () => {
  const open = vi.fn()
  const failed = { ...REVIEWS[0], publicationState: 'failed' }
  render(<ReviewHistory reviews={[failed, { ...REVIEWS[1], publicationState: 'completed' }]} projects={[]} activeProjectId={null} filters={{ project: '', source: '全部', exception: 'publication' }} onFiltersChange={vi.fn()} onRefresh={vi.fn()} onRetry={vi.fn()} onOpenDetail={open} />)
  const row = screen.getByText(`${failed.mr} · ${failed.project}`).closest('tr')!
  expect(screen.queryByText(`${REVIEWS[1].mr} · ${REVIEWS[1].project}`)).not.toBeInTheDocument()
  await userEvent.click(within(row).getByRole('button', { name: '查看记录' }))
  expect(open).toHaveBeenCalledWith(failed.id)
})

it('prevents a repeated retry while its request is pending', async () => {
  let resolve!: () => void
  const retry = vi.fn(() => new Promise<void>(done => { resolve = done }))
  render(<ReviewHistory reviews={[{ ...REVIEWS[0], publicationState: 'failed' }]} projects={[]} activeProjectId={null} filters={{ project: '', source: '全部', exception: 'all' }} onFiltersChange={vi.fn()} onRefresh={vi.fn()} onRetry={retry} onOpenDetail={vi.fn()} />)
  await userEvent.dblClick(screen.getByRole('button', { name: '重试失败阶段' }))
  expect(retry).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: '正在重新排队…' })).toBeDisabled()
  resolve()
  await screen.findByRole('button', { name: '重试失败阶段' })
})
