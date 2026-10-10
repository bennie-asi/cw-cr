import { describe, expect, it } from 'vitest'
import { notificationFeedback } from './notificationFeedback'

describe('notification feedback', () => {
  it('does not call a skipped notification delivered', () => {
    expect(notificationFeedback({ ok: true, skipped: true, message: '通知已关闭' })).toEqual({ message: '通知已关闭', tone: 'info' })
  })
  it('does not claim a GitLab sync when only sending WeCom', () => {
    expect(notificationFeedback({ ok: true }).message).not.toContain('GitLab')
  })
  it('reports business errors as warnings', () => {
    expect(notificationFeedback({ ok: false }).tone).toBe('warning')
  })
})
