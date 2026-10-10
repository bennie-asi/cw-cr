import { createRoot } from 'react-dom/client'
import type { SessionUser } from './api'
import { createPreviewFetch } from './previewTransport'
import './styles.css'
import './v4.css'
import './implementation-preview.css'

// This preview deliberately uses the existing test fixtures and never calls the API.
const previewUser: SessionUser = { id: 0, username: 'v4-preview', display_name: '设计预览', platform_role: 'admin' }
async function start() {
  const previewPage = new URL(window.location.href).searchParams.get('page');
  (globalThis as typeof globalThis & { process?: { env?: Record<string, string> } }).process = { env: { MODE: 'test' } }
  window.fetch = createPreviewFetch(window.fetch.bind(window), window.location.origin)
  document.body.classList.add('implementation-preview')
  const paths: Record<string, string> = {
    projects: '/platform/projects',
    review: '/platform/reviews',
    models: '/platform/settings/models',
    notifications: '/platform/settings/notifications',
    system: '/platform/settings/system',
    members: '/projects/demo-api/settings/members',
  }
  window.history.replaceState({}, '', paths[previewPage || ''] || '/')
  const { default: App } = await import('./App')
  createRoot(document.getElementById('root')!).render(<App user={previewUser} onLogout={async () => undefined} />)
}
void start()
