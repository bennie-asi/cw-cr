import { expect, it, vi } from 'vitest'
import { createPreviewFetch } from './previewTransport'

it('keeps every API operation inside the preview and persists member changes in memory', async () => {
  const network = vi.fn()
  const fetch = createPreviewFetch(network, 'http://127.0.0.1:5173')
  const endpoint = 'http://127.0.0.1:8000/api/projects/demo/members/'
  const added = await fetch(endpoint, { method: 'POST', body: JSON.stringify({ username: 'zhouqi', role: 'member' }) })
  expect(added.status).toBe(201)
  expect((await (await fetch(endpoint)).json()).some((row: {username: string}) => row.username === 'zhouqi')).toBe(true)
  const unknown = await fetch('https://unrelated.example/api/send/', { method: 'POST' })
  expect(unknown.status).toBe(501)
  expect(network).not.toHaveBeenCalled()
})

it('provides latest-commit coverage and queues MR work without inventing a finished review', async () => {
  const fetch = createPreviewFetch(vi.fn(), 'http://127.0.0.1:5173')
  const coverage = await (await fetch('http://127.0.0.1:8000/api/overview/coverage/')).json()
  expect(coverage.percentage).toBe(75)
  const result = await (await fetch('http://127.0.0.1:8000/api/projects/demo/trigger/', { method: 'POST', body: JSON.stringify({ mr_iid: 128, mode: 'review' }) })).json()
  expect(result.job_status).toBe('queued')
  expect(result.review).toBeUndefined()
  const list = await (await fetch('http://127.0.0.1:8000/api/projects/demo/merge-requests/')).json()
  expect(list.results.find((row: {iid:number}) => row.iid === 128).review_state).toBe('queued')
})

it('blocks unknown same-origin API prefixes and allows only static reads', async () => {
  const network = vi.fn().mockResolvedValue(new Response('static'))
  const fetch = createPreviewFetch(network, 'http://127.0.0.1:5173')
  for (const method of ['GET', 'POST', 'PATCH', 'DELETE']) {
    expect((await fetch('/backend/projects/', { method })).status).toBe(501)
  }
  expect((await fetch('/src/example.ts', { method: 'POST' })).status).toBe(501)
  expect(network).not.toHaveBeenCalled()
  expect((await fetch('/src/example.ts')).status).toBe(200)
  expect(network).toHaveBeenCalledTimes(1)
})
