import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'
import type { ProjectConnectionCheck } from './api'
import { ProjectDrawer } from './ProjectOnboarding'
import { PROJECTS } from './test-fixtures'

afterEach(() => vi.restoreAllMocks())
const check: ProjectConnectionCheck = { webhook_url: 'https://workspace.example/api/webhooks/gitlab/', repositories: [{ path: 'team/api', connection: { state: 'ready', detail: '可读取仓库' }, webhook: { state: 'missing', detail: '尚未配置 Webhook' } }] }
async function fill(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('项目名称'), 'Team')
  await user.type(screen.getByLabelText('GitLab 项目路径 1'), 'team/api')
  await user.click(screen.getByRole('button', { name: '下一步：连接检查' }))
}

it('checks the draft without saving, then confirms creation and provides a configuration entry', async () => {
  const request = vi.spyOn(api, 'checkProjectConnection').mockResolvedValue(check)
  const save = vi.fn().mockResolvedValue({ ...PROJECTS[0], id: 'new', name: 'Team' })
  const next = vi.fn(); const user = userEvent.setup()
  render(<ProjectDrawer initial={null} saving={false} onClose={vi.fn()} onSave={save} onContinue={next} />)
  await fill(user)
  expect(await screen.findByText('尚未配置 Webhook')).toBeInTheDocument()
  expect(request).toHaveBeenCalledWith([{ path: 'team/api' }])
  expect(save).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '下一步：确认创建' }))
  await user.click(screen.getByRole('button', { name: '创建项目' }))
  expect(await screen.findByRole('heading', { name: '项目已创建' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '继续配置模型' }))
  expect(next).toHaveBeenCalledWith('new', 'models')
})

it('allows failed connection checks to create a pending project and preserves inline save errors', async () => {
  vi.spyOn(api, 'checkProjectConnection').mockRejectedValue(new Error('无法连接'))
  const save = vi.fn().mockRejectedValue(new Error('仓库已接入其他项目'))
  const user = userEvent.setup()
  render(<ProjectDrawer initial={null} saving={false} onClose={vi.fn()} onSave={save} />)
  await fill(user)
  await screen.findByText('无法连接')
  await user.click(screen.getByRole('button', { name: '下一步：确认创建' }))
  await user.click(screen.getByRole('button', { name: '创建项目' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('仓库已接入其他项目')
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

it('invalidates checks after editing a repository and ignores stale check responses', async () => {
  let finish!: (value: ProjectConnectionCheck) => void
  vi.spyOn(api, 'checkProjectConnection').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const user = userEvent.setup()
  render(<ProjectDrawer initial={null} saving={false} onClose={vi.fn()} onSave={vi.fn()} />)
  await fill(user)
  await user.click(screen.getByRole('button', { name: '上一步' }))
  const input = screen.getByLabelText('GitLab 项目路径 1')
  await user.clear(input); await user.type(input, 'team/ui')
  await act(async () => { finish(check) })
  expect(within(screen.getByRole('dialog')).queryByText('可读取仓库')).not.toBeInTheDocument()
  expect(input).toHaveValue('team/ui')
})
