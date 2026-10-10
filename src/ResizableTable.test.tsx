import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ResizableTable } from './ResizableTable'

const columns = [{ title: 'MR', width: 300, minWidth: 120 }, { title: '状态', width: 100, minWidth: 80 }, { title: '操作', width: 180, minWidth: 160 }]
function Sample({ id = 'sample', onRow = () => {} }: { id?: string; onRow?: () => void }) {
  return <ResizableTable tableId={id} label="测试表格" columns={columns} stickyLastColumn><tbody><tr onClick={onRow}><td>长标题</td><td>未评审</td><td><button>评审</button></td></tr></tbody></ResizableTable>
}
function handle(title = 'MR') { return screen.getByRole('separator', { name: `调整${title}列宽` }) }
function width(title = 'MR') { return Number(handle(title).getAttribute('aria-valuenow')) }
function drag(title: string, delta: number, end: 'pointerUp' | 'pointerCancel' = 'pointerUp') {
  const target = handle(title)
  fireEvent.pointerDown(target, { button: 0, pointerId: 1, clientX: 300 })
  fireEvent.pointerMove(target, { pointerId: 1, clientX: 300 + delta })
  fireEvent[end](target, { pointerId: 1, clientX: 300 + delta })
}
beforeEach(() => {
  localStorage.clear()
  class TestPointerEvent extends MouseEvent {
    pointerId: number
    constructor(type: string, options: PointerEventInit = {}) { super(type, options); this.pointerId = options.pointerId ?? 1 }
  }
  vi.stubGlobal('PointerEvent', TestPointerEvent)
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('exposes a labelled table and keyboard accessible resize handles for every column', () => {
  render(<Sample />)
  expect(screen.getByRole('table', { name: '测试表格' })).toBeInTheDocument()
  expect(screen.getAllByRole('separator')).toHaveLength(3)
  expect(handle()).toHaveAttribute('aria-orientation', 'vertical')
  expect(handle()).toHaveAttribute('tabindex', '0')
  expect(screen.getByRole('columnheader', { name: 'MR' })).toBeInTheDocument()
  expect(screen.getByText(/拖动表头分隔线/)).toBeInTheDocument()
})
it('resizes just the selected column and keeps row actions functional', async () => {
  const row = vi.fn()
  render(<Sample onRow={row} />)
  drag('MR', 70)
  expect(width()).toBe(370)
  expect(width('状态')).toBe(100)
  expect(width('操作')).toBe(180)
  expect(row).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '评审' }))
  expect(row).toHaveBeenCalledOnce()
})
it('bounds column width to prevent collapsed or enormous columns', () => {
  render(<Sample />)
  drag('操作', -1000)
  expect(width('操作')).toBe(160)
  drag('MR', 10000)
  expect(width()).toBe(2000)
})
it('ignores other pointer ids and non-primary mouse buttons', () => {
  render(<Sample />)
  fireEvent.pointerDown(handle(), { button: 2, pointerId: 1, clientX: 300 })
  fireEvent.pointerMove(handle(), { pointerId: 1, clientX: 500 })
  expect(width()).toBe(300)
  fireEvent.pointerDown(handle(), { button: 0, pointerId: 1, clientX: 300 })
  fireEvent.pointerMove(handle(), { pointerId: 2, clientX: 500 })
  fireEvent.pointerUp(handle(), { pointerId: 2 })
  expect(width()).toBe(300)
  fireEvent.pointerMove(handle(), { pointerId: 1, clientX: 340 })
  fireEvent.pointerUp(handle(), { pointerId: 1 })
  expect(width()).toBe(340)
})
it.each(['pointerCancel', 'lostPointerCapture'] as const)('restores the previous width when a drag ends via %s', event => {
  render(<Sample />)
  drag('MR', 30)
  fireEvent.pointerDown(handle(), { button: 0, pointerId: 1, clientX: 300 })
  fireEvent.pointerMove(handle(), { pointerId: 1, clientX: 500 })
  fireEvent[event](handle(), { pointerId: 1 })
  expect(width()).toBe(330)
  expect(localStorage.length).toBe(1)
})
it('Escape cancels an active drag without saving a layout preference', () => {
  render(<Sample />)
  fireEvent.pointerDown(handle(), { button: 0, pointerId: 1, clientX: 300 })
  fireEvent.pointerMove(handle(), { pointerId: 1, clientX: 500 })
  expect(width()).toBe(500)
  fireEvent.keyDown(handle(), { key: 'Escape' })
  expect(width()).toBe(300)
  expect(localStorage.length).toBe(0)
})
it('remembers committed widths on remount and isolates preferences by table', () => {
  const first = render(<Sample />)
  drag('MR', 45)
  first.unmount()
  const second = render(<Sample />)
  expect(width()).toBe(345)
  second.unmount()
  const other = render(<Sample id="other" />)
  expect(width()).toBe(300)
  drag('状态', 20)
  other.unmount()
  render(<Sample />)
  expect(width()).toBe(345)
  expect(width('状态')).toBe(100)
})
it('loads the correct preference when tableId changes in the same React position', () => {
  const { rerender } = render(<Sample />)
  drag('MR', 50)
  rerender(<Sample id="other" />)
  expect(width()).toBe(300)
  rerender(<Sample />)
  expect(width()).toBe(350)
})
it('supports keyboard adjustment with coarse steps and bounds', async () => {
  render(<Sample />)
  await userEvent.click(handle())
  await userEvent.keyboard('{ArrowRight}{Shift>}{ArrowRight}{/Shift}{ArrowLeft}')
  expect(width()).toBe(350)
  await userEvent.keyboard('{Home}')
  expect(width()).toBe(120)
  await userEvent.keyboard('{End}')
  expect(width()).toBe(2000)
})
it('restores all defaults and removes the saved preference', async () => {
  render(<Sample />)
  drag('MR', 100)
  drag('状态', 50)
  await userEvent.click(screen.getByRole('button', { name: '恢复默认列宽' }))
  expect(width()).toBe(300)
  expect(width('状态')).toBe(100)
  expect(localStorage.length).toBe(0)
})
it('double clicking a separator restores just that column', () => {
  render(<Sample />)
  drag('MR', 80)
  drag('状态', 20)
  fireEvent.doubleClick(handle())
  expect(width()).toBe(300)
  expect(width('状态')).toBe(120)
})
it('does not save an unfinished drag when the table is removed', () => {
  const { unmount } = render(<Sample />)
  fireEvent.pointerDown(handle(), { button: 0, pointerId: 1, clientX: 300 })
  fireEvent.pointerMove(handle(), { pointerId: 1, clientX: 500 })
  unmount()
  render(<Sample />)
  expect(width()).toBe(300)
  expect(localStorage.length).toBe(0)
})
it('synchronizes the top scrollbar and table viewport, and resets horizontal position', async () => {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(400)
  render(<Sample />)
  const viewport = screen.getByRole('region', { name: '测试表格，可横向滚动' })
  const top = viewport.previousElementSibling as HTMLElement
  top.scrollLeft = 100
  fireEvent.scroll(top)
  expect(viewport.scrollLeft).toBe(100)
  viewport.scrollLeft = 180
  fireEvent.scroll(viewport)
  expect(top.scrollLeft).toBe(180)
  await userEvent.click(screen.getByRole('button', { name: '恢复默认列宽' }))
  expect(viewport.scrollLeft).toBe(0)
})
it.each(['not json', '{"version":1,"widths":[0,100,180]}', '{"version":1,"widths":[300]}', '{"version":99,"widths":[400,100,180]}'])('ignores malformed or obsolete saved data: %s', saved => {
  const first = render(<Sample />)
  drag('MR', 50)
  const key = localStorage.key(0)!
  first.unmount()
  localStorage.setItem(key, saved)
  render(<Sample />)
  expect(width()).toBe(300)
})
it('still adjusts and resets when browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked') })
  render(<Sample />)
  drag('MR', 20)
  expect(width()).toBe(320)
  await userEvent.click(screen.getByRole('button', { name: '恢复默认列宽' }))
  expect(width()).toBe(300)
  expect(within(screen.getByRole('table')).getByRole('button', { name: '评审' })).toBeEnabled()
})
