import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './test-app'
import { ModelsPageConnected } from './App'
import { GlobalPrompts } from './GlobalPrompts'
import { api } from './api'
import type { PromptConfig } from './api'

beforeEach(() => window.history.replaceState({}, '', '/'))
afterEach(() => vi.restoreAllMocks())

const prompts: PromptConfig = {
  general_prompt: '通用要求', review_prompt: '审查要求', improve_prompt: '改进要求',
  security_prompt: '安全要求', reliability_prompt: '可靠性要求', style_prompt: '风格要求',
}

it('keeps edits across prompt tabs and persists the complete configuration', async () => {
  vi.spyOn(api, 'promptConfig').mockResolvedValue(prompts)
  const save = vi.spyOn(api, 'updatePromptConfig').mockImplementation(payload => Promise.resolve(payload as PromptConfig))
  const dirty = vi.fn()
  render(<GlobalPrompts onDirtyChange={dirty} onNotify={vi.fn()} />)
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('通用评审要求提示词'), '补充')
  await user.click(screen.getByRole('tab', { name: '/improve 改进' }))
  await user.type(screen.getByLabelText('/improve 改进提示词'), '优化')
  await user.click(screen.getByRole('tab', { name: '通用评审要求' }))
  expect(screen.getByLabelText('通用评审要求提示词')).toHaveValue('通用要求补充')
  await user.click(screen.getByRole('button', { name: '保存全局提示词' }))
  await screen.findByText('全局提示词已保存，对新触发的评审生效。')
  expect(save).toHaveBeenCalledWith({ ...prompts, general_prompt: '通用要求补充', improve_prompt: '改进要求优化' })
  expect(dirty).toHaveBeenLastCalledWith(false)
})

it('retains an edited prompt when saving fails', async () => {
  vi.spyOn(api, 'promptConfig').mockResolvedValue(prompts)
  vi.spyOn(api, 'updatePromptConfig').mockRejectedValue(new Error('后端保存失败'))
  render(<GlobalPrompts onDirtyChange={vi.fn()} onNotify={vi.fn()} />)
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('通用评审要求提示词'), '草稿')
  await user.click(screen.getByRole('button', { name: '保存全局提示词' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('后端保存失败')
  expect(screen.getByLabelText('通用评审要求提示词')).toHaveValue('通用要求草稿')
  expect(screen.getByRole('button', { name: '保存全局提示词' })).toBeEnabled()
})

it('requires a successful read before allowing edits and retries a failed load', async () => {
  vi.spyOn(api, 'promptConfig').mockRejectedValueOnce(new Error('读取失败')).mockResolvedValueOnce(prompts)
  render(<GlobalPrompts onDirtyChange={vi.fn()} onNotify={vi.fn()} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('全局提示词加载失败')
  expect(screen.queryByRole('button', { name: '保存全局提示词' })).not.toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole('button', { name: '重新加载提示词' }))
  expect(await screen.findByLabelText('通用评审要求提示词')).toHaveValue('通用要求')
})

it('exposes platform prompt settings in the workspace navigation', async () => {
  vi.spyOn(api, 'promptConfig').mockResolvedValue(prompts)
  render(<App />)
  const entry = screen.getByRole('button', { name: '提示词配置' })
  expect(entry).toBeInTheDocument()
  await userEvent.setup().click(entry)
  expect(window.location.pathname).toBe('/platform/settings/prompts')
})

it('reloads current saved prompts when refreshing the platform page', async () => {
  const load = vi.spyOn(api, 'promptConfig').mockResolvedValue(prompts)
  render(<App />)
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '提示词配置' }))
  await screen.findByLabelText('通用评审要求提示词')
  load.mockResolvedValue({ ...prompts, general_prompt: '新的平台要求' })
  await user.click(screen.getByRole('button', { name: '刷新数据' }))
  expect(load).toHaveBeenCalledTimes(2)
  expect(await screen.findByDisplayValue('新的平台要求')).toBeInTheDocument()
})

it('describes discovered models as coming from the configured gateway', () => {
  render(<ModelsPageConnected initial={{ gateway_type: 'Anthropic 兼容', model: 'claude-opus-4-6-thinking' }} modelOptions={['model-a', 'model-b']} scope="global" projectName="项目" onScopeChange={vi.fn()} tested onTest={vi.fn()} onSave={vi.fn()} />)
  expect(screen.getByText('已从当前模型网关获取 2 个模型')).toBeInTheDocument()
  expect(screen.queryByText(/已从 CCSwitch 获取/)).not.toBeInTheDocument()
})
