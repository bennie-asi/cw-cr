import App from './App'
import type { SessionUser } from './api'

// Explicit test identity, never a production authentication fallback.
export const TEST_ADMIN: SessionUser = { id: 1, username: 'test-admin', display_name: '测试管理员', platform_role: 'admin' }
export default function TestApp() {
  return <App user={TEST_ADMIN} onLogout={async () => undefined} />
}
