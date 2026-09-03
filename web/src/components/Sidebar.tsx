/* 侧边栏：品牌区 + 当前用户 + 会话列表 + 知识库统计（GET /kb/overview） */

import { useEffect, useState } from 'react'
import { kbOverview, type KbOverview } from '../api/client'
import type { AuthUser } from '../api/auth'
import type { SessionMeta } from '../api/sessions'

interface Props {
  sessions: SessionMeta[]
  activeId: string | null
  user: AuthUser
  onNew: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onAdmin: () => void
  onLogout: () => void
}

export default function Sidebar({
  sessions,
  activeId,
  user,
  onNew,
  onSelect,
  onDelete,
  onAdmin,
  onLogout,
}: Props) {
  const [kb, setKb] = useState<KbOverview | null>(null)

  useEffect(() => {
    kbOverview()
      .then(setKb)
      .catch(() => setKb(null)) // 后端未起时静默降级
  }, [])

  const isAdmin = user.role === 'admin'

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="logo">🏸</div>
        <div>
          <h1>羽问</h1>
          <p>羽毛球知识库问答</p>
        </div>
      </div>

      <div className="user-card">
        <span className="avatar-dot">{user.nickname?.[0] || user.username[0]}</span>
        <div className="user-info">
          <span className="user-name">{user.nickname || user.username}</span>
          <span className="user-role">{isAdmin ? '🛡️ 管理员' : '👤 用户'}</span>
        </div>
        <button className="icon-btn" title="退出登录" onClick={onLogout}>
          退出
        </button>
      </div>

      <button className="btn-new" onClick={onNew}>
        ＋ 新建会话
      </button>

      <div className="session-list">
        {sessions.length === 0 && (
          <div style={{ padding: '8px', fontSize: '12px', opacity: 0.6, textAlign: 'center' }}>
            暂无会话，开始提问吧
          </div>
        )}
        {sessions.map((s) => (
          <div
            key={s.id}
            className={`session-item${s.id === activeId ? ' active' : ''}`}
            onClick={() => onSelect(s.id)}
          >
            <span className="title">{s.title}</span>
            <button
              className="del"
              title="删除会话"
              onClick={(e) => {
                e.stopPropagation()
                onDelete(s.id)
              }}
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="kb-stats">
        <h3>知识库</h3>
        {kb ? (
          <>
            <div className="kb-stat-row">
              <span>数据表</span>
              <span className="num">{kb.tables.length} 张</span>
            </div>
            <div className="kb-stat-row">
              <span>知识条目</span>
              <span className="num">{kb.total_chunks} 条</span>
            </div>
            <div className="kb-stat-row">
              <span>知识文件</span>
              <span className="num">{kb.knowledge_files.length} 份</span>
            </div>
            <div className="kb-tables">
              {kb.tables.map((t) => (
                <div className="table-row" key={t.table}>
                  <span>{t.table}</span>
                  <span>{t.chunks}</span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div style={{ padding: '4px 8px', fontSize: '12px', opacity: 0.6 }}>
            统计不可用（后端未启动）
          </div>
        )}
      </div>

      {isAdmin && (
        <div className="nav-admin">
          <button onClick={onAdmin}>📊 管理端（控制台 / 用户与权限）</button>
        </div>
      )}
    </aside>
  )
}
