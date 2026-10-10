import { useState } from 'react'
import type { ReactNode } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { ChevronDown } from 'lucide-react'
import './menus.css'

export function ActionMenu({ label, title, className = '', children }: { label: string; title: ReactNode; className?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return <Popover.Root open={open} onOpenChange={setOpen} modal={false}>
    <Popover.Trigger asChild><button type="button" className={`action-menu-trigger ${className}`} aria-label={label} onClick={event => event.stopPropagation()}>{title}<ChevronDown size={14} /></button></Popover.Trigger>
    <Popover.Portal><Popover.Content aria-label={`${label}选项`} className="action-menu-panel" sideOffset={6} align={className === 'workspace-switcher' ? 'start' : 'end'} onClick={event => {
      event.stopPropagation()
      if ((event.target as HTMLElement).closest('button')) setOpen(false)
    }}>{children}</Popover.Content></Popover.Portal>
  </Popover.Root>
}
