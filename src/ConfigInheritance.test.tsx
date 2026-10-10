import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { ModelsPageConnected, NotificationsPageConnected } from './App'

it.each(['notifications', 'models'])('opens inherited %s drafts from the primary modify action', async kind => {
  const onSave = vi.fn(), user = userEvent.setup()
  if (kind === 'notifications') render(<NotificationsPageConnected initial={{inherited:true, name:'平台群'}} scope="project" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={onSave} onReloadAudit={vi.fn()} />)
  else render(<ModelsPageConnected initial={{inherited:true, model:'global-model'}} modelOptions={[]} scope="project" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={onSave} />)
  const field = screen.getByLabelText(kind === 'notifications' ? '群名称' : '网关类型')
  expect(field).toBeDisabled()
  await user.click(screen.getByRole('button', {name:'修改配置'}))
  expect(field).toBeEnabled()
  expect(field).toHaveFocus()
  expect(onSave).not.toHaveBeenCalled()
})

it('does not unlock notification editing for a read-only member', async () => {
  render(<NotificationsPageConnected initial={{inherited:true}} scope="project" projectName="chaos" onScopeChange={vi.fn()} tested={false} readOnly onTest={vi.fn()} onSave={vi.fn()} onReloadAudit={vi.fn()} />)
  expect(screen.getByRole('button', {name:'修改配置'})).toBeDisabled()
  expect(screen.getByRole('button', {name:'自定义本项目'})).toBeDisabled()
  expect(screen.getByLabelText('群名称')).toBeDisabled()
})

it('requires explicit customization before editing inherited model configuration', async () => {
  const onSave = vi.fn()
  const user = userEvent.setup()
  render(<ModelsPageConnected initial={{inherited:true, model:'global-model'}} modelOptions={[]} scope="project" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={onSave} />)
  expect(screen.getByLabelText('模型 ID')).toBeDisabled()
  await user.click(screen.getByRole('button', {name:'自定义本项目'}))
  expect(screen.getByLabelText('模型 ID')).toBeEnabled()
  expect(onSave).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', {name:'保存项目模型配置'}))
  expect(onSave).toHaveBeenCalledWith(expect.objectContaining({model:'global-model'}))
})

it('does not send engine selection as part of a model connection save', async () => {
  const onSave = vi.fn(), user = userEvent.setup()
  render(<ModelsPageConnected initial={{inherited:false, engine:'pr-agent', model:'legacy-model'}} modelOptions={[]} scope="global" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={onSave} />)
  expect(screen.queryByLabelText('审查引擎')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', {name:'保存模型配置'}))
  expect(onSave).toHaveBeenCalledWith(expect.not.objectContaining({engine: expect.anything()}))
})

it('restores inheritance without submitting the override fields', async () => {
  const onSave = vi.fn()
  render(<ModelsPageConnected initial={{inherited:false, model:'private-model'}} modelOptions={[]} scope="project" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={onSave} />)
  await userEvent.setup().click(screen.getByRole('button', {name:'恢复全局继承'}))
  expect(onSave).not.toHaveBeenCalled()
  await userEvent.setup().click(within(screen.getByRole('dialog')).getByRole('button', {name:'确认恢复继承'}))
  expect(onSave).toHaveBeenCalledWith({inherited:true})
})

it('cancels restore without losing the edited model or writing configuration', async () => {
  const onSave = vi.fn(), user = userEvent.setup()
  render(<ModelsPageConnected initial={{inherited:false, model:'private-model'}} modelOptions={[]} scope="project" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={onSave} />)
  await user.type(screen.getByLabelText('模型 ID'), '-draft')
  await user.click(screen.getByRole('button', {name:'恢复全局继承'}))
  expect(screen.getByRole('dialog')).toHaveTextContent('已保存的项目覆盖值将保留')
  await user.keyboard('{Escape}')
  expect(onSave).not.toHaveBeenCalled()
  expect(screen.getByLabelText('模型 ID')).toHaveValue('private-model-draft')
  expect(screen.getByRole('button', {name:'恢复全局继承'})).toHaveFocus()
})

it('prevents duplicate restoration and retains failure feedback without changing source', async () => {
  let resolve!: (value: boolean) => void
  const onSave = vi.fn(() => new Promise<boolean>(r => { resolve = r })), user = userEvent.setup()
  render(<ModelsPageConnected initial={{inherited:false, model:'private-model'}} modelOptions={[]} scope="project" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={vi.fn()} onSave={onSave} />)
  await user.click(screen.getByRole('button', {name:'恢复全局继承'}))
  await user.dblClick(screen.getByRole('button', {name:'确认恢复继承'}))
  expect(onSave).toHaveBeenCalledOnce()
  expect(screen.getByRole('button', {name:'取消'})).toBeDisabled()
  expect(screen.getByLabelText('模型 ID')).toBeDisabled()
  await act(async () => resolve(false))
  expect(screen.getByRole('alert')).toHaveTextContent('恢复继承失败')
  await user.click(screen.getByRole('button', {name:'取消'}))
  expect(screen.getByLabelText('模型 ID')).toHaveValue('private-model')
  expect(screen.getByLabelText('模型 ID')).toBeEnabled()
})

it('confirms notification inheritance without sending a test message', async () => {
  const onSave = vi.fn().mockResolvedValue(true), onTest = vi.fn(), user = userEvent.setup()
  render(<NotificationsPageConnected initial={{inherited:false}} scope="project" projectName="chaos" onScopeChange={vi.fn()} tested={false} onTest={onTest} onSave={onSave} onReloadAudit={vi.fn()} />)
  await user.click(screen.getByRole('button', {name:'恢复全局继承'}))
  expect(onSave).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', {name:'确认恢复继承'}))
  expect(onSave).toHaveBeenCalledWith({inherited:true})
  expect(onTest).not.toHaveBeenCalled()
})
