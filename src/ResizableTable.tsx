import { useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react'
import './resizable-table.css'

export type TableColumn = { title: string; width: number; minWidth?: number }
type Props = {
  tableId: string
  label: string
  columns: readonly TableColumn[]
  children: ReactNode
  className?: string
  stickyLastColumn?: boolean
}
const MAX_WIDTH = 2000
const minimum = (column: TableColumn) => column.minWidth ?? 80
const bounded = (width: number, column: TableColumn) => Math.round(Math.min(MAX_WIDTH, Math.max(minimum(column), width)))

function loadWidths(key: string, schema: string, columns: readonly TableColumn[]): number[] | null {
  try {
    const saved = JSON.parse(localStorage.getItem(key) || 'null')
    if (saved?.version !== 1 || saved.schema !== schema || !Array.isArray(saved.widths) || saved.widths.length !== columns.length) return null
    return saved.widths.every((width: unknown, index: number) => typeof width === 'number' && Number.isFinite(width) && width >= minimum(columns[index]) && width <= MAX_WIDTH) ? saved.widths : null
  } catch { return null }
}

export function ResizableTable(props: Props) {
  return <ResizableTableContent key={props.tableId} {...props} />
}

function ResizableTableContent({ tableId, label, columns, children, className = '', stickyLastColumn = false }: Props) {
  const key = `cwcr-table-widths:v1:${tableId}`
  const schema = columns.map(column => `${column.title}:${minimum(column)}`).join('|')
  const [customWidths, setCustomWidths] = useState<number[] | null>(() => loadWidths(key, schema, columns))
  const [containerWidth, setContainerWidth] = useState(0)
  const [resizing, setResizing] = useState<number | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const topScroll = useRef<HTMLDivElement>(null)
  const table = useRef<HTMLTableElement>(null)
  const drag = useRef<{ index: number; pointerId: number; startX: number; startWidths: number[]; currentWidths: number[]; previous: number[] | null } | null>(null)
  const defaults = columns.map(column => bounded(column.width, column))
  const spare = Math.max(0, containerWidth - defaults.reduce((sum, width) => sum + width, 0))
  defaults[0] += spare
  const widths = customWidths ?? defaults
  const total = widths.reduce((sum, width) => sum + width, 0)

  useLayoutEffect(() => {
    const element = viewport.current
    if (!element) return
    const measure = () => setContainerWidth(Math.round(element.clientWidth))
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  function save(next: number[]) {
    setCustomWidths(next)
    try { localStorage.setItem(key, JSON.stringify({ version: 1, schema, widths: next })) } catch { /* Resizing still works when browser storage is blocked or full. */ }
  }
  function measuredWidths() {
    const headers = table.current?.querySelectorAll<HTMLTableCellElement>('thead th')
    return widths.map((width, index) => Math.round(headers?.[index]?.getBoundingClientRect().width || width))
  }
  function start(event: PointerEvent<HTMLSpanElement>, index: number) {
    if (event.button !== 0 || drag.current) return
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.focus()
    const current = measuredWidths()
    drag.current = { index, pointerId: event.pointerId, startX: event.clientX, startWidths: current, currentWidths: current, previous: customWidths }
    setCustomWidths(current)
    setResizing(index)
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function move(event: PointerEvent<HTMLSpanElement>) {
    const active = drag.current
    if (!active || active.pointerId !== event.pointerId) return
    const next = [...active.startWidths]
    next[active.index] = bounded(active.startWidths[active.index] + event.clientX - active.startX, columns[active.index])
    active.currentWidths = next
    setCustomWidths(next)
  }
  function finish(event: PointerEvent<HTMLSpanElement>, cancel = false) {
    const active = drag.current
    if (!active || active.pointerId !== event.pointerId) return
    drag.current = null
    setResizing(null)
    if (cancel) setCustomWidths(active.previous)
    else save(active.currentWidths)
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }
  function keyboard(event: KeyboardEvent<HTMLSpanElement>, index: number) {
    if (event.key === 'Escape' && drag.current) {
      const active = drag.current
      drag.current = null
      setCustomWidths(active.previous)
      setResizing(null)
      if (event.currentTarget.hasPointerCapture?.(active.pointerId)) event.currentTarget.releasePointerCapture(active.pointerId)
      event.preventDefault()
      return
    }
    if (drag.current || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    event.stopPropagation()
    const next = measuredWidths()
    next[index] = event.key === 'Home' ? minimum(columns[index]) : event.key === 'End' ? MAX_WIDTH : bounded(next[index] + (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? 50 : 10), columns[index])
    save(next)
  }
  function reset() {
    drag.current = null
    setResizing(null)
    setCustomWidths(null)
    if (viewport.current) viewport.current.scrollLeft = 0
    if (topScroll.current) topScroll.current.scrollLeft = 0
    try { localStorage.removeItem(key) } catch { /* In-memory reset remains available. */ }
  }

  return <div className={`resizable-table ${stickyLastColumn ? 'resizable-table-sticky' : ''} ${resizing !== null ? 'resizable-table-dragging' : ''}`}>
    <div className="table-width-tools"><span>拖动表头分隔线调整列宽{containerWidth > 0 && total > containerWidth ? ' · 可横向滚动' : ''}</span><button type="button" onClick={reset}>恢复默认列宽</button></div>
    {containerWidth > 0 && total > containerWidth && <div className="table-width-scrollbar" ref={topScroll} aria-hidden="true" onScroll={event => { if (viewport.current) viewport.current.scrollLeft = event.currentTarget.scrollLeft }}><div style={{ width: total, height: 1 }} /></div>}
    <div className="table-wrap table-width-viewport" ref={viewport} tabIndex={0} role="region" aria-label={`${label}，可横向滚动`} onScroll={event => { if (topScroll.current) topScroll.current.scrollLeft = event.currentTarget.scrollLeft }}>
      <table ref={table} className={className} aria-label={label} style={{ width: total, minWidth: total }}>
        <colgroup>{widths.map((width, index) => <col key={index} style={{ width }} />)}</colgroup>
        <thead><tr>{columns.map((column, index) => <th key={index} scope="col" aria-label={column.title || '操作'}>
          {column.title || <span className="table-width-sr-only">操作</span>}
          <span className="table-column-resizer" role="separator" tabIndex={0} aria-label={`调整${column.title || '操作'}列宽`} aria-orientation="vertical" aria-valuemin={minimum(column)} aria-valuemax={MAX_WIDTH} aria-valuenow={Math.round(widths[index])} title="拖动调整列宽；双击恢复此列；方向键微调"
            onPointerDown={event => start(event, index)} onPointerMove={move} onPointerUp={event => finish(event)} onPointerCancel={event => finish(event, true)} onLostPointerCapture={event => finish(event, true)} onKeyDown={event => keyboard(event, index)}
            onDoubleClick={event => { event.stopPropagation(); const next = measuredWidths(); next[index] = defaults[index]; save(next) }} onClick={event => event.stopPropagation()} />
        </th>)}</tr></thead>
        {children}
      </table>
    </div>
  </div>
}
