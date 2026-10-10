import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { GitLabConnectionCard } from './App'

it('preserves secret drafts when the actual save fails and prevents overlapping saves', async () => {
  let resolve!: (value: boolean) => void
  const save = vi.fn(() => new Promise<boolean>(r => { resolve = r }))
  const user = userEvent.setup()
  render(<GitLabConnectionCard config={{ base_url: 'https://gitlab.example' }} tested={false} onTest={vi.fn()} onSave={save} />)
  await user.type(screen.getByLabelText('GitLab API Token'), 'test-token')
  await user.type(screen.getByLabelText('Webhook Secret'), 'test-secret')
  await user.dblClick(screen.getByRole('button', { name: '保存 GitLab 配置' }))
  expect(save).toHaveBeenCalledOnce()
  expect(screen.getByLabelText('GitLab API Token')).toHaveValue('test-token')
  expect(screen.getByLabelText('GitLab API Token')).toBeDisabled()
  expect(screen.getByRole('button', { name: '测试 GitLab 连接' })).toBeDisabled()
  await act(async () => resolve(false))
  expect(screen.getByRole('alert')).toHaveTextContent('保存失败')
  expect(screen.getByLabelText('GitLab API Token')).toHaveValue('test-token')
  expect(screen.getByLabelText('Webhook Secret')).toHaveValue('test-secret')
  expect(screen.getByLabelText('GitLab 地址')).toHaveValue('https://gitlab.example')
})

it('clears credential inputs only after a confirmed successful save', async () => {
  const save = vi.fn().mockResolvedValue(true), user = userEvent.setup()
  render(<GitLabConnectionCard config={{ base_url: 'https://gitlab.example' }} tested={false} onTest={vi.fn()} onSave={save} />)
  await user.type(screen.getByLabelText('GitLab API Token'), 'test-token')
  await user.click(screen.getByRole('button', { name: '保存 GitLab 配置' }))
  expect(save).toHaveBeenCalledWith({ base_url: 'https://gitlab.example', enabled: true, api_token: 'test-token' })
  expect(screen.getByLabelText('GitLab API Token')).toHaveValue('')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('handles a rejected save without losing the draft or generating an unhandled rejection', async () => {
  const user = userEvent.setup()
  render(<GitLabConnectionCard config={null} tested={false} onTest={vi.fn()} onSave={vi.fn().mockRejectedValue(new Error('offline'))} />)
  await user.type(screen.getByLabelText('GitLab API Token'), 'test-token')
  await user.click(screen.getByRole('button', { name: '保存 GitLab 配置' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('保存失败')
  expect(screen.getByLabelText('GitLab API Token')).toHaveValue('test-token')
})
