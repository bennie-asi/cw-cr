import type { PageId } from './types'

export type WorkspaceRoute = { page: PageId; projectId: string | null; reviewId: string | null }
const platform: Partial<Record<PageId, string>> = {
  overview:'overview', projects:'projects', history:'reviews', models:'settings/models',
  prompts:'settings/prompts',
  notifications:'settings/notifications', system:'settings/system', users:'settings/users', runtime:'settings/runtime',
}
const project: Partial<Record<PageId, string>> = {
  history:'reviews', 'mr-list':'merge-requests', policy:'settings/policy', models:'settings/models',
  notifications:'settings/notifications', 'merge-gate':'settings/merge-gate', members:'settings/members',
}

export function workspacePath(page: PageId, projectId: string | null, reviewId?: string | null): string {
  if (projectId && page === 'review-detail' && reviewId) return `/projects/${encodeURIComponent(projectId)}/reviews/${encodeURIComponent(reviewId)}`
  const suffix = (projectId ? project : platform)[page]
  if (!suffix) throw new Error('页面不属于当前空间')
  return projectId ? `/projects/${encodeURIComponent(projectId)}/${suffix}` : `/platform/${suffix}`
}

export function parseWorkspaceRoute(pathname: string): WorkspaceRoute | null {
  if (pathname === '/') return {page:'overview', projectId:null, reviewId:null}
  const parts = pathname.replace(/\/$/, '').split('/')
  const isProject = parts[1] === 'projects'
  if (!isProject && parts[1] !== 'platform') return null
  try {
    const projectId = isProject ? decodeURIComponent(parts[2] || '') : null
    if (isProject && !projectId) return null
    if (isProject && parts[3] === 'reviews' && parts.length === 5 && parts[4]) {
      return {page:'review-detail', projectId, reviewId:decodeURIComponent(parts[4])}
    }
    const suffix = parts.slice(isProject ? 3 : 2).join('/')
    const page = Object.entries(isProject ? project : platform).find(([, value]) => value === suffix)?.[0] as PageId | undefined
    return page ? {page, projectId, reviewId:null} : null
  } catch { return null }
}
