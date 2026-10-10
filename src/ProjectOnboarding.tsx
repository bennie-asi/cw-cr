import { useEffect, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { AlertTriangle, Bell, Bot, CheckCircle2, Plus, RefreshCw, Save, X } from 'lucide-react'
import { api, apiUrl } from './api'
import type { PageId, Project } from './types'
import type { ProjectConnectionCheck, RepositoryDraft } from './api'
export type ProjectDraft = { name: string; owner: string; repositories: RepositoryDraft[]; enabled: boolean; modelInherit: boolean; notificationInherit: boolean }
export type ProjectDrawerProps = { initial: Project | null; saving: boolean; onClose: () => void; onSave: (draft: ProjectDraft) => Promise<Project>; onContinue?: (id: string, page: PageId) => void }
export function ProjectDrawer({ initial, saving, onClose, onSave, onContinue }: ProjectDrawerProps) {
  const editing = initial !== null
  const [step, setStep] = useState(1)
  const [name, setName] = useState(initial?.name || '')
  const [repositories, setRepositories] = useState<RepositoryDraft[]>(initial?.repositories?.length ? initial.repositories : [{ path: initial?.path || '' }])
  const [owner, setOwner] = useState(initial?.owner === '未分配' ? '' : initial?.owner || '')
  const [enabled, setEnabled] = useState(initial?.enabled !== false)
  const [modelInherit, setModelInherit] = useState(initial?.modelInherit !== false)
  const [notificationInherit, setNotificationInherit] = useState(initial?.notificationInherit !== false)
  const [error, setError] = useState('')
  const [checkError, setCheckError] = useState('')
  const [checking, setChecking] = useState(false)
  const [check, setCheck] = useState<ProjectConnectionCheck | null>(null)
  const [created, setCreated] = useState<Project | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const pending = useRef(false)
  const active = useRef(true)
  const checkVersion = useRef(0)
  const opener = useRef(document.activeElement as HTMLElement | null)
  const busy = saving || submitting
  useEffect(() => { active.current = true; return () => { active.current = false; checkVersion.current++ } }, [])
  function invalidateCheck() { checkVersion.current++; setCheck(null); setCheckError(''); setChecking(false) }
  function updateRepositories(value: RepositoryDraft[]) { invalidateCheck(); setRepositories(value); setError('') }
  function validate() {
    const paths = repositories.map(repository => repository.path.trim())
    if (!name.trim() || paths.some(path => !path)) { setError('请填写项目名称和所有 GitLab 仓库路径'); return false }
    const normalized = paths.map(path => {
      try { return (path.startsWith('http') ? new URL(path).pathname : path).replace(/^\/+|\/+$/g, '').replace(/\.git$/, '') } catch { return '' }
    })
    if (normalized.some(path => !path.includes('/'))) { setError('请输入 group/project 路径或完整 GitLab 项目地址'); return false }
    if (new Set(normalized).size !== normalized.length) { setError('GitLab 仓库路径不能重复'); return false }
    setError(''); return true
  }
  async function runCheck() {
    const version = ++checkVersion.current
    setChecking(true); setCheckError(''); setCheck(null)
    try {
      const result = await api.checkProjectConnection(repositories.map(repository => ({ ...repository, path: repository.path.trim() })))
      if (active.current && version === checkVersion.current) setCheck(result)
    } catch (reason) {
      if (active.current && version === checkVersion.current) setCheckError(reason instanceof Error ? reason.message : '连接检查失败，请稍后重试')
    } finally { if (active.current && version === checkVersion.current) setChecking(false) }
  }
  async function submit() {
    if (pending.current || busy || !validate()) return
    pending.current = true; setSubmitting(true)
    try {
      const result = await onSave({ name: name.trim(), owner: owner.trim(), repositories: repositories.map(repository => ({ ...repository, path: repository.path.trim() })), enabled, modelInherit, notificationInherit })
      if (active.current) { if (editing) onClose(); else setCreated(result) }
    } catch (reason) { if (active.current) setError(reason instanceof Error ? reason.message : '保存失败，请重试') }
    finally { pending.current = false; if (active.current) setSubmitting(false) }
  }
  const repositoryForm = <>
    <label className="field"><span>项目名称 <em>*</em></span><input aria-label="项目名称" maxLength={160} value={name} onChange={event => setName(event.target.value)} placeholder="例如 demo-api" /></label>
    <div className="drawer-section"><div className="section-label">GitLab 仓库</div><p className="side-note">支持 group/project 或完整项目 URL；仓库必须属于已配置的平台 GitLab。</p>{repositories.map((repository, index) => <div className="repository-input-row" key={index}><label className="field"><span>GitLab 项目路径 {index + 1} <em>*</em></span><input aria-label={`GitLab 项目路径 ${index + 1}`} value={repository.path} maxLength={255} onChange={event => updateRepositories(repositories.map((item, position) => position === index ? { path: event.target.value } : item))} placeholder="example-team/project" /></label>{repositories.length > 1 && <button className="button button-secondary" aria-label={`移除仓库 ${index + 1}`} onClick={() => updateRepositories(repositories.filter((_, position) => position !== index))}>移除</button>}</div>)}<button className="button button-secondary" disabled={repositories.length >= 10} onClick={() => updateRepositories([...repositories, { path: '' }])}><Plus size={14} />添加仓库</button></div>
    <label className="field"><span>负责人</span><input value={owner} maxLength={120} onChange={event => setOwner(event.target.value)} placeholder="可选" /></label>
  </>
  const defaults = <div className="drawer-section"><div className="section-label">默认行为</div><label className="drawer-check"><Bot size={17} /><div><strong>继承平台模型</strong><span>关闭后需要配置项目模型</span></div><input type="checkbox" checked={modelInherit} onChange={event => setModelInherit(event.target.checked)} /></label><label className="drawer-check"><Bell size={17} /><div><strong>继承平台通知</strong><span>关闭后需要配置项目企微机器人</span></div><input type="checkbox" checked={notificationInherit} onChange={event => setNotificationInherit(event.target.checked)} /></label>{editing && <label className="drawer-check"><div><strong>启用项目</strong><span>暂停后不接受 Webhook 或手动评审</span></div><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} /></label>}</div>
  return <Dialog.Root open onOpenChange={open => { if (!open && !pending.current && !saving) onClose() }}><Dialog.Portal><Dialog.Overlay className="drawer-backdrop" />
    <Dialog.Content className="drawer onboarding-drawer" onEscapeKeyDown={event => { if (pending.current || saving) event.preventDefault() }} onInteractOutside={event => { if (pending.current || saving) event.preventDefault() }} onCloseAutoFocus={event => { event.preventDefault(); opener.current?.focus() }}>
      <div className="drawer-header"><div><div className="eyebrow">项目管理</div><Dialog.Title asChild><h2>{editing ? '编辑项目' : '添加项目'}</h2></Dialog.Title><Dialog.Description>一个项目可绑定多个 GitLab 仓库，使用同一套评审策略。</Dialog.Description></div><button className="icon-button" aria-label="关闭项目编辑" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      {!editing && !created && <ol className="onboarding-steps" aria-label="接入步骤">{['仓库信息', '连接检查', '确认创建'].map((label, index) => <li key={label} aria-current={step === index + 1 ? 'step' : undefined} className={step > index + 1 ? 'completed' : ''}><b>{step > index + 1 ? <CheckCircle2 size={14} /> : index + 1}</b><span>{label}</span></li>)}</ol>}
      <div className="drawer-body">
        {created ? <div className="onboarding-success"><CheckCircle2 size={36} /><h3>项目已创建</h3><strong>{created.name}</strong><p>当前状态：{created.status}</p>{created.statusReasons?.length ? <ul>{created.statusReasons.map(reason => <li key={reason}>{reason}</li>)}</ul> : <p>连接、模型与自动触发设置可在项目空间继续查看。</p>}<p>在 GitLab 配好 Webhook 并验证模型后，即可开始真实评审。</p></div> : <fieldset disabled={busy} className="onboarding-fields">
          {(editing || step === 1) && repositoryForm}
          {editing && defaults}
          {!editing && step === 2 && <>
            <div className="onboarding-section-heading"><h3>连接检查</h3><button className="button button-secondary" disabled={checking} onClick={() => void runCheck()}><RefreshCw size={14} />重新检查</button></div>
            <p className="side-note">检查只读取仓库和 Webhook 配置。不会创建项目，也不会修改 GitLab。</p>
            {checking && <p role="status">正在检查仓库与 Webhook…</p>}
            {checkError && <p role="alert" className="form-error">{checkError}</p>}
            {check?.repositories.map((repository, index) => <section className="onboarding-check" key={`${repository.path}-${index}`}><h4>{repository.path}</h4><div><span className={`badge badge-${repository.connection.state === 'ready' ? 'success' : 'warning'}`}>{repository.connection.state === 'ready' ? '连接可用' : '连接待配置'}</span><p>{repository.connection.detail}</p></div><div><span className={`badge badge-${repository.webhook.state === 'ready' ? 'success' : 'warning'}`}>{repository.webhook.state === 'ready' ? 'Webhook 已配置' : 'Webhook 待核对'}</span><p>{repository.webhook.detail}</p></div></section>)}
            <label className="field"><span>Webhook 地址</span><input readOnly value={check?.webhook_url || apiUrl('webhooks/gitlab/')} onFocus={event => event.target.select()} /></label>
            <div className="onboarding-note"><AlertTriangle size={17} /><span>未配置或无法核对时仍可创建项目，稍后继续配置。Webhook 的令牌和实际投递需要在 GitLab 验证。</span></div>
          </>}
          {!editing && step === 3 && <><h3>确认创建</h3><dl className="onboarding-confirm"><div><dt>项目名称</dt><dd>{name}</dd></div><div><dt>负责人</dt><dd>{owner || '未分配'}</dd></div><div><dt>仓库</dt><dd>{repositories.map(repository => <span key={repository.path}>{repository.path}</span>)}</dd></div><div><dt>连接核对</dt><dd>{check ? '已完成检查，请按结果继续配置' : '尚未完成检查，可创建后配置'}</dd></div></dl>{defaults}<p className="side-note">创建会保存项目和仓库关系。满足配置条件后才可开始评审。</p></>}
          {error && <div className="form-error" role="alert"><AlertTriangle size={15} />{error}</div>}
        </fieldset>}
      </div>
      <div className="drawer-footer">{created ? <><button className="button button-secondary" onClick={onClose}>完成</button>{onContinue && <button className="button button-primary" onClick={() => onContinue(created.id, 'models')}>继续配置模型</button>}</> : <><button className="button button-secondary" disabled={busy} onClick={() => { if (editing || step === 1) onClose(); else { invalidateCheck(); setError(''); setStep(value => value - 1) } }}>{editing || step === 1 ? '取消' : '上一步'}</button>
        {editing || step === 3 ? <button className="button button-primary" disabled={busy} onClick={() => void submit()}><Save size={15} />{busy ? '保存中…' : editing ? '保存项目' : '创建项目'}</button> : <button className="button button-primary" disabled={busy || (step === 2 && checking)} onClick={() => { if (step === 1) { if (validate()) { setStep(2); void runCheck() } } else { setError(''); setStep(3) } }}>{step === 1 ? '下一步：连接检查' : '下一步：确认创建'}</button>}</>}
      </div>
    </Dialog.Content></Dialog.Portal></Dialog.Root>
}
