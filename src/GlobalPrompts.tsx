import { useEffect, useRef, useState } from 'react'
import { Save, SlidersHorizontal } from 'lucide-react'
import { api } from './api'
import type { PromptConfig } from './api'
import { useDraftState } from './useDraftState'
import { useConfigSave } from './useConfigSave'

const fields: { key: keyof PromptConfig; label: string; hint: string }[] = [
  { key: 'general_prompt', label: '通用评审要求', hint: '所有引擎与评审模式共同遵守的要求。' },
  { key: 'review_prompt', label: '/review 评审', hint: '完整评审时加入的缺陷与风险检查要求。' },
  { key: 'improve_prompt', label: '/improve 改进', hint: '生成改进建议时加入的要求。' },
  { key: 'security_prompt', label: '安全性检查', hint: '只在项目开启安全性检查时加入。' },
  { key: 'reliability_prompt', label: '可靠性检查', hint: '只在项目开启可靠性检查时加入。' },
  { key: 'style_prompt', label: '代码风格检查', hint: '只在项目开启代码风格检查时加入。' },
]

export function GlobalPrompts({ onDirtyChange, onNotify }: { onDirtyChange: (dirty: boolean) => void; onNotify: (message: string) => void }) {
  const [form, setForm, resetForm] = useDraftState<PromptConfig | null>(null, onDirtyChange)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [reload, setReload] = useState(0)
  const [selected, setSelected] = useState(0)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const active = useRef(true)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  useEffect(() => {
    let current = true
    setStatus('loading')
    void api.promptConfig().then(data => {
      if (current) { resetForm(data); setStatus('ready') }
    }).catch(() => { if (current) setStatus('error') })
    return () => { current = false }
  }, [reload, resetForm])

  const { save, saving, saveError } = useConfigSave(async payload => {
    setError('')
    const invalid = fields.find(field => !String(payload[field.key] || '').trim())
    if (invalid) { setError(`${invalid.label}不能为空`); return false }
    try {
      const data = await api.updatePromptConfig(payload)
      if (!active.current) return false
      resetForm(data)
      setSaved(true)
      onNotify('全局提示词已保存，对新触发的评审生效')
      return true
    } catch (failure) {
      if (active.current) setError(failure instanceof Error ? failure.message : '保存失败，输入内容已保留')
      return false
    }
  }, () => undefined)

  if (status !== 'ready' || !form) return <div className="empty-state" role={status === 'error' ? 'alert' : 'status'}>
    <strong>{status === 'error' ? '全局提示词加载失败' : '正在加载全局提示词…'}</strong>
    <span>读取成功后才能编辑和保存。</span>
    {status === 'error' && <button className="button button-secondary" onClick={() => setReload(value => value + 1)}>重新加载提示词</button>}
  </div>

  const field = fields[selected]
  return <div className="settings-layout"><div className="settings-main">
    <section className="card">
      <div className="card-header"><div><h2>全局评审提示词</h2><p>平台通用要求 + 当前模式 + 开启的专项检查 + 项目补充要求</p></div></div>
      <fieldset className="notification-form-boundary" disabled={saving} aria-busy={saving}>
        <div className="detail-tabs" role="tablist" aria-label="提示词类型">{fields.map((item, index) => <button
          role="tab" id={`prompt-tab-${item.key}`} aria-controls={`prompt-panel-${item.key}`} aria-selected={selected === index}
          tabIndex={selected === index ? 0 : -1} className={selected === index ? 'active' : ''} key={item.key}
          onClick={() => setSelected(index)} onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
            event.preventDefault()
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? fields.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + fields.length) % fields.length
            setSelected(next)
            document.getElementById(`prompt-tab-${fields[next].key}`)?.focus()
          }}>{item.label}</button>)}</div>
        <div role="tabpanel" id={`prompt-panel-${field.key}`} aria-labelledby={`prompt-tab-${field.key}`}>
          <label className="field"><span>{field.label}提示词</span><textarea className="prompt-editor" maxLength={20000}
            value={form[field.key]} onChange={event => { setSaved(false); setForm(previous => previous && { ...previous, [field.key]: event.target.value }) }} /></label>
          <p className="side-note">{field.hint} 项目已有的补充提示词会保留。</p>
        </div>
        {(error || saveError) && <p role="alert" className="form-error">{error || saveError}</p>}
        {saved && <p role="status" className="side-note">全局提示词已保存，对新触发的评审生效。</p>}
        <div className="policy-save-bar"><span>{saving ? '正在保存全局提示词…' : '保存全部六类提示词；仅影响新触发的评审'}</span>
          <button className="button button-primary" onClick={() => void save(form)}><Save size={14} />保存全局提示词</button></div>
      </fieldset>
    </section>
  </div><aside className="settings-side"><section className="card"><div className="card-header"><div><h2>生效范围</h2></div></div>
    <div className="inherit-note"><SlidersHorizontal size={18} /><div><strong>平台统一，项目补充</strong><span>内置评审、Pi Agent、PR-Agent 和 OpenCodeReview 使用同一组全局要求。项目策略中的检查开关决定专项要求是否加入。</span></div></div>
    <p className="side-note">中文输出、结构化结果、真实行号与建议校验由平台统一执行。通知结论依据实际行级评论和门禁。</p>
  </section></aside></div>
}
