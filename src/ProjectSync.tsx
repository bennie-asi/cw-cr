import { useEffect, useRef, useState } from 'react'
import { Clock3, RefreshCw } from 'lucide-react'
import { api, type GitLabSyncStatus } from './api'
import './project-sync.css'

const timestamp = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

export function ProjectSync({ projectId, readOnly }: { projectId: string; readOnly: boolean }) {
  const [status, setStatus] = useState<GitLabSyncStatus | null>(null)
  const [enabled, setEnabled] = useState(false)
  const [interval, setIntervalValue] = useState('60')
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const epoch = useRef(0)
  const mutation = useRef(0)
  const busyRef = useRef(false)

  useEffect(() => {
    const generation = ++epoch.current
    ++mutation.current
    busyRef.current = false
    setStatus(null); setBusy(false); setLoadError(''); setError(''); setMessage('')
    async function refresh(initial: boolean) {
      if (busyRef.current) return
      const version = mutation.current
      try {
        const value = await api.projectSync(projectId)
        if (generation !== epoch.current || version !== mutation.current) return
        setStatus(value); setLoadError('')
        if (initial) { setEnabled(value.enabled); setIntervalValue(String(value.interval_seconds)) }
      } catch (cause) {
        if (generation === epoch.current && version === mutation.current) setLoadError(cause instanceof Error ? cause.message : '无法加载同步设置')
      }
    }
    void refresh(true)
    const timer = window.setInterval(() => void refresh(false), 10000)
    return () => { ++epoch.current; window.clearInterval(timer) }
  }, [projectId, reload])

  const seconds = Number(interval)
  const validInterval = interval.trim() !== '' && Number.isInteger(seconds) && seconds >= 30 && seconds <= 3600
  const dirty = Boolean(status && (enabled !== status.enabled || seconds !== status.interval_seconds))
  async function save() {
    if (!status || readOnly || busyRef.current || !validInterval) return
    const generation = epoch.current
    ++mutation.current; busyRef.current = true
    setBusy(true); setError(''); setMessage('')
    try {
      const value = await api.saveProjectSync(projectId, enabled, seconds)
      if (generation !== epoch.current) return
      setStatus(value); setEnabled(value.enabled); setIntervalValue(String(value.interval_seconds))
      setMessage('同步设置已保存')
    } catch (cause) {
      if (generation === epoch.current) setError(cause instanceof Error ? cause.message : '同步设置保存失败')
    } finally {
      if (generation === epoch.current) { busyRef.current = false; setBusy(false) }
    }
  }
  async function run() {
    if (!status?.enabled || readOnly || busyRef.current || status.in_progress) return
    const generation = epoch.current
    ++mutation.current; busyRef.current = true
    setBusy(true); setError(''); setMessage('')
    try {
      const value = await api.runProjectSync(projectId)
      if (generation !== epoch.current) return
      setStatus(value); setMessage('已请求后台同步')
    } catch (cause) {
      if (generation === epoch.current) setError(cause instanceof Error ? cause.message : '无法请求同步')
    } finally {
      if (generation === epoch.current) { busyRef.current = false; setBusy(false) }
    }
  }

  return <section className="card project-sync-card" aria-label="GitLab 定时同步">
    <div className="card-header"><div><h2><RefreshCw size={17} /> GitLab 定时同步</h2><p>主动发现 MR 变更，也可同时接收 Webhook 实时通知。</p></div>
      {status && <span className={`sync-status ${status.enabled ? status.sync_state : 'idle'}`}>
        {!status.enabled ? '已关闭' : status.in_progress ? '正在同步' : status.sync_state === 'error' ? '等待重试' : '已开启'}
      </span>}
    </div>
    {!status ? <div className="sync-loading">{loadError ? <><p role="alert">{loadError}</p><button className="sync-button" onClick={() => setReload(value => value + 1)}>重新加载同步设置</button></> : <p role="status">正在加载同步设置…</p>}</div> : <>
      <fieldset className="sync-controls" disabled={readOnly || busy} aria-busy={busy}>
        <label className="sync-switch"><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} aria-label="启用定时同步" />启用定时同步</label>
        <label className="field sync-interval"><span>同步间隔（秒）</span><input type="number" aria-label="同步间隔（秒）" min={30} max={3600} step={1} value={interval} onChange={event => setIntervalValue(event.target.value)} /><small>30–3600 秒，默认 60 秒</small></label>
        <div className="sync-actions"><button className="sync-button primary" disabled={!validInterval || !dirty} onClick={() => void save()}>{busy ? '正在处理…' : '保存同步设置'}</button><button className="sync-button" disabled={!status.enabled || status.in_progress} onClick={() => void run()}>立即同步</button></div>
      </fieldset>
      {!validInterval && <p className="form-error">同步间隔须为 30–3600 秒的整数</p>}
      <p className="side-note sync-baseline-note"><Clock3 size={14} />开启时记录现有 MR 的提交，只自动处理之后的新 MR 和新提交。重新开启会更新基线。</p>
      <dl className="sync-times"><div><dt>最近同步</dt><dd>{timestamp(status.last_sync_at)}</dd></div><div><dt>下次同步</dt><dd>{timestamp(status.next_sync_at)}</dd></div><div><dt>基线记录</dt><dd>{status.baseline_mrs} 个 MR</dd></div><div><dt>最近入队</dt><dd>{status.last_result.enqueued ?? 0} 个 MR</dd></div></dl>
      {status.last_result.repositories?.length ? <div className="sync-repositories">{status.last_result.repositories.map(repo => <div key={repo.path}><strong>{repo.path}</strong><span>{repo.error || `${repo.opened_mrs} 个开放 MR · ${repo.enqueued} 个新任务${repo.baselined ? ' · 已记录基线' : ''}`}</span></div>)}</div> : null}
      {status.last_error && <p className="form-error">{status.last_error}</p>}
      {loadError && <p role="alert" className="form-error">{loadError}</p>}
      {error && <p role="alert" className="form-error">{error}</p>}
      {message && <p role="status" className="sync-message">{message}</p>}
    </>}
  </section>
}
