import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import App from './test-app'

it('keeps the common policy and save action visible while advanced prompt stays collapsed', async () => {
  const user = userEvent.setup()
  window.history.replaceState({}, '', '/projects/demo-api/settings/policy')
  render(<App />)
  expect(screen.getByRole('button', { name: '保存项目策略' })).toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: '中文总评提示词' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '高级设置' }))
  expect(screen.getByRole('textbox', { name: '中文总评提示词' })).toBeInTheDocument()
})

it('gives every policy switch a distinct name and supports keyboard operation', async () => {
  const user = userEvent.setup()
  window.history.replaceState({}, '', '/projects/demo-api/settings/policy')
  render(<App />)
  for (const label of ['MR 创建 / push 自动触发', '支持 /review 命令', '支持 /improve 命令']) {
    expect(screen.getByRole('switch', { name: label })).toBeInTheDocument()
  }
  for (const title of ['安全性', '可靠性', '代码风格']) {
    await user.click(screen.getByRole('tab', { name: title }))
    expect(screen.getByRole('checkbox', { name: `启用${title}检查` })).toBeInTheDocument()
  }
  await user.click(screen.getByRole('tab', { name: '安全性' }))
  const security = screen.getByRole('checkbox', { name: '启用安全性检查' })
  security.focus()
  await user.keyboard(' ')
  expect(security).not.toBeChecked()
})
