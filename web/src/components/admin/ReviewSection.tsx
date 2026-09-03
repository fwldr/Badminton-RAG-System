/* 管理端模块：内容审核与反馈处理——纠错工单池（审核/通知闭环）+ 低质量回答聚合 */

import { useCallback, useEffect, useState } from 'react'
import {
  badQuestions,
  listCorrections,
  patchCorrection,
  type BadQuestion,
  type CorrectionItem,
} from '../../api/client'

interface Props {
  token: string
  onError: (msg: string) => void
  onToast: (msg: string) => void
}

const STATUSES: { key: string; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'pending', label: '待处理' },
  { key: 'accepted', label: '已采纳' },
  { key: 'rejected', label: '已驳回' },
  { key: 'discussion', label: '讨论中' },
]

export default function ReviewSection({ token, onError, onToast }: Props) {
  const [status, setStatus] = useState('')
  const [items, setItems] = useState<CorrectionItem[]>([])
  const [replyDraft, setReplyDraft] = useState<Record<number, string>>({})
  const [bad, setBad] = useState<BadQuestion[]>([])
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const [c, b] = await Promise.all([listCorrections(token, status || undefined), badQuestions(token)])
      setItems(c.items)
      setBad(b.items)
    } catch (e) {
      onError(e instanceof Error ? e.message : '加载失败')
    }
  }, [token, status, onError])

  useEffect(() => {
    void load()
  }, [load])

  const act = async (c: CorrectionItem, st: string) => {
    setBusyId(c.id)
    try {
      const reply = replyDraft[c.id] ?? ''
      await patchCorrection(token, c.id, { status: st, admin_reply: reply || null })
      onToast(`工单 #${c.id} 已${st === 'accepted' ? '采纳' : st === 'rejected' ? '驳回' : '转讨论'}`)
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : '审核失败')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      <div className="admin-tabs">
        {STATUSES.map((s) => (
          <button key={s.key} className={status === s.key ? 'active' : ''} onClick={() => setStatus(s.key)}>
            {s.label}
          </button>
        ))}
      </div>

      <table className="stats-table">
        <thead>
          <tr>
            <th>#</th>
            <th>用户</th>
            <th>引用</th>
            <th>原文 → 改后</th>
            <th>原因</th>
            <th>状态</th>
            <th>管理员回复</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr>
              <td colSpan={8} className="admin-hint">暂无纠错工单</td>
            </tr>
          )}
          {items.map((c) => (
            <tr key={c.id}>
              <td>{c.id}</td>
              <td>{c.nickname || c.username || c.user_id}</td>
              <td className="rag-preview">{c.doc_ref || '—'}</td>
              <td className="rag-preview">
                {c.original_text ? `${c.original_text.slice(0, 40)} → ` : ''}
                {c.corrected_text.slice(0, 40)}
              </td>
              <td className="rag-preview">{c.reason || '—'}</td>
              <td>
                {c.status === 'pending' ? '⏳ 待处理' : c.status === 'accepted' ? '✅ 已采纳' : c.status === 'rejected' ? '❌ 已驳回' : '💬 讨论中'}
              </td>
              <td>
                <input
                  className="tag-input"
                  placeholder="回复理由（可选）"
                  value={replyDraft[c.id] ?? ''}
                  onChange={(e) => setReplyDraft((p) => ({ ...p, [c.id]: e.target.value }))}
                />
              </td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <button className="icon-btn" disabled={busyId === c.id} onClick={() => void act(c, 'accepted')}>
                  采纳
                </button>
                <button className="icon-btn" disabled={busyId === c.id} onClick={() => void act(c, 'rejected')}>
                  驳回
                </button>
                <button className="icon-btn" disabled={busyId === c.id} onClick={() => void act(c, 'discussion')}>
                  转讨论
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ margin: '18px 0 8px' }}>🔻 低质量回答（同一问题多次点踩）</h3>
      <table className="stats-table">
        <thead>
          <tr>
            <th>问题</th>
            <th>点踩次数</th>
            <th>最近评论</th>
            <th>最近时间</th>
          </tr>
        </thead>
        <tbody>
          {bad.length === 0 && (
            <tr>
              <td colSpan={4} className="admin-hint">暂无点踩记录</td>
            </tr>
          )}
          {bad.map((b) => (
            <tr key={b.question}>
              <td>{b.question}</td>
              <td><b>{b.dislike_count}</b></td>
              <td className="rag-preview">{b.last_comment || '—'}</td>
              <td>{b.last_at || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
