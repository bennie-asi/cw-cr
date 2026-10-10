import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { AlertCircle, Eye, EyeOff, LoaderCircle, Terminal } from 'lucide-react'
import './login.css'

type Props = {
  onLogin: (username: string, password: string) => Promise<void>
  needsSetup?: boolean
  expired?: boolean
}

/** Layout follows Stitch screen 3b649301a1c74af3b62b4d4c6e4d51cf. */
export function LoginPage({ onLogin, needsSetup = false, expired = false }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (inFlight.current || needsSetup) return
    inFlight.current = true
    setBusy(true)
    setError('')
    try {
      await onLogin(username.trim(), password)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败，请稍后重试')
      setShowPassword(false)
    } finally {
      setPassword('')
      setBusy(false)
      inFlight.current = false
    }
  }

  return <div className="login-page">
    <header className="login-header"><span className="login-brand-icon"><Terminal size={20} /></span><strong>智审台</strong></header>
    <main className="login-main">
      <section className="login-card" aria-labelledby="login-title">
        <h1 id="login-title">账号登录</h1>
        <p className="login-subtitle">使用平台分配的内置账号登录</p>
        {expired && <p role="status" className="login-notice">会话已过期，请重新登录</p>}
        {needsSetup && <div className="login-notice">尚未初始化账号，请在服务器的 backend 目录执行 <code>python manage.py createsuperuser</code> 设置首位管理员。平台不提供公开默认密码。</div>}
        <form onSubmit={submit} aria-busy={busy}>
          <label htmlFor="login-username">用户名</label>
          <input id="login-username" name="username" autoComplete="username" required maxLength={150} value={username} disabled={busy || needsSetup} onChange={event => setUsername(event.target.value)} placeholder="请输入用户名" />
          <label htmlFor="login-password">密码</label>
          <div className="login-password-wrap">
            <input id="login-password" name="password" autoComplete="current-password" required maxLength={4096} type={showPassword ? 'text' : 'password'} value={password} disabled={busy || needsSetup} onChange={event => setPassword(event.target.value)} placeholder="请输入密码" />
            <button className="login-visibility" type="button" aria-label={showPassword ? '隐藏密码' : '显示密码'} aria-pressed={showPassword} disabled={busy || needsSetup} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button>
          </div>
          {error && <p role="alert" className="login-error"><AlertCircle size={16} /><span>{error}</span></p>}
          <button className="login-submit" type="submit" disabled={busy || needsSetup}>{busy && <LoaderCircle size={17} className="login-spinner" />}{busy ? '正在登录…' : '登录'}</button>
        </form>
        <p className="login-help">忘记密码？请联系平台管理员</p>
      </section>
    </main>
  </div>
}
