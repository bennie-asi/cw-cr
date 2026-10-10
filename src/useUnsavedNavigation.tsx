import { useCallback, useEffect, useId, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { createPortal } from 'react-dom'
import './identity.css'

export function useUnsavedNavigation(dirty: MutableRefObject<boolean>) {
  const [open, setOpen] = useState(false)
  const pending = useRef<(() => void) | null>(null)
  const cancelPending = useRef<(() => void) | undefined>(undefined)
  const trigger = useRef<HTMLElement | null>(null)
  const panel = useRef<HTMLDivElement>(null)
  const title = useId()
  const request = useCallback((action: () => void, onCancel?: () => void) => {
    if (!dirty.current) { action(); return }
    if (pending.current) { onCancel?.(); return }
    trigger.current = document.activeElement as HTMLElement
    pending.current = action; cancelPending.current = onCancel; setOpen(true)
  }, [dirty])
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => { if (dirty.current) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', unload)
    return () => window.removeEventListener('beforeunload', unload)
  }, [dirty])
  useEffect(() => { if (open) panel.current?.querySelector('button')?.focus() }, [open])
  function cancel() { pending.current = null; const callback = cancelPending.current; cancelPending.current = undefined; setOpen(false); trigger.current?.focus(); callback?.() }
  function discard() { const action = pending.current; pending.current = null; cancelPending.current = undefined; dirty.current = false; setOpen(false); action?.() }
  const dialog = open ? createPortal(<div className="identity-backdrop"><div ref={panel} className="identity-dialog" role="dialog" aria-modal="true" aria-labelledby={title} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); cancel() }
    if (event.key === 'Tab') {
      const controls = [...event.currentTarget.querySelectorAll('button')]
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
  }}><h2 id={title}>有未保存的修改</h2><p>离开会放弃本页尚未保存的输入，不会自动保存。正在执行的服务端请求不会因此撤销。</p><footer><button className="button button-secondary" onClick={cancel}>继续编辑</button><button className="button button-primary" onClick={discard}>放弃修改并离开</button></footer></div></div>, document.body) : null
  return { request, dialog }
}
