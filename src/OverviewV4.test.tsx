import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { OverviewPage } from './App'
import { PROJECTS, REVIEWS } from './test-fixtures'

it('prioritizes recent severe issues and opens their review', async () => {
  const openReview = vi.fn()
  render(<OverviewPage stats={{ recent_severe_count: 1, recent_severe_issues: [{ id: 'comment-1', review_id: 'review-357', level: 'P1', title: '订单接口缺少权限校验', project: '交易服务', mr_iid: 128, created_at: '2026-09-29T01:00:00Z' }] }} onNavigate={vi.fn()} onOpenReview={openReview} reviews={REVIEWS} projects={PROJECTS} unresolvedBlockers={0} />)
  expect(screen.getByRole('heading', { name: '需要关注的严重问题' })).toBeInTheDocument()
  expect(screen.getByText('近 7 天 · 新发现严重问题')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /订单接口缺少权限校验/ }))
  expect(openReview).toHaveBeenCalledWith('review-357')
})

it('shows a coverage gap instead of an invented rate when coverage data is unavailable', () => {
  render(<OverviewPage stats={{ recent_severe_count: 0, recent_severe_issues: [] }} onNavigate={vi.fn()} onOpenReview={vi.fn()} reviews={[]} projects={PROJECTS} unresolvedBlockers={0} />)
  expect(screen.getByText('暂无覆盖数据')).toBeInTheDocument()
  expect(screen.getByText(/开放 MR 的最新提交覆盖率/)).toBeInTheDocument()
})
