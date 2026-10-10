import type { BackendGitLabConfig } from './api'

export function GitLabEffectiveSummary({ config }: { config: BackendGitLabConfig | null }) {
  function focusDraft() {
    const card = document.querySelector<HTMLElement>('.gitlab-draft-card')
    card?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    card?.querySelector<HTMLElement>('input')?.focus()
  }
  return <section className="card effective-config" aria-label="当前 GitLab 连接">
    <div className="effective-config-heading">
      <div><span className="effective-config-kicker">平台连接 · {config?.enabled === false ? '已停用' : '当前设置'}</span><h2>当前 GitLab 连接</h2></div>
      <button type="button" className="button button-primary" onClick={focusDraft}>修改配置</button>
    </div>
    <div className="effective-config-values">
      <div><span>GitLab 地址</span><strong>{config?.base_url || '未配置'}</strong></div>
      <div><span>API Token</span><strong>{config?.api_token_configured ? '已配置 · 原文不回显' : '未配置'}</strong></div>
      <div><span>Webhook Secret</span><strong>{config?.webhook_secret_configured ? '已配置 · 原文不回显' : '未配置'}</strong></div>
    </div>
    <p>下方为编辑草稿；连接测试和保存是独立操作，只有保存成功后才会更新当前配置。</p>
  </section>
}
