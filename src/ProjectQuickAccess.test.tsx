import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { ProjectQuickAccess } from './ProjectQuickAccess'
import { PROJECTS } from './test-fixtures'

beforeEach(() => localStorage.clear())

it('lets a user pin a project and open it from quick access', async () => {
  const open = vi.fn()
  const user = userEvent.setup()
  render(<ProjectQuickAccess projects={PROJECTS} onOpen={open} />)
  await user.click(screen.getByText('管理常用项目'))
  await user.click(screen.getByRole('button', { name: `设为常用 ${PROJECTS[0].name}` }))
  expect(screen.getByRole('button', { name: `进入常用项目 ${PROJECTS[0].name}` })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: `进入常用项目 ${PROJECTS[0].name}` }))
  expect(open).toHaveBeenCalledWith(PROJECTS[0].id)
  expect(screen.getByText('最近访问')).toBeInTheDocument()
})

it('closes favorite management after clicking outside it', async () => {
  const user = userEvent.setup()
  render(<><ProjectQuickAccess projects={PROJECTS} onOpen={vi.fn()} /><button>页面内容</button></>)
  await user.click(screen.getByRole('button', { name: '管理常用项目' }))
  expect(screen.getByLabelText('收藏项目')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '页面内容' }))
  expect(screen.queryByLabelText('收藏项目')).not.toBeInTheDocument()
})
