/* 管理端：左侧一级导航（6 模块）+ 模块级权限过滤
   知识库总览 / 知识库管理 / 检索调优 / 内容审核 / 用户与权限 / 系统设置 */

import { useEffect, useState } from 'react'
import type { AuthUser } from '../api/auth'
import DashboardSection from '../components/admin/DashboardSection'
import KbSection from '../components/admin/KbSection'
import RagSection from '../components/admin/RagSection'
import ReviewSection from '../components/admin/ReviewSection'
import SystemSection from '../components/admin/SystemSection'
import UsersSection from '../components/admin/UsersSection'

interface Props {
  token: string
  user: AuthUser
  onBack: () => void
  onLogout: () => void
}

const NAV = [
  { key: 'dashboard', label: '📊 知识库总览' },
  { key: 'kb', label: '📚 知识库管理' },
  { key: 'rag', label: '🧪 检索调优' },
  { key: 'review', label: '⚖️ 内容审核' },
  { key: 'users', label: '👥 用户与权限' },
  { key: 'system', label: '⚙️ 系统设置' },
]

// 用户与权限/审计为严格管理员端点（后端未设模块门禁），所有管理员可见；其余按 permissions 过滤
function parsePerms(user: AuthUser): string[] | null {
  const raw = user.permissions
  if (raw === null || raw === undefined || raw === '' || raw === 'null') return null // NULL=全部
  try {
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}

export default function AdminPage({ token, user, onBack, onLogout }: Props) {
  const perms = parsePerms(user)
  const [tab, setTab] = useState('dashboard')
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [badcase, setBadcase] = useState('')

  const visible = NAV.filter((n) => n.key === 'users' || perms === null || perms.includes(n.key))

  useEffect(() => {
    if (!visible.some((n) => n.key === tab)) {
      setTab(visible[0]?.key ?? 'users')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(visible.map((n) => n.key))])

  useEffect(() => {
    fetch('/eval/bad_cases.md')
      .then((r) => (r.ok ? r.text() : ''))
      .then(setBadcase)
      .catch(() => setBadcase(''))
  }, [])

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 3500)
  }

  return (
    <div className="admin">
      <div className="admin-topbar">
        <button className="icon-btn" onClick={onBack}>← 返回用户端</button>
        <span className="admin-title">🛡️ 管理端（{user.nickname || user.username}）</span>
        <button className="icon-btn" onClick={onLogout}>退出登录</button>
      </div>
      {error && <p style={{ color: 'var(--danger)', marginBottom: 12 }}>{error}</p>}
      <div className="admin-layout">
        <nav className="admin-nav">
          {visible.map((n) => (
            <button
              key={n.key}
              className={`admin-nav-item${tab === n.key ? ' active' : ''}`}
              onClick={() => setTab(n.key)}
            >
              {n.label}
            </button>
          ))}
        </nav>
        <main className="admin-main">
          {tab === 'dashboard' && <DashboardSection token={token} onError={setError} />}
          {tab === 'kb' && <KbSection token={token} onError={setError} onToast={showToast} />}
          {tab === 'rag' && <RagSection token={token} onError={setError} onToast={showToast} />}
          {tab === 'review' && (
            <>
              <ReviewSection token={token} onError={setError} onToast={showToast} />
              <div className="rag-block" style={{ marginTop: 18 }}>
                <h4>📋 Bad Case 复盘（data/eval/bad_cases.md）</h4>
                {badcase ? (
                  <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 320, overflow: 'auto' }}>
                    {badcase}
                  </pre>
                ) : (
                  <p className="admin-hint">未找到 bad_cases.md（构建时复制进 web/public/eval/）</p>
                )}
              </div>
            </>
          )}
          {tab === 'users' && <UsersSection token={token} onError={setError} onToast={showToast} />}
          {tab === 'system' && <SystemSection token={token} onError={setError} />}
        </main>
      </div>
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
