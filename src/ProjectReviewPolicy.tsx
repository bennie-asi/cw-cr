import { useEffect, useRef, useState } from 'react'
import { Activity, Code2, ShieldCheck } from 'lucide-react'
import type { PolicyState } from './types'
import './project-policy.css'

const checks = [
  { key: 'security', prompt: 'securityPrompt', api: 'security_prompt', title: '安全性', hint: '关注越权、注入、认证与敏感信息泄露。', icon: ShieldCheck },
  { key: 'reliability', prompt: 'reliabilityPrompt', api: 'reliability_prompt', title: '可靠性', hint: '关注异常处理、并发、幂等与资源释放。', icon: Activity },
  { key: 'style', prompt: 'stylePrompt', api: 'style_prompt', title: '代码风格', hint: '关注命名、重复逻辑和可维护性。', icon: Code2 },
] as const

export const thresholdLabel = (policy: PolicyState) => `${policy.blockOnP2 ? 'P0 / P1 / P2' : policy.blockOnP1 ? 'P0 / P1' : 'P0'} + 未评级`

export function ProjectReviewPolicy({ policy, onChange, onValidityChange, saving, readOnly }: {
  policy: PolicyState; onChange: (value: PolicyState) => void; onValidityChange: (error: string) => void;
  saving: boolean; readOnly: boolean;
}) {
  const [selected, setSelected] = useState(0)
  const [custom, setCustom] = useState(() => checks.map(check => Boolean(policy[check.prompt]?.trim())))
  const previousDrafts = useRef(checks.map(check => policy[check.prompt] || ''))
  useEffect(() => {
    setCustom(checks.map(check => Boolean(policy[check.prompt]?.trim())))
    previousDrafts.current = checks.map(check => policy[check.prompt] || '')
  }, [policy.effectivePrompts])
  const field = checks[selected]
  const text = policy[field.prompt] || ''
  const inheritedText = policy.platformPrompts?.[field.api] || policy.effectivePrompts?.[field.api] || ''
  const enabled = policy[field.key]
  const error = checks.find((check, index) => custom[index] && !(policy[check.prompt] || '').trim())
  const errorMessage = error ? `${error.title}提示词不能为空；选择继承平台可恢复默认要求。` : ''
  useEffect(() => { onValidityChange(errorMessage) }, [errorMessage, onValidityChange])

  function source(useCustom: boolean) {
    setCustom(previous => previous.map((value, index) => index === selected ? useCustom : value))
    if (!useCustom) previousDrafts.current[selected] = text
    onChange({ ...policy, [field.prompt]: useCustom ? previousDrafts.current[selected] || inheritedText : '' })
  }

  return <>
    <section className="card project-prompt-card">
      <div className="card-header"><div><h2>评审检查与提示词</h2><p>每类检查独立配置；继承平台要求，或使用本项目的检查提示词。</p></div></div>
        <div className="project-check-tabs" role="tablist" aria-label="项目专项检查">{checks.map((check, index) => <button
          type="button" role="tab" id={`project-check-${check.key}`} key={check.key} aria-controls="project-check-panel"
          aria-selected={selected === index} tabIndex={selected === index ? 0 : -1} onClick={() => setSelected(index)}
          onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
            event.preventDefault()
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3
            setSelected(next)
            document.getElementById(`project-check-${checks[next].key}`)?.focus()
          }}><check.icon size={16} /><span>{check.title}</span><span className={`check-enabled-dot ${policy[check.key] ? 'enabled' : ''}`} aria-hidden="true" /></button>)}</div>
      <fieldset className="notification-form-boundary" disabled={readOnly || saving} aria-busy={saving}>
        <div role="tabpanel" id="project-check-panel" aria-labelledby={`project-check-${field.key}`}>
          <div className="project-check-heading"><div><h3>{field.title}检查</h3><p>{field.hint}</p></div>
            <label className="project-check-enable"><input type="checkbox" aria-label={`启用${field.title}检查`} checked={enabled} onChange={event => onChange({ ...policy, [field.key]: event.target.checked })} />启用检查</label></div>
          <div className="project-prompt-source" role="radiogroup" aria-label={`${field.title}提示词来源`}>
            <label><input type="radio" name="project-prompt-source" checked={!custom[selected]} onChange={() => source(false)} />继承平台提示词</label>
            <label><input type="radio" name="project-prompt-source" checked={custom[selected]} onChange={() => source(true)} />自定义本项目</label>
          </div>
          <label className="field"><span>{custom[selected] ? '项目' : '平台'}{field.title}提示词</span><textarea className="prompt-editor project-specialty-editor" maxLength={20000}
            readOnly={!custom[selected]} value={custom[selected] ? text : inheritedText} aria-describedby="project-prompt-hint"
            placeholder={!custom[selected] ? '平台提示词尚未加载，请刷新项目策略。' : `填写本项目的${field.title}检查要求`}
            onChange={event => onChange({ ...policy, [field.prompt]: event.target.value })} /></label>
          <div className="project-prompt-footer"><span id="project-prompt-hint">{!enabled ? '该检查已关闭，内容保留；提示词不参与评审。' : custom[selected] ? '保存后覆盖本项目该类专项提示词。' : '平台更新后，本项目自动使用新的要求。'}</span><span>{(custom[selected] ? text : inheritedText).length} / 20000</span></div>
          {errorMessage && <p role="alert" className="form-error">{errorMessage}</p>}
          <p className="policy-explanation">平台通用要求与当前 /review 或 /improve 模式始终保留；关闭检查后，该类提示词不参与评审。</p>
        </div>
      </fieldset>
    </section>
    <section className="card project-threshold-card"><div className="card-header"><div><h2>合并阻断门槛</h2><p>选择哪些未解决问题阻止合并。通知、评审结果和门禁使用同一门槛。</p></div></div>
      <fieldset disabled={readOnly || saving} className="notification-form-boundary"><div className="project-threshold-options" role="radiogroup" aria-label="合并阻断门槛">{[
        { value: 'P0', label: '仅 P0 · 宽松', hint: '只阻断严重问题，P1 / P2 作为建议保留。' },
        { value: 'P1', label: 'P0 / P1 · 标准', hint: '阻断严重和高优先级问题，推荐默认设置。' },
        { value: 'P2', label: 'P0 / P1 / P2 · 严格', hint: '所有级别都需要处理，适用于要求严格的仓库。' },
      ].map(option => <label key={option.value}><input type="radio" name="project-blocking-threshold"
        checked={option.value === (policy.blockOnP2 ? 'P2' : policy.blockOnP1 ? 'P1' : 'P0')}
        onChange={() => onChange({ ...policy, blockOnP1: option.value !== 'P0', blockOnP2: option.value === 'P2' })} />
        <strong>{option.label}</strong><span>{option.hint}</span></label>)}</div></fieldset>
      <p className="policy-explanation">未评级问题始终需要人工确认；已解决的问题不再计入阻断。更改检查要求或门槛后，项目自动合并开关保持原状态；后续完整评审按新要求自动检查，旧检查预览失效。</p>
    </section>
  </>
}
