import { CheckCircle2, RefreshCw } from 'lucide-react'
import type { BackendReview } from './api'
import './review-recheck.css'

export function ReviewRecheck({ result }: { result: BackendReview['recheck_result'] }) {
  if (!result?.applied) return null
  const resolved = result.resolved || 0
  const retained = result.retained || 0
  return <section className="review-recheck card" aria-label="本轮问题变化">
    <div className="recheck-heading"><RefreshCw size={17} /><strong>本轮问题变化</strong><span>同一 MR 的问题与 GitLab 讨论持续跟踪</span></div>
    <div className="recheck-metrics" role="status">
      <span>新增问题 {result.new || 0} 条</span>
      <span>复用原讨论 {result.repeated || 0} 条</span>
      <span className={resolved ? 'recheck-resolved' : ''}>复审已解决 {resolved} 条</span>
      <span className={retained ? 'recheck-retained' : ''}>保留待核验 {retained} 条</span>
    </div>
    {retained > 0 && <p className="recheck-note">部分历史问题尚未确认修复，仍计入当前问题。新提交的完整复审确认修复后才会解除阻断；仅生成建议或评审范围变更不会自动清除。</p>}
    {resolved > 0 && !!result.resolved_issues?.length && <details className="recheck-evidence">
      <summary>查看本轮解决的问题</summary>
      <ul>{result.resolved_issues.map(issue => <li key={issue.issue_id}><CheckCircle2 size={15} /><span><strong>{issue.title}</strong><small>{issue.file_path}</small></span><span className="recheck-level">{issue.level}</span></li>)}</ul>
      <p>原评审记录和代码位置已保留，相关 GitLab 讨论已标记解决。</p>
    </details>}
  </section>
}
