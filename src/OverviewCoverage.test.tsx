import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'
import type { MRCoverage } from './api'
import { OverviewCoverage } from './OverviewCoverage'

afterEach(() => vi.restoreAllMocks())
const complete: MRCoverage = { state: 'complete', open_mrs: 4, reviewed_mrs: 3, unreviewed_mrs: 1, percentage: 75, checked_at: '2026-09-30T00:00:00Z', repositories_checked: 2, repositories_total: 2, errors: [] }

it('reads the current-version coverage and opens projects', async () => {
  vi.spyOn(api, 'overviewCoverage').mockResolvedValue(complete)
  const open = vi.fn()
  render(<OverviewCoverage onOpenProjects={open} />)
  expect(await screen.findByText('75%')).toBeInTheDocument()
  expect(screen.getByText(/已评审 3 \/ 4/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /查看项目与 MR/ }))
  expect(open).toHaveBeenCalledOnce()
})

it('hides the global rate when a repository cannot be checked', async () => {
  vi.spyOn(api, 'overviewCoverage').mockResolvedValue({ ...complete, state: 'partial', percentage: null, repositories_checked: 1, errors: [{ path: 'team/api', detail: '连接不可用' }] })
  render(<OverviewCoverage onOpenProjects={vi.fn()} />)
  expect(await screen.findByText('覆盖核对未完成')).toBeInTheDocument()
  expect(screen.queryByText('75%')).not.toBeInTheDocument()
  expect(screen.getByText(/team\/api.*连接不可用/)).toBeInTheDocument()
})

it('offers a retry after failure and reports an empty open-MR scope without a fake percentage', async () => {
  const request = vi.spyOn(api, 'overviewCoverage').mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ ...complete, open_mrs: 0, reviewed_mrs: 0, unreviewed_mrs: 0, percentage: null })
  render(<OverviewCoverage onOpenProjects={vi.fn()} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('覆盖数据暂不可用')
  await userEvent.click(screen.getByRole('button', { name: '刷新覆盖' }))
  expect(await screen.findByText('暂无开放 MR')).toBeInTheDocument()
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  expect(screen.queryByText('100%')).not.toBeInTheDocument()
})
