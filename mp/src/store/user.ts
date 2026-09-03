/** 登录态 store（zustand）：token + user + 会话状态。 */

import { create } from 'zustand'
import {
  loadSession,
  saveSession,
  wechatLogin,
  logout as doLogout,
  type AuthUser,
} from '../api/auth'

interface UserState {
  token: string
  user: AuthUser | null
  loggingIn: boolean
  restore: () => void
  wxLogin: (nickname?: string) => Promise<boolean>
  setUser: (user: AuthUser) => void
  logout: () => void
}

export const useUserStore = create<UserState>((set, get) => ({
  token: '',
  user: null,
  loggingIn: false,

  restore: () => {
    const s = loadSession()
    if (s) set({ token: s.token, user: s.user })
  },

  wxLogin: async (nickname?: string) => {
    if (get().loggingIn) return false
    set({ loggingIn: true })
    try {
      const s = await wechatLogin(nickname)
      saveSession(s)
      set({ token: s.token, user: s.user })
      return true
    } catch (e) {
      const msg = e instanceof Error ? e.message : '登录失败'
      // eslint-disable-next-line no-console
      console.warn('微信登录失败:', msg)
      return false
    } finally {
      set({ loggingIn: false })
    }
  },

  setUser: (user) => {
    const s = loadSession()
    if (s) saveSession({ ...s, user })
    set({ user })
  },

  logout: () => {
    doLogout()
    set({ token: '', user: null })
  },
}))
