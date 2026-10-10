export function notificationFeedback(result: Record<string, unknown>): { message: string; tone: 'success' | 'info' | 'warning' } {
  if (result.skipped === true) return { message: typeof result.message === 'string' ? result.message : '本次通知已跳过，未发送消息', tone: 'info' }
  if (result.ok !== true) return { message: typeof result.error === 'string' ? result.error : '企业微信通知未送达，请检查配置和发送记录', tone: 'warning' }
  return { message: '企业微信已接收通知', tone: 'success' }
}
