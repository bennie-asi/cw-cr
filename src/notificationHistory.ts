import type { StatusTone } from './types'

export function notificationDeliveryStatus(action: string, metadata: Record<string, unknown>): { result: string; response: string; tone: StatusTone } {
  const nested = metadata.notification
  const delivery = action === '自动合并结果' && typeof nested === 'object' && nested !== null ? nested as Record<string, unknown> : metadata
  if (action.includes('不确定') || delivery.delivery_state === 'unknown' || delivery.status === 'unknown' || (action.includes('通知失败') && typeof delivery.error === 'string' && delivery.ok !== false)) {
    return { result: '结果不确定', response: '可能已送达，请先核对群消息', tone: 'warning' }
  }
  if (delivery.configured === false) return { result: '未发送', response: '未配置机器人', tone: 'neutral' }
  if (delivery.skipped === true || delivery.status === 'skipped') return { result: '已跳过', response: '本次未发送', tone: 'neutral' }
  if (delivery.ok === false || delivery.status === 'failed' || action.includes('失败')) return { result: '失败', response: '发送未成功', tone: 'danger' }
  if (delivery.ok === true) return { result: '已送达', response: '企业微信已接收', tone: 'success' }
  return { result: '已记录', response: '缺少投递确认', tone: 'neutral' }
}
