import { useEffect } from 'react'

type Kind = 'model' | 'notification'

function textValue(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : '未配置'
}

export function EffectiveConfigSummary({ kind, scope, projectName, initial, editing = false, onEdit }: {
  kind: Kind
  scope: 'global' | 'project'
  projectName: string
  initial: Record<string, unknown> | null
  editing?: boolean
  onEdit?: () => void
}) {
  const model = kind === 'model'
  const inherited = scope === 'project' && initial?.inherited === true
  const source = scope === 'global' ? '平台默认' : inherited ? '继承平台' : '项目自定义'
  const configured = model ? Boolean(initial?.api_key_masked) : initial?.webhook_configured === true
  const editSelector = model ? '.model-draft-card' : '.notification-draft-card'
  function focusDraft() {
    const card = document.querySelector<HTMLElement>(editSelector)
    card?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    card?.querySelector<HTMLElement>('input, select, textarea')?.focus()
  }
  useEffect(() => { if (editing) focusDraft() }, [editing])
  function edit() {
    onEdit?.()
    if (!onEdit || editing) focusDraft()
  }
  return <section className="card effective-config" aria-label={model ? '当前生效模型配置' : '当前生效通知配置'}>
    <div className="effective-config-heading">
      <div><span className="effective-config-kicker">{source} · {scope === 'global' ? '所有未覆盖项目' : projectName}</span><h2>{model ? '当前生效模型配置' : '当前生效通知配置'}</h2></div>
      <button type="button" className="button button-primary" onClick={edit}>修改配置</button>
    </div>
    <div className="effective-config-values">
      <div><span>{model ? '当前模型' : '目标群名称'}</span><strong>{textValue(model ? initial?.model : initial?.name)}</strong></div>
      <div><span>{model ? '网关地址' : '机器人地址'}</span><strong>{model ? textValue(initial?.base_url) : configured ? '已配置 · 原文不回显' : '未配置'}</strong></div>
      {model && <div><span>API Key</span><strong>{configured ? '已配置 · 原文不回显' : '未配置'}</strong></div>}
      {!model && <div><span>发送状态</span><strong>{initial?.enabled === false ? '已停用' : configured ? '已启用' : '待配置'}</strong></div>}
    </div>
    <p>下方为编辑草稿；只有保存成功后才会替换当前生效配置。</p>
  </section>
}
