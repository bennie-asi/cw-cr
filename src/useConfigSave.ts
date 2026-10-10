import { useEffect, useRef, useState } from 'react'

type Save = (payload: Record<string, unknown>) => void | boolean | Promise<void | boolean>

// Lock synchronously, before React can render a disabled button on double click.
export function useConfigSave(onSave: Save, onSuccess: () => void) {
  const pending = useRef(false)
  const active = useRef(true)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])

  async function save(payload: Record<string, unknown>) {
    if (pending.current) return
    pending.current = true
    setSaving(true)
    setSaveError('')
    try {
      const result = await onSave(payload)
      if (!active.current) return
      if (result === false) setSaveError('保存失败，输入内容已保留，请重试')
      else onSuccess()
    } catch {
      if (active.current) setSaveError('保存失败，输入内容已保留，请重试')
    } finally {
      pending.current = false
      if (active.current) setSaving(false)
    }
  }

  return { save, saving, saveError }
}
