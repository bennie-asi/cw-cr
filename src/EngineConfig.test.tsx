import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { EngineConfigPanelConnected, ModelsPageConnected } from './App'

const engines = [
  { id: 'builtin', label: '智审台内置评审', capabilities: ['行级评论'], availability: 'ready', installed: true, version: 'builtin', verified: true, selectable: true, reason: '内置引擎可用', verified_at: null },
  { id: 'pr-agent', label: 'PR-Agent', capabilities: ['结构化行级问题'], availability: 'unverified', installed: true, version: 'PR-Agent 0.46.0', verified: false, selectable: false, reason: '请先运行引擎验证', verified_at: null },
  { id: 'open-code-review', label: 'OpenCodeReview', capabilities: ['结构化行级问题'], availability: 'not-installed', installed: false, version: '', verified: false, selectable: false, reason: '未安装', verified_at: null },
  { id: 'pi-agent', label: 'Pi Agent', capabilities: ['只读代码上下文'], availability: 'ready', installed: true, version: '0.86.0', verified: true, selectable: true, reason: '', verified_at: '2026-09-23T00:00:00Z' },
]

it('keeps engine selection out of model connection payloads', async () => {
  const onSave = vi.fn()
  const user = userEvent.setup()
  render(<ModelsPageConnected initial={{ model: 'review-model' }} modelOptions={[]} scope="global" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={onSave} />)

  expect(screen.queryByLabelText('审查引擎')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '保存模型配置' }))
  expect(onSave).toHaveBeenCalledWith(expect.not.objectContaining({ engine: expect.anything() }))
})

it('separates a project engine override from the inherited platform default', async () => {
  const onSave = vi.fn().mockResolvedValue(true)
  const user = userEvent.setup()
  render(<EngineConfigPanelConnected
    initial={{ inherited: true, configured_engine: 'builtin', effective_engine: 'pi-agent', engines }}
    scope="project" projectName="chaos" onSave={onSave} onTest={vi.fn()}
  />)

  expect(screen.getAllByText('Pi Agent').length).toBeGreaterThan(0)
  expect(screen.getAllByText(/继承平台默认/).length).toBeGreaterThan(0)
  await user.click(screen.getByRole('button', { name: '自定义本项目' }))
  const selector = screen.getByLabelText('项目评审引擎')
  expect(selector).toBeEnabled()
  expect(within(selector).getByRole('option', { name: /OpenCodeReview/ })).toBeDisabled()
  await user.selectOptions(selector, 'builtin')
  await user.click(screen.getByRole('button', { name: '保存项目引擎配置' }))
  expect(onSave).toHaveBeenCalledWith({ engine: 'builtin' })
})

it('allows runtime verification without treating installation as a selectable engine', async () => {
  const onTest = vi.fn()
  const user = userEvent.setup()
  render(<EngineConfigPanelConnected
    initial={{ default_engine: 'builtin', effective_engine: 'builtin', engines }}
    scope="global" projectName="chaos" onSave={vi.fn()} onTest={onTest}
  />)

  const selector = screen.getByLabelText('平台默认评审引擎')
  expect(within(selector).getByRole('option', { name: /PR-Agent/ })).toBeDisabled()
  const prAgentRow = screen.getByRole('row', { name: /PR-Agent/ })
  await user.click(within(prAgentRow).getByRole('button', { name: '验证引擎' }))
  expect(onTest).toHaveBeenCalledWith('pr-agent')
})

it('can pin the currently inherited engine as a project override', async () => {
  const onSave = vi.fn().mockResolvedValue(true)
  render(<EngineConfigPanelConnected initial={{ inherited: true, configured_engine: 'builtin', effective_engine: 'builtin', engines }} scope="project" projectName="chaos" onSave={onSave} />)
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '自定义本项目' }))
  expect(screen.getByRole('button', { name: '保存项目引擎配置' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '保存项目引擎配置' }))
  expect(onSave).toHaveBeenCalledWith({ engine: 'builtin' })
})

it.each(['global', 'project'] as const)('keeps %s table layout editable without granting configuration authority', async scope => {
  localStorage.clear()
  const onSave = vi.fn(); const onTest = vi.fn()
  const initial = scope === 'global' ? { default_engine: 'builtin', effective_engine: 'builtin', engines } : { inherited: true, configured_engine: 'builtin', effective_engine: 'builtin', engines }
  render(<EngineConfigPanelConnected initial={initial} scope={scope} projectName="chaos" readOnly onSave={onSave} onTest={onTest} />)
  const user = userEvent.setup()
  const handle = screen.getByRole('separator', { name: '调整引擎列宽' })
  await user.click(handle)
  await user.keyboard('{ArrowRight}')
  expect(handle).toHaveAttribute('aria-valuenow', '270')
  expect(screen.getByRole('button', { name: '恢复默认列宽' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '恢复默认列宽' }))
  expect(handle).toHaveAttribute('aria-valuenow', '260')
  expect(screen.getByRole('combobox', { name: scope === 'global' ? '平台默认评审引擎' : '项目评审引擎' })).toBeDisabled()
  expect(onSave).not.toHaveBeenCalled()
  expect(onTest).not.toHaveBeenCalled()
})
