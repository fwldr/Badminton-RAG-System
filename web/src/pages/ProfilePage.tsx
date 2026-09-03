/* 我的：个人资料 + 偏好设置 + 消息通知 + 我的纠错 + 退出登录 */

import { useEffect, useState } from 'react'
import type { AuthUser } from '../api/auth'
import { updateProfile } from '../api/client'
import {
  listCorrections,
  listNotifications,
  markNotificationsRead,
  submitCorrection,
  type Correction,
  type Notification,
} from '../api/user'

interface Props {
  token: string
  user: AuthUser
  onUserChange: (user: AuthUser) => void
  onAdmin: () => void
  onLogout: () => void
}

const LEVELS = ['新手', '进阶', '专业']
const GENDERS = ['男', '女', '保密']
const AVATARS = ['🏸', '🦾', '🎾', '⚡', '🦉', '🐯', '🔥', '⭐']

export default function ProfilePage({ token, user, onUserChange, onAdmin, onLogout }: Props) {
  const [nickname, setNickname] = useState(user.nickname || user.username)
  const [gender, setGender] = useState(user.gender || '保密')
  const [level, setLevel] = useState(user.level || '新手')
  const [racketModel, setRacketModel] = useState(user.racket_model || '')
  const [avatar, setAvatar] = useState(user.avatar || '🏸')
  const [prefStyle, setPrefStyle] = useState<'simple' | 'detailed'>(user.pref_style === 'simple' ? 'simple' : 'detailed')
  const [showSources, setShowSources] = useState((user.pref_show_sources ?? 1) === 1)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const [notifs, setNotifs] = useState<Notification[]>([])
  const [unread, setUnread] = useState(0)
  const [corrections, setCorrections] = useState<Correction[]>([])
  const [docRef, setDocRef] = useState('')
  const [origText, setOrigText] = useState('')
  const [corrText, setCorrText] = useState('')
  const [reason, setReason] = useState('')

  const load = async () => {
    try {
      const [n, c] = await Promise.all([listNotifications(token), listCorrections(token)])
      setNotifs(n.notifications)
      setUnread(n.unread)
      setCorrections(c.corrections)
    } catch {
      // 忽略
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const save = async () => {
    setSaving(true)
    setMsg(null)
    try {
      const updated = await updateProfile(token, {
        nickname: nickname.trim() || undefined,
        gender: gender as '男' | '女' | '保密',
        level: level as '新手' | '进阶' | '专业',
        racket_model: racketModel.trim() || undefined,
        avatar,
        pref_style: prefStyle,
        pref_show_sources: showSources,
      })
      onUserChange(updated)
      setMsg('✅ 资料已保存')
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const markAllRead = async () => {
    try {
      await markNotificationsRead(token)
      await load()
    } catch {
      // 忽略
    }
  }

  const submitCorr = async () => {
    if (!corrText.trim()) return
    setMsg(null)
    try {
      await submitCorrection(token, {
        doc_ref: docRef.trim() || undefined,
        original_text: origText.trim() || undefined,
        corrected_text: corrText.trim(),
        reason: reason.trim() || undefined,
      })
      setDocRef(''); setOrigText(''); setCorrText(''); setReason('')
      setMsg('✅ 纠错已提交，审核通过后会通知你')
      await load()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '提交失败')
    }
  }

  const isAdmin = user.role === 'admin'

  return (
    <div className="page profile-page">
      <header className="page-header">
        <span>👤 我的</span>
        <span className="muted">{isAdmin ? '🛡️ 管理员' : '👤 用户'}</span>
      </header>

      <div className="scroll-area">
        <h3 className="section-title">个人资料</h3>
        <div className="form-card">
          <div className="avatar-picker">
            {AVATARS.map((a) => (
              <button key={a} className={avatar === a ? 'active' : ''} onClick={() => setAvatar(a)}>
                {a}
              </button>
            ))}
          </div>
          <label>昵称<input value={nickname} onChange={(e) => setNickname(e.target.value)} maxLength={32} /></label>
          <div className="form-row">
            <label>性别
              <select value={gender} onChange={(e) => setGender(e.target.value as '男' | '女' | '保密')}>
                {GENDERS.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </label>
            <label>打球水平
              <select value={level} onChange={(e) => setLevel(e.target.value as '新手' | '进阶' | '专业')}>
                {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
            </label>
          </div>
          <label>常用球拍型号（个性化推荐用）
            <input value={racketModel} onChange={(e) => setRacketModel(e.target.value)} maxLength={64} placeholder="如：天斧99 Pro" />
          </label>
        </div>

        <h3 className="section-title">偏好设置</h3>
        <div className="form-card">
          <label>回答语气
            <select value={prefStyle} onChange={(e) => setPrefStyle(e.target.value as 'simple' | 'detailed')}>
              <option value="simple">简洁模式</option>
              <option value="detailed">详细模式</option>
            </select>
          </label>
          <label className="switch-row">
            <input type="checkbox" checked={showSources} onChange={(e) => setShowSources(e.target.checked)} />
            显示引用来源
          </label>
        </div>
        <button className="send-btn save-btn" disabled={saving} onClick={() => void save()}>
          {saving ? '保存中…' : '保存资料与偏好'}
        </button>

        <h3 className="section-title">🔔 消息通知{unread > 0 ? `（${unread} 未读）` : ''}</h3>
        <div className="card-list">
          {notifs.map((n) => (
            <div className={`notif-card${n.is_read ? ' read' : ''}`} key={n.id}>
              <div className="notif-title">{n.title}</div>
              {n.content && <div className="muted">{n.content}</div>}
              <div className="muted small">{n.created_at}</div>
            </div>
          ))}
          {notifs.length === 0 && <p className="muted">暂无通知</p>}
        </div>
        {unread > 0 && (
          <button className="chip" onClick={() => void markAllRead()}>全部已读</button>
        )}

        <h3 className="section-title">🛠️ 内容纠错</h3>
        <div className="form-card">
          <label>引用文档/片段<input value={docRef} onChange={(e) => setDocRef(e.target.value)} maxLength={200} placeholder="如：BWF发球规则" /></label>
          <label>原文（可选）<textarea value={origText} onChange={(e) => setOrigText(e.target.value)} rows={2} maxLength={2000} /></label>
          <label>修正内容<input value={corrText} onChange={(e) => setCorrText(e.target.value)} maxLength={2000} placeholder="如：击球点不得高于腰部" /></label>
          <label>原因（可选）<input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} placeholder="如：规则原文已修改" /></label>
          <button className="chip" disabled={!corrText.trim()} onClick={() => void submitCorr()}>提交纠错</button>
        </div>
        <div className="card-list">
          {corrections.map((c) => (
            <div className="notif-card" key={c.id}>
              <div className="notif-title">
                纠错 #{c.id} · {c.status === 'pending' ? '⏳ 待审核' : c.status === 'accepted' ? '✅ 已采纳' : '❌ 已驳回'}
              </div>
              <div className="muted">{c.corrected_text}{c.admin_reply ? `（管理员：${c.admin_reply}）` : ''}</div>
            </div>
          ))}
        </div>

        <div className="profile-actions">
          {isAdmin && (
            <button className="chip" onClick={onAdmin}>🛡️ 进入管理端</button>
          )}
          <button className="chip logout-btn" onClick={onLogout}>退出登录</button>
        </div>
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  )
}
