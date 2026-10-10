import { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import './identity.css'

export function InheritanceDialog({ busy, error, onCancel, onConfirm }: { busy: boolean; error: string; onCancel: () => void; onConfirm: () => void }) {
  const title = useId()
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => { panel.current?.querySelector('button')?.focus() }, [])
  return createPortal(<div className="identity-backdrop"><div className="identity-dialog" ref={panel} role="dialog" aria-modal="true" aria-labelledby={title} tabIndex={-1} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); if (!busy) onCancel() }
    if (event.key === 'Tab') {
      const controls = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
      const first = controls[0], last = controls[controls.length - 1]
      if (!first) { event.preventDefault(); panel.current?.focus() }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
  }}><h2 id={title}>恢复全局继承？</h2><p>确认后，当前项目将使用平台默认配置。已保存的项目覆盖值将保留，平台默认配置不会被修改。</p><p>本页尚未保存的修改不会提交；成功切换后将显示平台默认值。取消会保留当前编辑内容。</p>{error && <p role="alert" className="identity-danger">{error}</p>}<footer><button className="button button-secondary" disabled={busy} onClick={onCancel}>取消</button><button className="button button-primary" disabled={busy} onClick={onConfirm}>{busy ? '正在恢复…' : '确认恢复继承'}</button></footer></div></div>, document.body)
}
