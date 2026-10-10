import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Plus, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { api } from './api'
import type { ManagedUser, ProjectMember } from './api'
import { ActionMenu } from './ActionMenu'
import { ResizableTable } from './ResizableTable'
import { tableColumns } from './tableColumns'
import './identity.css'

type Props = { projectId: string; projectName: string; canManage: boolean; canCreateAccounts?: boolean; currentUserId: number; onAccessChange: () => void }
type Action = { kind: 'add' } | { kind: 'edit' | 'remove'; member: ProjectMember }
const errorText = (reason: unknown) => reason instanceof Error ? reason.message : '请求失败，请重试'

export function ProjectMembers(props: Props) {
  // Changing scope or authority must remove old rows, forms and pending callbacks synchronously.
  return <MembersContent key={`${props.projectId}:${props.canManage}:${props.canCreateAccounts ?? false}:${props.currentUserId}`} {...props} />
}

function MembersContent({ projectId, projectName, canManage, canCreateAccounts = false, currentUserId, onAccessChange }: Props) {
  const [rows, setRows] = useState<ProjectMember[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [query, setQuery] = useState('')
  const [role, setRole] = useState('')
  const [action, setAction] = useState<Action | null>(null)
  const generation = useRef(0)
  const trigger = useRef<HTMLElement | null>(null)
  const addTrigger = useRef<HTMLButtonElement>(null)
  async function load() {
    const request = ++generation.current
    setLoading(true); setError('')
    try {
      const result = await api.listProjectMembers(projectId)
      if (request === generation.current) setRows(result)
    } catch (reason) { if (request === generation.current) setError(errorText(reason)) }
    finally { if (request === generation.current) setLoading(false) }
  }
  useEffect(() => { void load(); return () => { generation.current++ } }, [projectId])
  function open(next: Action, returnTarget: HTMLElement | null) { trigger.current = returnTarget; setMessage(''); setAction(next) }
  function close(createdAccount?: ManagedUser) {
    if (createdAccount) setMessage(`内置账号 ${createdAccount.username} 已创建，尚未加入项目。账号已保留，可通过“添加已有账号”继续添加。`)
    setAction(null)
  }
  function restoreFocus() {
    const target = trigger.current?.isConnected ? trigger.current : addTrigger.current
    if (target?.isConnected) target.focus()
  }
  const filtered = rows.filter(row => (!role || row.role === role) && `${row.username} ${row.display_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  return <section className="identity-page" aria-label="项目成员权限">
    <header className="identity-heading"><div><h2>成员权限管理 <span className="identity-scope">项目作用域</span></h2><p>{projectName} · 项目角色仅在本项目生效，不授予或删除全局内置账号。</p></div>
      {canManage && <button ref={addTrigger} className="button button-primary" disabled={loading || !!error} onClick={event => open({ kind: 'add' }, event.currentTarget)}><Plus size={16} />添加项目成员</button>}
    </header>
    <div className="identity-rules"><ShieldCheck size={21} /><div><strong>添加成员机制</strong><p>{canCreateAccounts && canManage ? '优先添加已有启用账号；平台管理员也可先创建普通内置账号，再确认加入本项目。' : '输入已存在且已启用的内置账号用户名；全局账号由平台管理员管理。'}</p></div><div><strong>权限与隔离边界</strong><p>项目管理员管理本项目成员与策略；项目成员可查看配置并执行允许的评审操作。</p></div><div><strong>管理员保护</strong><p>移除或降级最后一名启用的项目管理员前，须先指定替代管理员。</p></div></div>
    {!canManage && <p className="identity-scope">当前为只读成员权限，可刷新列表，不能修改成员关系。</p>}
    {message && <p role="status">{message}</p>}
    <div className="identity-card"><div className="identity-toolbar">
      <input type="search" aria-label="搜索项目成员" placeholder="搜索用户名或显示名…" value={query} onChange={event => setQuery(event.target.value)} />
      <select aria-label="筛选项目角色" value={role} onChange={event => setRole(event.target.value)}><option value="">全部项目角色</option><option value="admin">项目管理员</option><option value="member">项目成员</option></select>
      <span>{loading || error ? '—' : `共 ${rows.length} 位项目成员`}</span><button className="button button-secondary" disabled={loading || !!action} onClick={() => void load()} aria-label="刷新成员列表"><RefreshCw size={15} /></button>
    </div>
    {loading ? <p className="identity-empty" role="status">正在加载项目成员…</p> : error ? <div className="identity-empty" role="alert"><p>{error}</p><button className="button button-secondary" onClick={() => void load()}>重试加载</button></div> : <ResizableTable tableId="project-members" label="项目成员" columns={tableColumns.projectMembers} stickyLastColumn><tbody>
      {filtered.map(row => <tr key={row.id}><td><strong>{row.display_name || row.username}</strong><small className="identity-subtext">{row.username}{row.user_id === currentUserId ? ' · 当前用户' : ''}</small></td><td><span className={`identity-badge ${row.role}`}>{row.role === 'admin' ? '项目管理员' : '项目成员'}</span></td><td>{row.platform_role === 'admin' ? '平台管理员' : '平台成员'}</td><td>{row.is_active ? '已启用' : '已停用'}</td><td>{canManage ? <MemberActions member={row} onAction={open} /> : '只读'}</td></tr>)}
      {!filtered.length && <tr><td colSpan={5} className="identity-empty">{rows.length ? '没有匹配的项目成员' : '尚未添加项目成员'}</td></tr>}
    </tbody></ResizableTable>}</div>
    {action && <MemberDrawer action={action} projectId={projectId} projectName={projectName} canCreateAccounts={canCreateAccounts} onClose={close} restoreFocus={restoreFocus} onSaved={result => {
      generation.current++
      if (action.kind === 'remove') setRows(previous => previous.filter(row => row.id !== action.member.id))
      else if (result) setRows(previous => action.kind === 'add' ? [...previous, result] : previous.map(row => row.id === result.id ? result : row))
      setMessage(action.kind === 'remove' ? '已移除项目成员，全局账号保留' : '项目成员已更新')
      close()
      if (action.kind !== 'add' && action.member.user_id === currentUserId) onAccessChange()
    }} />}
  </section>
}

function MemberActions({ member, onAction }: { member: ProjectMember; onAction: (action: Action, trigger: HTMLElement | null) => void }) {
  const container = useRef<HTMLDivElement>(null)
  const name = member.display_name || member.username
  function open(kind: 'edit' | 'remove') { onAction({ kind, member }, container.current?.querySelector('button') ?? null) }
  return <div ref={container} className="identity-member-actions"><ActionMenu label={`${name} 的更多操作`} title="更多操作">
    <button type="button" onClick={() => open('edit')} aria-label={`修改 ${name} 的角色`}>修改角色</button>
    <button type="button" className="identity-danger" onClick={() => open('remove')} aria-label={`移除 ${name}`}>移除项目成员</button>
  </ActionMenu></div>
}

function MemberDrawer({ action, projectId, projectName, canCreateAccounts, onClose, restoreFocus, onSaved }: {
  action: Action; projectId: string; projectName: string; canCreateAccounts: boolean;
  onClose: (createdAccount?: ManagedUser) => void; restoreFocus: () => void; onSaved: (result?: ProjectMember) => void;
}) {
  const panel = useRef<HTMLDivElement>(null)
  const active = useRef(true)
  const pending = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<'existing' | 'create'>('existing')
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [createdAccount, setCreatedAccount] = useState<ManagedUser | null>(null)
  const [role, setRole] = useState<ProjectMember['role']>(action.kind === 'add' ? 'member' : action.member.role)
  const creating = action.kind === 'add' && mode === 'create' && !createdAccount
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  useEffect(() => { if (createdAccount) panel.current?.querySelector<HTMLElement>('select')?.focus() }, [createdAccount])
  function close() { if (!pending.current) onClose(createdAccount ?? undefined) }
  function changeMode(next: 'existing' | 'create') {
    if (pending.current || createdAccount) return
    setMode(next); setError(''); setPassword('')
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (pending.current) return
    pending.current = true; setBusy(true); setError('')
    try {
      if (creating) {
        const account = await api.createUser({ username: username.trim(), display_name: displayName.trim(), password, platform_role: 'member' })
        if (active.current) { setCreatedAccount(account); setPassword('') }
      } else {
        const result = action.kind === 'add' ? await api.addProjectMember(projectId, { username: createdAccount?.username ?? username.trim(), role }) : action.kind === 'edit' ? await api.updateProjectMember(projectId, action.member.id, role) : await api.removeProjectMember(projectId, action.member.id)
        if (active.current) onSaved(result || undefined)
      }
    } catch (reason) { if (active.current) setError(errorText(reason)) }
    finally { pending.current = false; if (active.current) setBusy(false) }
  }
  const title = action.kind === 'add' ? '添加项目成员' : action.kind === 'edit' ? '修改项目角色' : '移除项目成员'
  return <Dialog.Root open onOpenChange={open => { if (!open) close() }}><Dialog.Portal>
    <Dialog.Overlay className="identity-backdrop identity-drawer-backdrop identity-member-overlay" />
    <Dialog.Content className="identity-dialog identity-drawer identity-member-drawer" ref={panel}
      onOpenAutoFocus={event => { event.preventDefault(); (panel.current?.querySelector<HTMLElement>('input, select') ?? panel.current?.querySelector<HTMLElement>('button'))?.focus() }}
      onCloseAutoFocus={event => { event.preventDefault(); restoreFocus() }}
      onEscapeKeyDown={event => { if (pending.current) event.preventDefault() }}
      onPointerDownOutside={event => { if (pending.current) event.preventDefault() }}
      onInteractOutside={event => { if (pending.current) event.preventDefault() }}>
    <header><Dialog.Title asChild><h2>{title}</h2></Dialog.Title><button type="button" disabled={busy} onClick={close} aria-label="关闭成员操作"><X size={18} /></button></header>
    <Dialog.Description asChild><p>{projectName} · {action.kind === 'remove' ? '撤销本项目成员关系，保留全局账号及评审记录。' : action.kind === 'edit' ? '项目角色仅在当前项目生效。' : '添加账号到本项目，并分配项目角色。'}</p></Dialog.Description>
    <form onSubmit={submit} aria-busy={busy}>
      {action.kind === 'add' ? <>
        {canCreateAccounts && <div className="identity-member-modes" role="group" aria-label="添加方式">
          <button type="button" aria-pressed={mode === 'existing'} disabled={busy || !!createdAccount} onClick={() => changeMode('existing')}>添加已有账号</button>
          <button type="button" aria-pressed={mode === 'create'} disabled={busy || !!createdAccount} onClick={() => changeMode('create')}>创建普通内置账号</button>
        </div>}
        {createdAccount ? <div className="identity-created-account" role="status"><strong>内置账号 {createdAccount.username} 已创建，尚未加入项目</strong><p>{createdAccount.display_name} · 平台普通用户</p><p>确认添加后才会获得本项目角色。添加失败或关闭抽屉时账号会保留，可通过“添加已有账号”继续添加。</p></div> : creating ? <>
          <p>第 1 步：创建普通内置账号。创建完成后，需单独确认添加到本项目。</p>
          <label className="field">用户名<input required maxLength={150} autoComplete="off" disabled={busy} value={username} onChange={event => setUsername(event.target.value)} /></label>
          <label className="field">显示名<input required maxLength={150} disabled={busy} value={displayName} onChange={event => setDisplayName(event.target.value)} /></label>
          <label className="field">初始密码<input type="password" required minLength={10} maxLength={4096} autoComplete="new-password" disabled={busy} value={password} onChange={event => setPassword(event.target.value)} /></label>
          <p>平台角色固定为普通用户。密码至少 10 位，避免常见密码、纯数字和与账号相似的内容。</p>
        </> : <><p>输入已存在且已启用的内置账号用户名。</p><label className="field">已有账号用户名<input required maxLength={150} autoComplete="off" disabled={busy} value={username} onChange={event => setUsername(event.target.value)} /></label></>}
      </> : <p>操作账号：{action.member.username}。{action.kind === 'remove' ? '平台管理员仍保留平台级访问权限。移除最后一名启用的项目管理员前，须先指定替代管理员。' : '若修改自己的角色，保存后会重新加载权限。降级最后一名启用的项目管理员前，须先指定替代管理员。'}</p>}
      {action.kind !== 'remove' && !creating && <label className="field">项目角色<select disabled={busy} value={role} onChange={event => setRole(event.target.value as ProjectMember['role'])}><option value="member">项目成员</option><option value="admin">项目管理员</option></select></label>}
      {error && <p role="alert" className="identity-danger">{error}</p>}
      <footer><button type="button" className="button button-secondary" disabled={busy} onClick={close}>取消</button><button type="submit" className={`button button-primary ${action.kind === 'remove' ? 'identity-remove-confirm' : ''}`} disabled={busy}>{busy ? creating ? '正在创建…' : '正在保存…' : creating ? '确认创建账号' : action.kind === 'add' ? '确认添加到项目' : action.kind === 'edit' ? '确认修改' : '确认移除'}</button></footer>
    </form></Dialog.Content>
  </Dialog.Portal></Dialog.Root>
}
