/* 管理端模块：用户与权限管理（列表/角色/启停/模块权限）+ 审计日志 */

import { useCallback, useEffect, useState } from 'react'
import {
  listAuditLogs,
  listUsers,
  patchUser,
  type AuditLog,
  type UserRecord,
} from '../../api/client'

interface Props {
  token: string
  onError: (msg: string) => void
  onToast: (msg: string) => void
}

const PERM_MODULES = [
  { key: 'dashboard', label: '知识库总览' },
  { key: 'kb', label: '知识库管理' },
  { key: 'rag', label: '检索调优' },
  { key: 'review', label: '内容审核' },
  { key: 'system', label: '系统设置' },
]

export default function UsersSection({ token, onError, onToast }: Props) {
  const [users, setUsers] = useState<UserRecord[]>([])
  const [total, setTotal] = useState(0)
  const [permDraft, setPermDraft] = useState<Record<number, string[]>>({})
  const [audit, setAudit] = useState<AuditLog[]>([])

  const load = useCallback(async () => {
    try {
      const [u, a] = await Promise.all([listUsers(token), listAuditLogs(token, 30)])
      setUsers(u.users)
      setTotal(u.total)
      setAudit(a.logs)
    } catch (e) {
      onError(e instanceof Error ? e.message : '加载失败')
    }
  }, [token, onError])

  useEffect(() => {
    void load()
  }, [load])

  const permissionsOf = (u: UserRecord): string[] => {
    if (!u.permissions) return [] // '' 或 NULL：NULL=全部；'' 视为空数组
    try {
      const arr = JSON.parse(u.permissions as string)
      return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : []
    } catch {
      return []
    }
  }

  const toggleRole = async (u: UserRecord) => {
    try {
      await patchUser(token, u.id, { role: u.role === 'admin' ? 'user' : 'admin' })
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : '角色修改失败')
    }
  }

  const toggleActive = async (u: UserRecord) => {
    try {
      await patchUser(token, u.id, { is_active: u.is_active !== 1 })
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : '状态修改失败')
    }
  }

  const togglePerm = (u: UserRecord, mod: string) => {
    setPermDraft((prev) => {
      const cur = prev[u.id] ?? permissionsOf(u)
      const next = cur.includes(mod) ? cur.filter((m) => m !== mod) : [...cur, mod]
      return { ...prev, [u.id]: next }
    })
  }

  const savePerms = async (u: UserRecord) => {
    const perms = permDraft[u.id] ?? permissionsOf(u)
    try {
      await patchUser(token, u.id, { permissions: perms })
      onToast(`「${u.username}」权限已更新（${perms.length} 个模块）`)
      setPermDraft((p) => {
        const { [u.id]: _drop, ...rest } = p
        return rest
      })
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : '权限保存失败')
    }
  }

  const exportCsv = async () => {
    try {
      const resp = await fetch('/audit/logs/export', { headers: { Authorization: `Bearer ${token}` } })
      if (!resp.ok) throw new Error('导出失败')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'audit_logs.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      onError(e instanceof Error ? e.message : '审计导出失败')
    }
  }

  return (
    <div>
      <p className="admin-hint">
        严格管理员 RBAC（旧 X-Admin-Key 不可管理用户）；permissions 为 NULL（未设置）时拥有全部模块权限。
      </p>
      <table className="stats-table">
        <thead>
          <tr>
            <th>id</th>
            <th>用户名</th>
            <th>昵称</th>
            <th>角色</th>
            <th>状态</th>
            <th>最近活跃</th>
            <th>模块权限</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const perms = permDraft[u.id] ?? permissionsOf(u)
            const nullPerms = !u.permissions // NULL = 全部
            return (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td>{u.username}</td>
                <td>{u.nickname || '—'}</td>
                <td>{u.role === 'admin' ? '🛡️ 管理员' : '👤 用户'}</td>
                <td>{u.is_active === 1 ? '正常' : '已禁用'}</td>
                <td>{u.last_active_at || '—'}</td>
                <td style={{ minWidth: 220 }}>
                  {nullPerms && permDraft[u.id] === undefined ? (
                    <span className="admin-hint">全部（未限制）</span>
                  ) : (
                    <div className="admin-row perm-row">
                      {PERM_MODULES.map((m) => (
                        <label key={m.key} className={`perm-chip${perms.includes(m.key) ? ' on' : ''}`}>
                          <input
                            type="checkbox"
                            checked={perms.includes(m.key)}
                            onChange={() => togglePerm(u, m.key)}
                          />
                          {m.label}
                        </label>
                      ))}
                      <button className="icon-btn" onClick={() => void savePerms(u)}>保存</button>
                    </div>
                  )}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="icon-btn" onClick={() => void toggleRole(u)}>
                    {u.role === 'admin' ? '降级' : '升级'}
                  </button>
                  <button className="icon-btn" onClick={() => void toggleActive(u)}>
                    {u.is_active === 1 ? '禁用' : '启用'}
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="admin-hint">共 {total} 个账户</p>

      <h3 style={{ margin: '18px 0 8px' }}>📋 操作审计日志</h3>
      <div className="admin-row">
        <button onClick={() => void exportCsv()}>导出 CSV</button>
        <span className="admin-hint">最近 30 条（/audit/logs 可加分页参数）</span>
      </div>
      <table className="stats-table">
        <thead>
          <tr>
            <th>#</th>
            <th>时间</th>
            <th>IP</th>
            <th>问题</th>
            <th>回答</th>
            <th>耗时ms</th>
          </tr>
        </thead>
        <tbody>
          {audit.length === 0 && (
            <tr>
              <td colSpan={6} className="admin-hint">暂无审计记录</td>
            </tr>
          )}
          {audit.map((l) => (
            <tr key={l.id}>
              <td>{l.id}</td>
              <td>{l.created_at || '—'}</td>
              <td>{l.client_ip || '—'}</td>
              <td className="rag-preview">{l.question}</td>
              <td className="rag-preview">{(l.answer || '').slice(0, 60)}</td>
              <td>{l.latency_ms ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
