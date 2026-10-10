import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { SessionBoundary } from './SessionBoundary'
import './styles.css'
import './v4.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SessionBoundary>{session => <App {...session} />}</SessionBoundary>
  </StrictMode>,
)
