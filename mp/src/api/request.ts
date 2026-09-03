/**
 * 网络层：wx.request 封装（镜像 web/src/api/client.ts 的 {code, message, data} 契约）。
 * - Bearer 自动注入（来自 userStore 持久化）；
 * - body.code !== 0 抛出 ApiError（message 可展示）；
 * - HTTP 401 时清空登录态并跳转登录页。
 */

import Taro from '@tarojs/taro'

// TODO: 上线前改为备案后的 HTTPS 域名，并在微信后台配置 request 合法域名
// 后端地址自动选择：
// - 开发者工具模拟器 → http://127.0.0.1:8000（本机）
// - 真机（iOS/Android）→ 电脑局域网 IP（须与电脑同一网络）
// 如需强制指定（如备案域名），在 mp/.env.development 设置 TARO_APP_API_BASE 后重新编译。
const DEFAULT_LAN_BASE = 'http://192.168.78.1:8000' // 本机局域网 IP（以太网 2），变了改这里

function defaultBaseUrl(): string {
  const envBase = process.env.TARO_APP_API_BASE
  if (envBase) return envBase
  try {
    const platform = Taro.getSystemInfoSync().platform
    if (platform === 'ios' || platform === 'android') return DEFAULT_LAN_BASE
  } catch {
    /* 读取平台信息失败时按模拟器处理 */
  }
  return 'http://127.0.0.1:8000'
}

export const API_BASE_URL = defaultBaseUrl()

/** 资源路径补全：/uploads/xxx → 后端完整地址；http(s)/data:/wxfile 等原样返回 */
export function assetUrl(path?: string | null): string {
  if (!path) return ''
  if (path.startsWith('/')) return `${API_BASE_URL}${path}`
  return path
}

/** 是否为可加载图片地址（服务端相对路径 / http(s) / 本地临时文件） */
export function isImageSrc(s?: string | null): boolean {
  if (!s) return false
  return s.startsWith('/') || s.startsWith('http') || s.startsWith('wxfile') || s.startsWith('data:')
}

export class ApiError extends Error {
  code: number
  status: number
  constructor(message: string, code: number, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  data?: Record<string, unknown> | unknown[]
  auth?: boolean // 默认 true：带 Bearer
  timeout?: number
}

const TOKEN_KEY = 'mp_token'

export function getToken(): string {
  try {
    return Taro.getStorageSync(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setToken(token: string) {
  try {
    Taro.setStorageSync(TOKEN_KEY, token)
  } catch {
    /* 存储失败忽略 */
  }
}

export function clearToken() {
  try {
    Taro.removeStorageSync(TOKEN_KEY)
  } catch {
    /* 忽略 */
  }
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', data, auth = true, timeout = 20000 } = options
  const header: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (auth && token) header.Authorization = `Bearer ${token}`

  let resp: Taro.request.SuccessCallbackResult<ApiEnvelope<T>>
  try {
    resp = await Taro.request<ApiEnvelope<T>>({
      url: `${API_BASE_URL}${path}`,
      method,
      data,
      header,
      timeout,
    })
  } catch (e) {
    throw new ApiError('网络异常，请检查网络后重试', -1, 0)
  }

  const body = resp.data
  if (resp.statusCode === 401) {
    clearToken()
    Taro.showToast({ title: '登录已过期，请重新登录', icon: 'none' })
    Taro.reLaunch({ url: '/pages/login/index' })
    throw new ApiError(body?.message || '未登录', body?.code ?? -1, resp.statusCode)
  }
  if (resp.statusCode < 200 || resp.statusCode >= 300 || !body || body.code !== 0) {
    throw new ApiError(body?.message || `请求失败（HTTP ${resp.statusCode}）`, body?.code ?? -1, resp.statusCode)
  }
  return body.data
}

/** 分页/列表通用形状 */
export interface ListResp<T> {
  items: T[]
  total: number
}
