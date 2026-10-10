import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiUrl, mapProject, mapReview, mapComment } from './api'


describe('backend API adapter', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('preserves only the server-provided current project role', () => {
    expect(mapProject({ id: 'p', name: 'p', gitlab_project_path: 'team/p', current_user_role: 'admin' }).currentUserRole).toBe('admin')
    expect(mapProject({ id: 'p', name: 'p', gitlab_project_path: 'team/p' }).currentUserRole).toBeNull()
  })
  it('preserves the old-side location of a deleted-line comment', () => {
    const comment = mapComment({ id: 'c', file_path: 'a.py', old_line: 42, level: 'P1', title: 'deleted', body: 'reason', status: 'published' } as Parameters<typeof mapComment>[0])
    expect(comment.line).toBe(42)
    expect(comment).toHaveProperty('side', 'old')
  })
  it('uses verified source coordinates and retains the original published line', () => {
    const comment = mapComment({ id: 'c', file_path: 'a.py', new_line: 157, level: 'P1', title: 'CSRF', body: 'reason', status: 'published',
      location: { status: 'relocated', new_line: 159, old_line: null, original_new_line: 157, original_old_line: null, reason: '原代码唯一匹配' },
    } as Parameters<typeof mapComment>[0])
    expect(comment.line).toBe(159)
    expect(comment.location?.original_new_line).toBe(157)
  })
  it('does not treat an unverified stored line as a highlightable position', () => {
    const comment = mapComment({ id: 'c', file_path: 'a.py', new_line: 157, level: 'P1', title: 'CSRF', body: 'reason', status: 'published',
      location: { status: 'unverified', new_line: null, old_line: null, original_new_line: 157, original_old_line: null, reason: '缺少完整代码引用' },
    } as Parameters<typeof mapComment>[0])
    expect(comment.line).toBe(0)
    expect(comment.location?.status).toBe('unverified')
  })
  it('builds API URLs without duplicate slashes', () => {
    expect(apiUrl('/projects/')).toMatch(/\/api\/projects\/$/)
  })

  it('maps backend project and review data to dashboard state', () => {
    const project = mapProject({
      id: 'project-1',
      name: 'demo',
      gitlab_project_path: 'group/demo',
      owner: '平台组',
      reviews_count: 4,
      blockers: 1,
      status: '运行中',
      model: { inherited: true },
      notification: { inherited: true },
      updated_at: '2026-09-21T10:00:00Z',
    })
    const review = mapReview({
      id: 'review-1',
      mr_iid: 357,
      project_name: 'demo',
      branch: 'main',
      source: 'GitLab Webhook',
      status: 'blocked',
      status_label: '有阻断',
      blocking_comment_count: 2,
      comment_count: 3,
      duration_ms: 1200,
      created_at: '2026-09-21T10:00:00Z',
    })

    expect(project.id).toBe('project-1')
    expect(project.reviews).toBe(4)
    expect(review.status).toBe('有阻断')
    expect(review.blockers).toBe(2)
  })

  it('sends the explicitly selected review id for a gate dry-run', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ review_id: 'review-1', passed: false, checks: {}, reason: 'blocked' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const { api } = await import('./api')
    await api.mergeGateDryRun('project-1', 'review-1')
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/merge-gate/dry-run/?project=project-1'), expect.objectContaining({ method: 'POST', body: JSON.stringify({ review_id: 'review-1' }) }))
  })
})
