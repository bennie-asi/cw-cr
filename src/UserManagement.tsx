import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { Plus, RefreshCw, X } from 'lucide-react'
import { api } from './api'
import { PasswordDialog } from './AccountMenu'
import type { ManagedUser } from './api'
import { ResizableTable } from './ResizableTable'
import { tableColumns } from './tableColumns'
import './identity.css'

type Action = { kind: 'create' } | { kind: 'edit' | 'toggle' | 'reset'; user: ManagedUser }
const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败，请重试'

export function UserManagement({ currentUserId, onAccessChange }: { currentUserId: number; onAccessChange: () => void }) {
  const [rows, setRows] = useState<ManagedUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [query, setQuery] = useState('')
  const [action, setAction] = useState<Action | null>(null)
  const [selfPasswordOpen, setSelfPasswordOpen] = useState(false)
  const generation = useRef(0)
  const trigger = useRef<HTMLElement | null>(null)
  async function load() {
    const request = ++generation.current
    setLoading(true); setError('')
    try { const data = await api.listUsers(); if (request === generation.current) setRows(data) }
    catch (reason) { if (request === generation.current) setError(errorText(reason)) }
    finally { if (request === generation.current) setLoading(false) }
  }
  useEffect(() => { void load(); return () => { generation.current++ } }, [])
  function open(next: Action) { trigger.current = document.activeElement as HTMLElement; setMessage(''); setAction(next) }
  function close() { setAction(null); trigger.current?.focus() }
  const filtered = rows.filter(row => `${row.username} ${row.display_name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  return <section className="identity-page" aria-label="平台内置用户管理">
    <header className="identity-heading"><div><h2>平台内置账号 <span className="identity-scope">平台作用域</span></h2><p>管理内置登录账号及全平台角色，不在此分配项目管理员。</p></div><button className="button button-primary" disabled={loading || !!error} onClick={() => open({ kind: 'create' })}><Plus size={16} />添加内置用户</button></header>
    <div className="identity-scope">项目管理员是项目空间内的角色，不是平台管理员。请在对应项目的「成员权限」中配置项目角色。平台账号停用影响所有项目，账号记录不会被删除。</div>
    {message && <p role="status">{message}</p>}
    <div className="identity-card"><div className="identity-toolbar"><strong>平台内置账号列表</strong><input aria-label="搜索内置账号" type="search" placeholder="搜索用户名或显示名…" value={query} onChange={event => setQuery(event.target.value)} /><span>{loading || error ? '—' : `${rows.length} 个账号`}</span><button className="button button-secondary" aria-label="刷新账号列表" disabled={loading || !!action} onClick={() => void load()}><RefreshCw size={15} /></button></div>
      {loading ? <p className="identity-empty" role="status">正在加载内置账号…</p> : error ? <div className="identity-empty" role="alert"><p>{error}</p><button className="button button-secondary" onClick={() => void load()}>重试加载</button></div> : <ResizableTable tableId="accounts" label="内置账号" columns={tableColumns.accounts} stickyLastColumn><tbody>
        {filtered.map(row => <tr key={row.id}><td>{row.username}{row.id === currentUserId && <small className="identity-subtext">当前用户</small>}</td><td>{row.display_name}</td><td><span className={`identity-badge ${row.platform_role}`}>{row.platform_role === 'admin' ? '平台管理员' : '普通用户'}</span></td><td>{row.is_active ? '已启用' : '已停用'}</td><td>{row.last_login ? new Date(row.last_login).toLocaleString('zh-CN') : '尚未登录'}</td><td><div className="identity-actions"><button aria-label={`编辑 ${row.username}`} onClick={() => open({ kind: 'edit', user: row })}>编辑</button>{row.id !== currentUserId ? <button aria-label={`重置 ${row.username} 的密码`} onClick={() => open({ kind: 'reset', user: row })}>重置密码</button> : <button aria-label="修改当前账号密码" onClick={() => setSelfPasswordOpen(true)}>修改密码</button>}<button className={row.is_active ? 'identity-danger' : ''} aria-label={`${row.is_active ? '停用' : '启用'} ${row.username}`} onClick={() => open({ kind: 'toggle', user: row })}>{row.is_active ? '停用' : '启用'}</button></div></td></tr>)}
        {!filtered.length && <tr><td colSpan={6} className="identity-empty">{rows.length ? '没有匹配的账号' : '暂无内置账号'}</td></tr>}
      </tbody></ResizableTable>}
    </div>
    {action && <UserDialog action={action} onClose={close} onSaved={result => {
      generation.current++
      if (result) setRows(previous => action.kind === 'create' ? [...previous, result] : previous.map(row => row.id === result.id ? result : row))
      setMessage(action.kind === 'reset' ? '密码已重置，原有会话将在后续请求时失效' : '账号已保存')
      close()
      if (action.kind !== 'create' && action.user.id === currentUserId) onAccessChange()
    }} />}
    {selfPasswordOpen && <PasswordDialog onClose={() => setSelfPasswordOpen(false)} onSuccess={() => { setSelfPasswordOpen(false); setMessage('密码已更新') }} />}
  </section>
}

function UserDialog({ action, onClose, onSaved }: { action: Action; onClose: () => void; onSaved: (result?: ManagedUser) => void }) {
  const id = useId()
  const panel = useRef<HTMLDivElement>(null)
  const active = useRef(true)
  const pending = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [username, setUsername] = useState('')
  const [name, setName] = useState(action.kind === 'create' ? '' : action.user.display_name)
  const [role, setRole] = useState<ManagedUser['platform_role']>(action.kind === 'create' ? 'member' : action.user.platform_role)
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const passwordRequired = action.kind === 'create' || action.kind === 'reset'
  const unchanged = action.kind === 'edit' && name === action.user.display_name && role === action.user.platform_role
  const title = action.kind === 'create' ? '添加内置用户' : action.kind === 'edit' ? '编辑内置账号' : action.kind === 'reset' ? '重置账号密码' : action.user.is_active ? '停用内置账号' : '启用内置账号'
  useEffect(() => { active.current = true; panel.current?.querySelector<HTMLElement>('input, select, button')?.focus(); return () => { active.current = false } }, [])
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (pending.current) return
    if (unchanged && action.kind === 'edit') { onSaved(action.user); return }
    if (passwordRequired && password !== confirmation) { setError('两次输入的密码不一致'); return }
    pending.current = true; setBusy(true); setError('')
    try {
      let result: ManagedUser | undefined
      if (action.kind === 'create') result = await api.createUser({ username: username.trim(), display_name: name.trim(), password, platform_role: role })
      else if (action.kind === 'edit') result = await api.updateUser(action.user.id, {
        ...(name !== action.user.display_name ? { display_name: name.trim() } : {}),
        ...(role !== action.user.platform_role ? { platform_role: role } : {}),
      })
      else if (action.kind === 'toggle') result = await api.updateUser(action.user.id, { is_active: !action.user.is_active })
      else await api.resetUserPassword(action.user.id, password)
      if (active.current) { setPassword(''); setConfirmation(''); onSaved(result) }
    } catch (reason) { if (active.current) { setError(errorText(reason)); setPassword(''); setConfirmation('') } }
    finally { pending.current = false; if (active.current) setBusy(false) }
  }
  return createPortal(<div className={`identity-backdrop ${action.kind === 'create' ? 'identity-drawer-backdrop' : ''}`}><div className={`identity-dialog ${action.kind === 'create' ? 'identity-drawer' : ''}`} ref={panel} role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); if (!pending.current) onClose() }
    if (event.key === 'Tab') {
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('input:not(:disabled), select:not(:disabled), button:not(:disabled)')]
      const first = controls[0], last = controls[controls.length - 1]
      if (!first) { event.preventDefault(); panel.current?.focus() }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
  }}><header><h2 id={id}>{title}</h2><button aria-label="关闭账号操作" disabled={busy} onClick={onClose}><X size={18} /></button></header><form onSubmit={submit} aria-busy={busy}>
      {action.kind !== 'create' && <p>操作账号：{action.user.username}</p>}
      {action.kind === 'create' && <label className="field">用户名<input required maxLength={150} autoComplete="off" disabled={busy} value={username} onChange={event => setUsername(event.target.value)} /></label>}
      {(action.kind === 'create' || action.kind === 'edit') && <><label className="field">显示名<input required maxLength={150} disabled={busy} value={name} onChange={event => setName(event.target.value)} /></label><label className="field">平台角色<select disabled={busy} value={role} onChange={event => setRole(event.target.value as ManagedUser['platform_role'])}><option value="member">普通用户</option><option value="admin">平台管理员</option></select></label><p>{role === 'admin' ? '确认后，此账号将拥有全平台配置及所有项目的管理权限。' : '普通用户的项目访问权限由各项目成员配置决定。'}</p></>}
      {action.kind === 'toggle' && <p>{action.user.is_active ? '确认停用后，该账号无法登录且已有会话将在后续请求时失效。保留账号、项目成员关系及历史记录；最后一名有效管理员受服务端保护。' : '确认启用后，该账号可以重新登录，原有项目成员关系仍然有效。'}</p>}
      {passwordRequired && <><label className="field">{action.kind === 'create' ? '初始密码' : '新密码'}<input type="password" required minLength={10} maxLength={4096} autoComplete="new-password" disabled={busy} value={password} onChange={event => setPassword(event.target.value)} /></label><label className="field">确认密码<input type="password" required minLength={10} maxLength={4096} autoComplete="new-password" disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label><p>至少 10 位，避免常见密码、纯数字和与账号相似的内容。{action.kind === 'reset' && '重置后该账号原有会话将在后续请求时失效，请通过安全渠道交付新密码。'}</p></>}
      {error && <p className="identity-danger" role="alert">{error}</p>}
      <footer><button type="button" className="button button-secondary" disabled={busy} onClick={onClose}>取消</button><button className="button button-primary" type="submit" disabled={busy}>{busy ? '正在保存…' : action.kind === 'create' ? '确认创建' : action.kind === 'edit' ? '确认修改' : action.kind === 'reset' ? '确认重置' : action.user.is_active ? '确认停用' : '确认启用'}</button></footer>
    </form></div></div>, document.body)
}
