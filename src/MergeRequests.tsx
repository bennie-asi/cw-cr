import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, RefreshCw } from 'lucide-react'
import { api } from './api'
import type { MergeRequestItem } from './api'
import { ResizableTable } from './ResizableTable'
import { tableColumns } from './tableColumns'

type Props = { projectId: string; repositories?: { path: string }[]; onReviewed: () => void; onOpenReview?: (id: string) => void }
const labels = { unreviewed: '未评审', queued: '排队中', running: '评审中', reviewed: '最新版本已评审', stale: '已有新提交 · 需重新评审', failed: '评审失败', unknown: '版本待核对' }

export function MergeRequests(props: Props) {
  return <MergeRequestsContent key={props.projectId} {...props} />
}

function MergeRequestsContent({ projectId, repositories = [], onReviewed, onOpenReview }: Props) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.listMergeRequests>> | null>(null)
  const [repositoryPath, setRepositoryPath] = useState(repositories[0]?.path || '')
  const [state, setState] = useState<'opened' | 'merged'>('opened')
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const context = useRef({ repository: repositoryPath, version: 0 })
  const pending = useRef<object | null>(null)
  if (context.current.repository !== repositoryPath) {
    context.current = { repository: repositoryPath, version: context.current.version + 1 }
    pending.current = null
  }
  useEffect(() => {
    let active = true
    setData(null); setError(''); setMessage('')
    api.listMergeRequests(projectId, page, repositoryPath || undefined, state)
      .then(value => { if (active) setData(value) })
      .catch(reason => { if (active) setError(reason instanceof Error && reason.message.includes('权限') ? reason.message : '无法加载 MR，请检查 GitLab 配置、项目路径和访问权限。') })
    return () => { active = false }
  }, [projectId, repositoryPath, state, page, refresh])
  useEffect(() => () => { context.current.version++; pending.current = null }, [])

  const run = async (mr: MergeRequestItem, mode: 'review' | 'improve') => {
    if (pending.current || mr.review_state === 'queued' || mr.review_state === 'running') return
    const token = {}
    const version = context.current.version
    pending.current = token
    setBusy(mr.iid); setMessage('')
    try {
      const result = await api.triggerReview(projectId, { mr_iid: mr.iid, mode, ...(repositoryPath ? { repository_path: repositoryPath } : {}) })
      if (version !== context.current.version) return
      if (result.job_status === 'queued' || result.job_status === 'running') setData(previous => previous && { ...previous, results: previous.results.map(row => row.iid === mr.iid ? { ...row, review_state: result.job_status as 'queued' | 'running' } : row) })
      setMessage(result.review?.status === 'failed' || result.job_status === 'failed' ? '评审失败，请在评审历史查看原因。' : '评审任务已提交，请在评审历史查看实际状态。')
      onReviewed()
    } catch (reason) {
      if (version === context.current.version) setMessage(reason instanceof Error ? reason.message : '触发失败，请检查模型配置和后端服务。')
    } finally {
      if (pending.current === token) { pending.current = null; setBusy(null) }
    }
  }
  return <section className="card mr-list-card" aria-label="GitLab MR 列表">
    <div className="card-header"><div><h2>GitLab MR 列表</h2><p>按 GitLab 最近更新排序 · 草稿同样计入覆盖 · 新提交后需重新评审</p></div><button className="button button-secondary" onClick={() => setRefresh(n => n + 1)}><RefreshCw size={14} />刷新 MR</button></div>
    <div className="toolbar-row">
      {repositories.length > 0 && <label className="filter-label">GitLab 仓库 <select aria-label="GitLab 仓库" value={repositoryPath} onChange={event => { setRepositoryPath(event.target.value); setPage(1); setBusy(null); setMessage('') }}>{repositories.map(repository => <option key={repository.path} value={repository.path}>{repository.path}</option>)}</select></label>}
      <div className="segmented-control" aria-label="MR 状态"><button className={state === 'opened' ? 'active' : ''} onClick={() => { setState('opened'); setPage(1) }}>待合并</button><button className={state === 'merged' ? 'active' : ''} onClick={() => { setState('merged'); setPage(1) }}>已合并</button></div>
    </div>
    {error && <div className="empty-state" role="alert"><strong>{error}</strong><button className="button button-secondary" onClick={() => setRefresh(n => n + 1)}>重试加载</button></div>}
    {!data && !error && <div className="empty-state" role="status">正在读取 GitLab…</div>}
    {data?.results.length === 0 && <div className="empty-state">当前没有{state === 'merged' ? '已合并' : '待合并'} MR。</div>}
    {data && data.results.length > 0 && <ResizableTable tableId="merge-requests" label="GitLab MR 列表" columns={tableColumns.mergeRequests} stickyLastColumn><tbody>{data.results.map(mr => {
      const reviewState = mr.review_state || 'unreviewed'
      const locked = busy !== null || reviewState === 'queued' || reviewState === 'running'
      return <tr key={mr.iid}><td><div className="mr-cell"><strong>{/^https?:\/\//i.test(mr.web_url) ? <a href={mr.web_url} target="_blank" rel="noreferrer">!{mr.iid} · {mr.title}</a> : <span>!{mr.iid} · {mr.title}</span>}</strong><small>{mr.source_branch} → {mr.target_branch}{mr.draft ? ' · 草稿' : ''}{mr.state === 'merged' ? ' · 已合并' : ''}</small></div></td>
        <td><span className={`badge ${reviewState === 'reviewed' ? 'badge-success' : reviewState === 'failed' ? 'badge-danger' : reviewState === 'stale' ? 'badge-warning' : 'badge-neutral'}`}>{labels[reviewState]}</span>{reviewState === 'reviewed' && typeof mr.issue_count === 'number' && <small className="identity-subtext">{mr.issue_count} 个问题</small>}</td>
        <td className="muted-text">{mr.updated_at ? new Date(mr.updated_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}</td>
        <td><div className="mr-list-actions"><button className="button button-soft" disabled={locked} onClick={() => void run(mr, 'review')}>{busy === mr.iid ? '提交中…' : '/review'}</button><button className="button button-secondary" disabled={locked} onClick={() => void run(mr, 'improve')}>/improve</button>{mr.review_id && onOpenReview && <button className="button button-ghost" onClick={() => onOpenReview(mr.review_id!)}>查看评审 <ArrowUpRight size={13} /></button>}</div></td>
      </tr>
    })}</tbody></ResizableTable>}
    <div className="mr-list-footer"><p className="side-note">已合并 MR 可重新评审；此处提交任务不会再次合并 MR。</p><div className="button-group"><button className="button button-secondary" disabled={page === 1 || !data} onClick={() => setPage(n => n - 1)}>上一页</button><span>第 {page} 页</span><button className="button button-secondary" disabled={!data?.has_next} onClick={() => setPage(n => n + 1)}>下一页</button></div></div>
    {message && <p role="status" className="side-note">{message}</p>}
  </section>
}
