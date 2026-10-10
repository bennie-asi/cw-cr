import type { Project, Review } from './types'
import { useEffect, useRef, useState } from 'react'
import { Filter, GitBranch, History, RefreshCw } from 'lucide-react'
import { ResizableTable } from './ResizableTable'
import { tableColumns } from './tableColumns'
export type HistoryFilters = { project: string; source: '全部' | Review['source']; exception: 'all' | 'generation' | 'publication' | 'notification' }

function generation(review: Review) {
  const fallback = { 已完成: 'completed', 有阻断: 'completed', 失败: 'failed', 运行中: 'running', 排队中: 'queued' }
  const state = review.generationState || fallback[review.status]
  return state === 'blocked' ? 'completed' : state
}
function problemLabel(review: Review) {
  if (review.blockers) return `${review.blockers} 个阻断`
  const finished = review.status === '已完成' || review.status === '有阻断'
  const published = !review.publicationState || ['completed', 'synced', 'skipped'].includes(review.publicationState)
  return finished && published ? '无阻断' : '结果待核验'
}
function Phase({ state, kind }: { state?: string; kind: 'generation' | 'publication' | 'notification' }) {
  const labels: Record<string, string> = kind === 'generation'
    ? { completed: '已生成', failed: '生成失败', queued: '排队中', running: '生成中' }
    : kind === 'publication'
      ? { completed: '已发布', synced: '已发布', pending: '待发布', failed: '发布失败', running: '发布中', not_requested: '未发布', skipped: '无需发布' }
      : { done: '已处理', sent: '已发送', delivered: '已发送', pending: '待处理', sending: '发送中', failed: '通知失败', skipped: '已跳过', not_requested: '未请求' }
  const label = state === 'unknown' ? '结果待确认' : labels[state || ''] || '尚无记录'
  const tone = state === 'failed' ? 'danger' : state === 'unknown' ? 'warning' : ['completed', 'synced', 'done', 'sent', 'delivered'].includes(state || '') ? 'success' : 'neutral'
  return <span className={`badge badge-${tone}`} title={kind === 'notification' && state === 'done' ? '后端已处理通知步骤；没有发送凭据时不能确认实际送达' : undefined}>{label}</span>
}

export function ReviewHistory({ reviews, projects, activeProjectId, filters, onFiltersChange, onRefresh, onRetry, onOpenDetail }: { reviews: Review[]; projects: Project[]; activeProjectId: string | null; filters: HistoryFilters; onFiltersChange: (filters: HistoryFilters) => void; onRefresh: () => void; onRetry: (id: string) => void | Promise<void>; onOpenDetail: (id: string) => void }) {
  const pending = useRef(new Set<string>())
  const active = useRef(true)
  const [busy, setBusy] = useState<string[]>([])
  const [retryError, setRetryError] = useState('')
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  async function retry(id: string) {
    if (pending.current.has(id)) return
    pending.current.add(id)
    setBusy([...pending.current]); setRetryError('')
    try { await onRetry(id) }
    catch (reason) { if (active.current) setRetryError(reason instanceof Error ? reason.message : '重新排队失败，请重试') }
    finally { pending.current.delete(id); if (active.current) setBusy([...pending.current]) }
  }
  const selectedProject = activeProjectId || filters.project
  const selectedName = projects.find(project => project.id === selectedProject)?.name
  const filtered = reviews.filter(review =>
    (filters.source === '全部' || review.source === filters.source) &&
    (!selectedProject || review.projectId === selectedProject || (!review.projectId && review.project === selectedName)) &&
    (filters.exception === 'all' || (filters.exception === 'generation' ? generation(review) === 'failed' : filters.exception === 'publication' ? review.publicationState === 'failed' : ['failed', 'unknown', 'sending'].includes(review.notificationState || ''))))
  return <>
    <div className="toolbar-row history-filters"><div className="filter-label"><Filter size={15} />触发来源</div><div className="segmented-control">{(['全部', 'GitLab Webhook', '/review', '/improve', '手动触发'] as const).map(item => <button key={item} className={filters.source === item ? 'active' : ''} onClick={() => onFiltersChange({ ...filters, source: item })}>{item}</button>)}</div>
      <label className="filter-label">项目 <select aria-label="按项目筛选评审" value={selectedProject} disabled={Boolean(activeProjectId)} onChange={event => onFiltersChange({ ...filters, project: event.target.value })}><option value="">全部项目</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
      <label className="filter-label">异常阶段 <select aria-label="异常阶段" value={filters.exception} onChange={event => onFiltersChange({ ...filters, exception: event.target.value as HistoryFilters['exception'] })}><option value="all">全部记录</option><option value="generation">生成失败</option><option value="publication">发布失败</option><option value="notification">通知异常 / 待确认</option></select></label>
      <div className="toolbar-spacer" /><button className="button button-secondary" onClick={onRefresh}><RefreshCw size={15} />刷新记录</button>
    </div>
    {retryError && <p className="form-error" role="alert">{retryError}</p>}
    <section className="card history-card"><div className="card-header"><div><h2>评审执行记录</h2><p>每行一条评审 · 生成、发布和通知分别记录</p></div><span className="muted-text">{filtered.length} 条</span></div><ResizableTable tableId="review-history" label="评审执行记录" columns={tableColumns.reviewHistory} stickyLastColumn><tbody>{filtered.map(review => <tr key={review.id} onClick={() => onOpenDetail(review.id)}>
      <td><div className="mr-cell"><strong>{review.mr} · {review.project}</strong><span><GitBranch size={13} />{review.repositoryPath ? `${review.repositoryPath} · ` : ''}{review.branch}</span></div></td>
      <td><span className="badge badge-neutral">{review.source}</span></td><td><Phase state={generation(review)} kind="generation" /></td><td><Phase state={review.publicationState} kind="publication" /></td><td><Phase state={review.notificationState} kind="notification" /></td>
      <td>{generation(review) === 'completed' ? <><strong className={review.blockers ? 'danger-text' : undefined}>{problemLabel(review)}</strong><small className="identity-subtext">{review.comments} 条评论</small></> : '—'}</td>
      <td className="muted-text">{review.createdAt}<small className="identity-subtext">{review.duration}</small></td><td><div className="mr-list-actions"><button className="button button-ghost" onClick={event => { event.stopPropagation(); onOpenDetail(review.id) }}>查看记录</button>{(generation(review) === 'failed' || review.publicationState === 'failed' || review.notificationState === 'failed') && <button className="button button-soft" disabled={busy.includes(review.id)} onClick={event => { event.stopPropagation(); void retry(review.id) }}><RefreshCw size={14} />{busy.includes(review.id) ? '正在重新排队…' : '重试失败阶段'}</button>}</div></td>
    </tr>)}</tbody></ResizableTable>{filtered.length === 0 && <div className="empty-state"><History size={22} /><strong>没有符合条件的评审</strong><span>调整项目、来源或异常阶段后再试。</span></div>}<p className="side-note">通知结果待确认时，先查看执行记录核对。未触发的 GitLab MR 不会出现在评审历史中。</p></section>
  </>
}
