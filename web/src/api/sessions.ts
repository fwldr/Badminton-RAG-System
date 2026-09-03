/* 会话管理：session 列表持久化到 localStorage（多轮记忆按 session 隔离） */

export interface SessionMeta {
  id: string
  title: string
  updatedAt: number
}

const KEY = 'br_sessions'

export function newSessionId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `s-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function loadSessions(): SessionMeta[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const list = JSON.parse(raw) as SessionMeta[]
    return Array.isArray(list) ? list : []
  } catch {
    return []
  }
}

export function saveSessions(list: SessionMeta[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    // localStorage 不可用（隐私模式等）时静默降级
  }
}

export function touchSession(list: SessionMeta[], id: string, question: string): SessionMeta[] {
  const title = question.length > 20 ? `${question.slice(0, 20)}…` : question
  const next = list.filter((s) => s.id !== id)
  next.unshift({ id, title, updatedAt: Date.now() })
  return next.slice(0, 50) // 最多保留 50 个会话
}

export function removeSession(list: SessionMeta[], id: string): SessionMeta[] {
  return list.filter((s) => s.id !== id)
}
