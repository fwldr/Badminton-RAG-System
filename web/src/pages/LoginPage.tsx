/* 登录/注册页：统一入口（用户与管理员共用；登录后按 role 路由到对应端） */

import { useState } from 'react'
import { login, register } from '../api/client'
import type { AuthSession } from '../api/auth'

interface Props {
  onAuthed: (session: AuthSession) => void
}

export default function LoginPage({ onAuthed }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [nickname, setNickname] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    const u = username.trim()
    if (!u || !password) {
      setError('请输入用户名和密码')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res =
        mode === 'login'
          ? await login(u, password)
          : await register(u, password, nickname.trim() || undefined)
      onAuthed({ token: res.token, user: res.user })
    } catch (e) {
      setError(e instanceof Error ? e.message : '请求失败')
    } finally {
      setLoading(false)
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') void submit()
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-logo">🏸</div>
        <h1>羽问</h1>
        <p className="auth-sub">羽毛球知识库问答 · 双角色系统（用户 / 管理员）</p>

        <div className="auth-tabs">
          <button className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>
            登录
          </button>
          <button className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>
            注册
          </button>
        </div>

        <input
          placeholder="用户名（2-32 位字母/数字/下划线/中文）"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <input
          type="password"
          placeholder="密码（至少 6 位）"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {mode === 'register' && (
          <input
            placeholder="昵称（可选）"
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            onKeyDown={onKeyDown}
          />
        )}

        {error && <p className="auth-error">{error}</p>}

        <button className="auth-submit" disabled={loading} onClick={() => void submit()}>
          {loading ? '处理中…' : mode === 'login' ? '登录' : '注册并登录'}
        </button>
        <p className="auth-hint">
          管理员账户由服务端配置（.env 的 BOOTSTRAP_ADMIN_* 种子账号或后台分配）
        </p>
      </div>
    </div>
  )
}
