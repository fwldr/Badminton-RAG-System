/* 应用外壳：登录态 + 角色路由（#/ 用户端、#/admin 管理端、#/login 登录） */

import { useEffect, useState } from 'react'
import { clearAuth, loadAuth, saveAuth, type AuthSession, type AuthUser } from './api/auth'
import AdminPage from './pages/AdminPage'
import LoginPage from './pages/LoginPage'
import UserShell from './pages/UserShell'

export default function App() {
  const [route, setRoute] = useState(() => window.location.hash || '#/')
  const [session, setSession] = useState<AuthSession | null>(() => loadAuth())

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash || '#/')
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const handleAuthed = (s: AuthSession) => {
    saveAuth(s)
    setSession(s)
    // 登录后按角色分流：管理员 → 管理端，用户 → 用户端
    window.location.hash = s.user.role === 'admin' ? '#/admin' : '#/'
  }

  const handleLogout = () => {
    clearAuth()
    setSession(null)
    window.location.hash = '#/login'
  }

  const handleUserChange = (user: AuthUser) => {
    setSession((prev) => {
      const next = prev ? { ...prev, user } : prev
      if (next) saveAuth(next)
      return next
    })
  }

  // 未登录（或令牌已丢失）→ 登录页
  if (!session) return <LoginPage onAuthed={handleAuthed} />

  // 管理端路由：仅管理员可进
  if (route.startsWith('#/admin')) {
    if (session.user.role !== 'admin') {
      return (
        <div className="auth-page">
          <div className="auth-card">
            <h2>⛔ 无权限</h2>
            <p className="auth-sub">管理端仅限管理员账户访问（当前角色：{session.user.role}）</p>
            <button className="auth-submit" onClick={() => (window.location.hash = '#/')}>
              返回用户端
            </button>
          </div>
        </div>
      )
    }
    return (
      <AdminPage
        token={session.token}
        user={session.user}
        onBack={() => (window.location.hash = '#/')}
        onLogout={handleLogout}
      />
    )
  }

  // 用户端（4 Tab：首页 / 发现 / 工作台 / 我的）
  return (
    <UserShell
      token={session.token}
      user={session.user}
      onUserChange={handleUserChange}
      onAdmin={() => (window.location.hash = '#/admin')}
      onLogout={handleLogout}
    />
  )
}
