import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createPortal } from 'react-dom'
import * as Popover from '@radix-ui/react-popover'
import { ChevronDown, UserRound, X } from 'lucide-react'
import { api } from './api'
import type { SessionUser } from './api'
import './account.css'

export function AccountMenu({ user, onLogout, label, className = '' }: {
  user: SessionUser; onLogout: () => Promise<void>; label: string; className?: string
}) {
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const focusMenu = () => trigger.current?.focus()
  function closeMenu() { setOpen(false) }
  async function logout() {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    setError('')
    setMessage('')
    try { await onLogout() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '退出失败，请重试') }
    finally { pending.current = false; setBusy(false) }
  }
  return <>
    <Popover.Root open={open} onOpenChange={setOpen} modal={false}>
      <Popover.Trigger asChild><button ref={trigger} type="button" className={`action-menu-trigger ${className}`} aria-label={label}><UserRound className="account-trigger-icon" size={18} /><span className="account-name">{user.display_name || user.username}</span><ChevronDown className="account-chevron" size={14} /></button></Popover.Trigger>
      <Popover.Portal><Popover.Content aria-label={`${label}选项`} className="action-menu-panel account-menu-panel" side={className === 'sidebar-user' ? 'top' : 'bottom'} align={className === 'sidebar-user' ? 'start' : 'end'} sideOffset={6}><>
        <strong>{user.username}</strong><p>{user.platform_role === 'admin' ? '平台管理员' : '平台成员'}</p>
        <button disabled={busy} onClick={() => { closeMenu(); setError(''); setMessage(''); setPasswordOpen(true) }}>修改密码</button>
        <button disabled={busy} onClick={() => { void logout() }}>{busy ? '正在退出…' : '退出登录'}</button>
      </></Popover.Content></Popover.Portal>
    </Popover.Root>
    {error && <div className="account-feedback account-error" role="alert">{error}<button aria-label="关闭账号提示" onClick={() => setError('')}><X size={14} /></button></div>}
    {message && <div className="account-feedback" role="status">{message}<button aria-label="关闭账号提示" onClick={() => setMessage('')}><X size={14} /></button></div>}
    {passwordOpen && <PasswordDialog onClose={() => { setPasswordOpen(false); focusMenu() }} onSuccess={() => { setMessage('密码已更新'); setPasswordOpen(false); focusMenu() }} />}
  </>
}

export function PasswordDialog({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const titleId = useId()
  const panel = useRef<HTMLDivElement>(null)
  const [current, setCurrent] = useState('')
  const [replacement, setReplacement] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  useEffect(() => { panel.current?.querySelector('input')?.focus() }, [])
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (pending.current) return
    if (replacement !== confirmation) { setError('两次输入的新密码不一致'); return }
    pending.current = true
    setBusy(true)
    setError('')
    try { await api.changePassword(current, replacement); onSuccess() }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : '密码修改失败，请重试')
      setCurrent(''); setReplacement(''); setConfirmation('')
    } finally { pending.current = false; setBusy(false) }
  }
  return createPortal(<div className="account-modal-backdrop">
    <div ref={panel} className="account-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); if (!pending.current) onClose() }
      if (event.key === 'Tab') {
        const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('input:not(:disabled), button:not(:disabled)')]
        const first = controls[0], last = controls[controls.length - 1]
        if (!first) { event.preventDefault(); panel.current?.focus() }
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }}>
      <h2 id={titleId}>修改密码</h2><p>更新当前账号的登录密码，其他设备需要重新登录。</p>
      <form onSubmit={submit} aria-busy={busy}>
        <label className="field">当前密码<input autoComplete="current-password" type="password" required maxLength={4096} disabled={busy} value={current} onChange={event => setCurrent(event.target.value)} /></label>
        <label className="field">新密码<input autoComplete="new-password" type="password" required minLength={10} maxLength={4096} disabled={busy} value={replacement} onChange={event => setReplacement(event.target.value)} /></label>
        <label className="field">确认新密码<input autoComplete="new-password" type="password" required minLength={10} maxLength={4096} disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
        <small>至少 10 位，避免常见密码、纯数字和与账号相似的内容。</small>
        {error && <p className="account-error" role="alert">{error}</p>}
        <div className="account-dialog-actions"><button type="button" className="button button-secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button button-primary" disabled={busy}>{busy ? '正在保存…' : '保存密码'}</button></div>
      </form>
    </div>
  </div>, document.body)
}
