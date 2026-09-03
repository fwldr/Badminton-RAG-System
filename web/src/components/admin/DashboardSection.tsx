/* 管理端模块：知识库总览（Dashboard）——指标卡 + 健康探活 + 成本报表 */

import { useCallback, useEffect, useState } from 'react'
import {
  adminDashboard,
  adminHealth,
  chatStats,
  type DashboardData,
  type HealthData,
  type StatsRow,
} from '../../api/client'

interface Props {
  token: string
  onError: (msg: string) => void
}

export default function DashboardSection({ token, onError }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [health, setHealth] = useState<HealthData | null>(null)
  const [rows, setRows] = useState<StatsRow[]>([])

  const load = useCallback(async () => {
    try {
      const [d, h, s] = await Promise.all([
        adminDashboard(token),
        adminHealth(token),
        chatStats(token).catch(() => ({ rows: [] })),
      ])
      setData(d)
      setHealth(h)
      setRows(s.rows)
    } catch (e) {
      onError(e instanceof Error ? e.message : '总览加载失败')
    }
  }, [token, onError])

  useEffect(() => {
    void load()
  }, [load])

  if (!data) return <p className="admin-hint">加载中…</p>

  const cards = [
    { label: '知识文档', value: data.documents.total, sub: `失败 ${data.documents.failed}` },
    { label: '向量总量', value: data.vectors.total_chunks, sub: `${data.vectors.collections} 个集合` },
    { label: '今日消息', value: data.activity.messages_today, sub: `累计 ${data.activity.messages}` },
    { label: '注册用户', value: data.activity.users, sub: `会话 ${data.activity.conversations}` },
    { label: '待审纠错', value: data.todo.pending_corrections, sub: `失败文档 ${data.todo.failed_documents}` },
    { label: '点踩反馈', value: data.feedback.dislikes, sub: `共 ${data.feedback.total} 条` },
  ]
  const maxTokens = Math.max(1, ...rows.map((r) => r.total_tokens))

  return (
    <div>
      <div className="admin-cards">
        {cards.map((c) => (
          <div key={c.label} className="admin-card">
            <div className="admin-card-value">{c.value}</div>
            <div className="admin-card-label">{c.label}</div>
            <div className="admin-card-sub">{c.sub}</div>
          </div>
        ))}
      </div>

      <h3 style={{ margin: '18px 0 8px' }}>🩺 系统健康</h3>
      <div className="admin-hint">
        {health?.items.map((it) => (
          <span key={it.name} className={`health-pill ${it.status === 'ok' ? 'ok' : 'err'}`}>
            {it.name}：{it.status === 'ok' ? '✓ 正常' : '✗ 异常'}
            {Object.keys(it.detail).length > 0
              ? `（${JSON.stringify(it.detail).slice(0, 60)}）`
              : ''}
          </span>
        ))}
        {health?.degraded ? (
          <p className="admin-warn">⚠️ 部分组件异常：{health.failed.join('、')}</p>
        ) : (
          <p className="admin-ok-text">全部组件正常</p>
        )}
      </div>

      <h3 style={{ margin: '18px 0 8px' }}>按路由的 Token 成本报表</h3>
      <table className="stats-table">
        <thead>
          <tr>
            <th>route</th>
            <th>调用</th>
            <th>prompt</th>
            <th>completion</th>
            <th>total</th>
            <th>占比</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={6} className="admin-hint">暂无调用（进程重启后重新累计）</td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={r.route}>
              <td>{r.route}</td>
              <td>{r.calls}</td>
              <td>{r.prompt_tokens.toLocaleString()}</td>
              <td>{r.completion_tokens.toLocaleString()}</td>
              <td>{r.total_tokens.toLocaleString()}</td>
              <td>
                <div className="bar">
                  <i style={{ width: `${(r.total_tokens / maxTokens) * 100}%` }} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ margin: '18px 0 8px' }}>📦 向量集合分布</h3>
      <table className="stats-table">
        <thead>
          <tr>
            <th>集合</th>
            <th>chunks</th>
          </tr>
        </thead>
        <tbody>
          {data.vectors.tables.map((t) => (
            <tr key={t.collection}>
              <td>{t.collection}</td>
              <td>{t.chunks}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
