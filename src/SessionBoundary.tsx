import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, subscribeSessionExpired } from './api'
import type { SessionState, SessionUser } from './api'
import { LoginPage } from './LoginPage'

type Props = {
  children: (session: { user: SessionUser; onLogout: () => Promise<void> }) => ReactNode
}

export function SessionBoundary({ children }: Props) {
  const [session, setSession] = useState<SessionState | null>(null)
  const [error, setError] = useState('')
  const [expired, setExpired] = useState(false)
  const generation = useRef(0)
  const loggingOut = useRef(false)

  const refresh = useCallback(async () => {
    const current = ++generation.current
    setSession(null)
    setError('')
    try {
      const next = await api.session()
      if (current === generation.current) setSession(next)
    } catch (reason) {
      if (current === generation.current) setError(reason instanceof Error ? reason.message : '无法确认登录状态')
    }
  }, [])

  useEffect(() => {
    const unsubscribe = subscribeSessionExpired(() => {
      setExpired(true)
      void refresh()
    })
    void refresh()
    return () => { generation.current++; unsubscribe() }
  }, [refresh])

  async function login(username: string, password: string) {
    const current = ++generation.current
    const next = await api.login(username, password)
    if (current !== generation.current) return
    if (!next.user) throw new Error('登录未成功，请重试')
    setSession(next)
    setExpired(false)
  }

  async function logout() {
    if (loggingOut.current) return
    loggingOut.current = true
    const current = ++generation.current
    try {
      const next = await api.logout()
      if (current === generation.current) {
        setSession({ ...next, user: null })
        setExpired(false)
      }
    } finally { loggingOut.current = false }
  }

  if (error) return <main className="login-page"><section className="login-card">
    <p role="alert">{error}</p><button className="login-submit" onClick={() => { void refresh() }}>重试</button>
  </section></main>
  if (!session) return <main className="login-page"><p role="status">正在确认登录状态…</p></main>
  if (!session.user) return <LoginPage onLogin={login} needsSetup={session.needs_setup} expired={expired} />
  return <Fragment key={session.user.id}>{children({ user: session.user, onLogout: logout })}</Fragment>
}
