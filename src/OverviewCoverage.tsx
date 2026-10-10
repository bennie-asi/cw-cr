import { useEffect, useState } from 'react'
import { ArrowUpRight, RefreshCw } from 'lucide-react'
import { api } from './api'
import type { MRCoverage } from './api'

export function OverviewCoverage({ onOpenProjects, enabled = true }: { onOpenProjects: () => void; enabled?: boolean }) {
  const [data, setData] = useState<MRCoverage | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    if (!enabled) { setData(null); setLoading(false); return }
    let active = true
    setLoading(true); setError(false); setData(null)
    api.overviewCoverage().then(value => { if (active) setData(value) }).catch(() => { if (active) setError(true) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [enabled, refresh])
  const complete = data?.state === 'complete'
  const title = loading ? '正在核对最新版本…' : error ? '覆盖数据暂不可用' : !data ? '暂无覆盖数据' : !complete ? '覆盖核对未完成' : data.open_mrs === 0 ? '暂无开放 MR' : data.percentage !== null ? `${data.percentage}%` : '版本待核对'
  return <section className="card coverage-card" aria-label="最新版本评审覆盖">
    <div className="card-header"><div><h2>开放 MR · 最新版本已评审</h2><p>包含草稿；新提交后需重新评审</p></div><button className="icon-button" aria-label="刷新覆盖" disabled={!enabled || loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={15} /></button></div>
    <div className={complete && data.open_mrs > 0 ? 'coverage-value' : 'coverage-unavailable'} role={error ? 'alert' : loading ? 'status' : undefined}>{title}</div>
    {data && <p className="coverage-count">{complete ? '' : '已核对范围：'}已评审 {data.reviewed_mrs} / {data.open_mrs} · 待覆盖 {data.unreviewed_mrs}</p>}
    {complete && data.percentage !== null && <div className="coverage-meter" role="meter" aria-label="最新版本覆盖率" aria-valuemin={0} aria-valuemax={100} aria-valuenow={data.percentage}><i style={{ width: `${data.percentage}%` }} /></div>}
    <p className="coverage-explain">开放 MR 的最新提交覆盖率按当前 SHA 核对；仅已完成的完整评审计入覆盖。</p>
    {data && <p className="side-note">已核对 {data.repositories_checked} / {data.repositories_total} 个仓库 · {new Date(data.checked_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</p>}
    {data?.errors.length ? <ul className="coverage-errors">{data.errors.slice(0, 4).map((item, index) => <li key={`${item.path}-${index}`}>{item.path}：{item.detail}</li>)}{data.errors.length > 4 && <li>另有 {data.errors.length - 4} 个仓库未完成核对</li>}</ul> : null}
    <button className="button button-soft" onClick={onOpenProjects}>查看项目与 MR <ArrowUpRight size={14} /></button>
  </section>
}
