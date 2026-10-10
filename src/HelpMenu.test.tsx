import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from './test-app'
import { HelpMenu } from './HelpMenu'

it('presents structured help and opens the configuration guide', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: '帮助' }))
  expect(screen.getByRole('heading', { name: '使用帮助' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /配置如何生效/ }))
  const dialog = screen.getByRole('dialog', { name: '配置如何生效' })
  expect(dialog).toHaveTextContent('项目自定义')
  expect(dialog).toHaveTextContent('/review')
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog', { name: '配置如何生效' })).not.toBeInTheDocument()
})

it('closes help on outside click and Escape', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: '帮助' }))
  await user.click(screen.getByRole('main'))
  expect(screen.queryByRole('heading', { name: '使用帮助' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '帮助' }))
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('heading', { name: '使用帮助' })).not.toBeInTheDocument()
})

it('hides platform connection controls from an ordinary member while keeping the rules guide', async () => {
  const user = userEvent.setup(), onConnections = vi.fn()
  render(<HelpMenu platformAdmin={false} onConnections={onConnections} />)
  await user.click(screen.getByRole('button', { name: '帮助' }))
  expect(screen.queryByRole('button', { name: /连接配置/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /评审与阻断规则/ }))
  expect(screen.getByRole('dialog', { name: '评审与阻断规则' })).toHaveTextContent('P2')
  expect(onConnections).not.toHaveBeenCalled()
})
