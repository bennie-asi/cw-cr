import { render } from '@testing-library/react'
import { expect, it } from 'vitest'
import { UnifiedDiff } from './App'

it('renders old and new file coordinates and never numbers the terminal separator', () => {
  const { container } = render(<UnifiedDiff text={'diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -10,2 +20,2 @@\n-old\n+new\n context\n\\ No newline at end of file\n'} comments={[]} />)
  const rows = [...container.querySelectorAll('.code-line')]
  expect(rows).toHaveLength(3)
  expect(rows.map(row => [...row.querySelectorAll('.line-no')].map(cell => cell.textContent))).toEqual([['10', ''], ['', '20'], ['11', '21']])
})

it('does not mistake source lines beginning with diff header characters for headers', () => {
  const { container } = render(<UnifiedDiff text={'diff --git a/a b/a\n@@ -1 +1 @@\n--- old\n+++ new'} comments={[]} />)
  expect([...container.querySelectorAll('code')].map(row => row.textContent)).toEqual(['-- old', '++ new'])
})

it('anchors deleted-side comments only to the deleted row', () => {
  const { container } = render(<UnifiedDiff text={'@@ -10 +10 @@\n-old\n+new'} comments={[{id:'old-comment', file:'a', line:10, side:'old', title:'x', body:'x', level:'P1', status:'已发布'}]} />)
  expect(container.querySelectorAll('.code-with-comment')).toHaveLength(1)
  expect(container.querySelector('.code-with-comment')).toHaveClass('code-removed')
})

it('highlights the selected issue when several comments share the same source line', () => {
  const comments = ['first', 'selected'].map(id => ({ id, file: 'a', line: 159, title: id, body: id, level: 'P1' as const, status: '已发布' as const }))
  const { container } = render(<UnifiedDiff text={'@@ -157 +157,3 @@\n-old\n+wrong\n+other\n+csrf'} comments={comments} activeCommentId="selected" />)
  expect(container.querySelector('.code-comment-active')).toHaveAttribute('data-comment-id', 'selected')
  expect(container.querySelector('.code-comment-active code')).toHaveTextContent('csrf')
})

it('never highlights a row for an unverified historical anchor', () => {
  const { container } = render(<UnifiedDiff text={'@@ -157 +157 @@\n-old\n+wrong'} comments={[{id:'c', file:'a', line:157, title:'x', body:'x', level:'P1', status:'已发布', location:{status:'unverified', reason:'缺少原代码'}}]} activeCommentId="c" />)
  expect(container.querySelector('.code-comment-active')).toBeNull()
})
