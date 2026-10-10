import { expect, it } from 'vitest'
import { notificationDeliveryStatus } from './notificationHistory'

it('does not label skipped or unconfigured notification as delivered', () => {
  expect(notificationDeliveryStatus('发送企业微信评审通知', { ok: true, skipped: true }).result).toBe('已跳过')
  expect(notificationDeliveryStatus('测试企业微信通知', { ok: true, configured: false }).result).toBe('未发送')
})
it('distinguishes accepted delivery, business refusal and transport uncertainty', () => {
  expect(notificationDeliveryStatus('测试企业微信通知', { ok: true, status_code: 200, response: { errcode: 0 } }).result).toBe('已送达')
  expect(notificationDeliveryStatus('测试企业微信通知', { ok: false, status_code: 200, response: { errcode: 40014 } }).result).toBe('失败')
  expect(notificationDeliveryStatus('企业微信投递结果不确定', {}).result).toBe('结果不确定')
  expect(notificationDeliveryStatus('企业微信通知失败', { error: 'read timeout' }).result).toBe('结果不确定')
  expect(notificationDeliveryStatus('发送企业微信评审通知', { skipped: true, delivery_state: 'unknown' }).result).toBe('结果不确定')
})
it('does not treat HTTP status alone as proof of delivery', () => {
  expect(notificationDeliveryStatus('测试企业微信通知', { status_code: 200 }).result).toBe('已记录')
})
it('interprets automatic merge notification results separately from merge outcomes', () => {
  expect(notificationDeliveryStatus('自动合并结果', { notification: { status: 'done', ok: true } }).result).toBe('已送达')
  expect(notificationDeliveryStatus('自动合并结果', { notification: { status: 'skipped', skipped: true } }).result).toBe('已跳过')
  expect(notificationDeliveryStatus('重发自动合并结果通知', { status: 'unknown', reason: 'read timeout' }).result).toBe('结果不确定')
  expect(notificationDeliveryStatus('自动合并结果', { notification: { status: 'failed', ok: false } }).result).toBe('失败')
})
