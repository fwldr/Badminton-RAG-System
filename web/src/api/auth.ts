/* 认证会话管理：token + 用户信息持久化到 localStorage（双角色 user / admin） */

export interface AuthUser {
  id: number
  username: string
  role: 'user' | 'admin'
  nickname?: string | null
  is_active?: number
  created_at?: string
  last_active_at?: string
  gender?: '男' | '女' | '保密' | null
  level?: '新手' | '进阶' | '专业' | null
  racket_model?: string | null
  avatar?: string | null
  pref_style?: 'simple' | 'detailed' | null
  pref_show_sources?: number | null
}

export interface AuthSession {
  token: string
  user: AuthUser
}

const TOKEN_KEY = 'br_token'
const USER_KEY = 'br_user'

export function loadAuth(): AuthSession | null {
  try {
    const token = localStorage.getItem(TOKEN_KEY)
    const raw = localStorage.getItem(USER_KEY)
    if (!token || !raw) return null
    return { token, user: JSON.parse(raw) as AuthUser }
  } catch {
    return null
  }
}

export function saveAuth(session: AuthSession): void {
  try {
    localStorage.setItem(TOKEN_KEY, session.token)
    localStorage.setItem(USER_KEY, JSON.stringify(session.user))
  } catch {
    // localStorage 不可用时静默降级（每次刷新需重新登录）
  }
}

export function clearAuth(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {
    // 忽略
  }
}
