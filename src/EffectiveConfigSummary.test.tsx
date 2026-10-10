import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { GitLabConnectionCard, ModelsPageConnected, NotificationsPageConnected } from './App'

it('keeps the effective model visible while its edit draft changes', async () => {
  render(<ModelsPageConnected
    initial={{ model: 'saved-model', base_url: 'https://gateway.example/v1', api_key_masked: '••••' }}
    modelOptions={[]} scope="global" projectName="交易服务" onScopeChange={vi.fn()}
    tested={false} onTest={vi.fn()} onSave={vi.fn()}
  />)
  const summary = screen.getByRole('region', { name: '当前生效模型配置' })
  expect(within(summary).getByText('saved-model')).toBeInTheDocument()
  await userEvent.clear(screen.getByRole('textbox', { name: '模型 ID' }))
  await userEvent.type(screen.getByRole('textbox', { name: '模型 ID' }), 'draft-model')
  expect(within(summary).getByText('saved-model')).toBeInTheDocument()
  expect(within(summary).queryByText('draft-model')).not.toBeInTheDocument()
})

it('shows the saved notification target without revealing its secret', async () => {
  render(<NotificationsPageConnected
    initial={{ name: '已保存群', webhook_configured: true, enabled: true }}
    scope="global" projectName="交易服务" onScopeChange={vi.fn()}
    tested={false} onTest={vi.fn()} onReloadAudit={vi.fn()} onSave={vi.fn()}
  />)
  const summary = screen.getByRole('region', { name: '当前生效通知配置' })
  expect(within(summary).getByText('已保存群')).toBeInTheDocument()
  expect(within(summary).getByText('已配置 · 原文不回显')).toBeInTheDocument()
  await userEvent.clear(screen.getByRole('textbox', { name: '群名称' }))
  await userEvent.type(screen.getByRole('textbox', { name: '群名称' }), '草稿群')
  expect(within(summary).getByText('已保存群')).toBeInTheDocument()
})

it('separates the effective GitLab connection from an edited URL', async () => {
  render(<GitLabConnectionCard
    config={{ base_url: 'https://gitlab.example', api_token_configured: true, api_token_masked: '••••', webhook_secret_configured: true, enabled: true }}
    tested={false} onTest={vi.fn()} onSave={vi.fn()}
  />)
  const summary = screen.getByRole('region', { name: '当前 GitLab 连接' })
  expect(within(summary).getByText('https://gitlab.example')).toBeInTheDocument()
  expect(within(summary).getAllByText('已配置 · 原文不回显')).toHaveLength(2)
  await userEvent.clear(screen.getByRole('textbox', { name: 'GitLab 地址' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'GitLab 地址' }), 'https://draft.example')
  expect(within(summary).getByText('https://gitlab.example')).toBeInTheDocument()
})
