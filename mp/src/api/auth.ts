/** 认证：微信一键登录（code2session → openid 绑定/建号 → 同一 token 体系）。 */

import Taro from '@tarojs/taro'
import { request, setToken } from './request'

export interface AuthUser {
  id: number
  username: string
  role: 'user' | 'admin'
  nickname: string | null
  level?: string | null
  racket_model?: string | null
  avatar?: string | null
  pref_style?: string | null
  pref_show_sources?: number | null
  gender?: string | null
  wx_bound?: boolean
  phone_bound?: boolean
}

export interface Session {
  token: string
  user: AuthUser
  is_new?: boolean
}

const SESSION_KEY = 'mp_session'

export function loadSession(): Session | null {
  try {
    const raw = Taro.getStorageSync(SESSION_KEY)
    return raw ? (JSON.parse(raw) as Session) : null
  } catch {
    return null
  }
}

export function saveSession(s: Session) {
  setToken(s.token)
  try {
    Taro.setStorageSync(SESSION_KEY, JSON.stringify(s))
  } catch {
    /* 忽略 */
  }
}

export function clearSession() {
  try {
    Taro.removeStorageSync(SESSION_KEY)
  } catch {
    /* 忽略 */
  }
}

/** 微信一键登录：wx.login 取 code → POST /auth/wechat */
export async function wechatLogin(nickname?: string): Promise<Session> {
  const login = await Taro.login()
  if (!login.code) throw new Error('获取微信授权失败')
  const data = await request<Session>('/auth/wechat', {
    method: 'POST',
    auth: false,
    data: { code: login.code, nickname },
    timeout: 15000,
  })
  saveSession(data)
  return data
}

/** 更新资料与偏好（切换后全站即时生效） */
export async function updateProfile(fields: Partial<{
  nickname: string
  gender: '男' | '女' | '保密'
  level: '新手' | '进阶' | '专业'
  racket_model: string
  avatar: string
  pref_style: 'simple' | 'detailed'
  pref_show_sources: boolean
}>): Promise<AuthUser> {
  const user = await request<AuthUser>('/auth/profile', { method: 'PATCH', data: fields })
  const s = loadSession()
  if (s) saveSession({ ...s, user })
  return user
}

/** 手机号快速验证绑定：code 来自 Button open-type=getPhoneNumber */
export async function bindWechatPhone(code: string): Promise<AuthUser> {
  const data = await request<{ phone_bound: boolean; user: AuthUser }>('/auth/wechat/phone', {
    method: 'POST',
    data: { code },
  })
  const s = loadSession()
  if (s) saveSession({ ...s, user: data.user })
  return data.user
}

/** 解绑（type: wechat | phone） */
export async function unbind(type: 'wechat' | 'phone'): Promise<AuthUser> {
  const user = await request<AuthUser>('/auth/unbind', { method: 'POST', data: { type } })
  const s = loadSession()
  if (s) saveSession({ ...s, user })
  return user
}

export function logout() {
  clearSession()
  Taro.reLaunch({ url: '/pages/login/index' })
}
