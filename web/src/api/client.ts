/* 后端 API 客户端：统一 {code, message, data} 包装，出错抛 ApiError */

import type { AuthUser } from './auth'

export class ApiError extends Error {
  code: number
  status: number
  constructor(message: string, code: number, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

/** POST /chat 响应（与后端 ChatResponse 对齐） */
export interface ChatResponse {
  answer: string
  sources: { table: string; brand: string; model: string }[]
  images?: { url: string; title?: string }[]
  clarification: string | null
  trace: { node: string; input: Record<string, unknown>; output: Record<string, unknown> }[]
  trace_id: string
  cached: boolean
  langfuse_url: string | null
}

/** GET /kb/overview 响应 */
export interface KbOverview {
  tables: { table: string; chunks: number }[]
  spec_tables: string[]
  knowledge_files: string[]
  total_chunks: number
}

/** GET /chat/stats 报表行 */
export interface StatsRow {
  route: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
}

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, init)
  let body: ApiEnvelope<T> | null = null
  try {
    body = (await resp.json()) as ApiEnvelope<T>
  } catch {
    // 非 JSON 响应（如代理 502）
  }
  if (!resp.ok || !body || body.code !== 0) {
    const msg = body?.message || `请求失败（HTTP ${resp.status}）`
    throw new ApiError(msg, body?.code ?? -1, resp.status)
  }
  return body.data
}

function bearer(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// ---------- 认证（双角色：user / admin） ----------

export interface LoginResult {
  token: string
  user: AuthUser
}

export async function register(
  username: string,
  password: string,
  nickname?: string,
): Promise<LoginResult> {
  return request<LoginResult>('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, nickname }),
  })
}

export async function login(username: string, password: string): Promise<LoginResult> {
  return request<LoginResult>('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export async function fetchMe(token: string): Promise<AuthUser> {
  return request<AuthUser>('/auth/me', { headers: bearer(token) })
}

// ---------- 问答 ----------

export async function chat(
  sessionId: string,
  question: string,
  token?: string | null,
  scope?: string | null,
): Promise<ChatResponse> {
  const body: Record<string, unknown> = { session_id: sessionId, question }
  if (scope) body.scope = scope
  return request<ChatResponse>('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token ?? null) },
    body: JSON.stringify(body),
  })
}

export interface ProfilePatch {
  nickname?: string
  gender?: '男' | '女' | '保密'
  level?: '新手' | '进阶' | '专业'
  racket_model?: string
  avatar?: string
  pref_style?: 'simple' | 'detailed'
  pref_show_sources?: boolean
}

export async function updateProfile(token: string, patch: ProfilePatch): Promise<AuthUser> {
  return request<AuthUser>('/auth/profile', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(patch),
  })
}

export interface FeedbackPayload {
  session_id: string
  question: string
  answer?: string
  rating: 1 | -1
  comment?: string
  trace_id?: string
}

export async function sendFeedback(payload: FeedbackPayload, token?: string | null): Promise<{ id: number }> {
  return request<{ id: number }>('/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token ?? null) },
    body: JSON.stringify(payload),
  })
}

export async function kbOverview(): Promise<KbOverview> {
  return request<KbOverview>('/kb/overview')
}

// ---------- 管理端（管理员 JWT；旧 X-Admin-Key 由后端向后兼容） ----------

export async function chatStats(token: string): Promise<{ rows: StatsRow[] }> {
  return request<{ rows: StatsRow[] }>('/chat/stats', { headers: bearer(token) })
}

/** GET /admin/documents 文档记录 */
export interface DocRecord {
  id: number
  filename: string
  doc_type: string
  status: string
  chunk_count: number
  version: number
  error_msg: string | null
  tags?: string | null
  created_at?: string
}

export interface UploadResult {
  id: number
  filename: string
  status: string
  chunk_count: number
}

export async function uploadDocument(file: File, token: string): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  return request<UploadResult>('/admin/documents', {
    method: 'POST',
    headers: bearer(token),
    body: form,
  })
}

export async function listDocuments(token: string): Promise<{ documents: DocRecord[] }> {
  return request<{ documents: DocRecord[] }>('/admin/documents', { headers: bearer(token) })
}

export async function deleteDocument(docId: number, token: string): Promise<{ id: number }> {
  return request<{ id: number }>(`/admin/documents/${docId}`, {
    method: 'DELETE',
    headers: bearer(token),
  })
}

export async function reindexDocument(
  docId: number,
  token: string,
): Promise<{ id: number; version: number; chunk_count: number }> {
  return request<{ id: number; version: number; chunk_count: number }>(
    `/admin/documents/${docId}/reindex`,
    { method: 'POST', headers: bearer(token) },
  )
}

export async function patchDocTags(
  docId: number,
  tags: string[],
  token: string,
): Promise<{ id: number; tags: string[] }> {
  return request<{ id: number; tags: string[] }>(`/admin/documents/${docId}/tags`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify({ tags }),
  })
}

// ---------- 管理端：知识库总览（Dashboard） ----------

export interface DashboardData {
  documents: { total: number; by_type: Record<string, number>; failed: number }
  vectors: { total_chunks: number; collections: number; tables: { collection: string; chunks: number }[] }
  activity: { users: number; conversations: number; messages: number; messages_today: number }
  todo: { pending_corrections: number; failed_documents: number }
  feedback: { total: number; dislikes: number }
  routes: StatsRow[]
}

export interface HealthItem {
  name: string
  status: 'ok' | 'error'
  detail: Record<string, unknown>
}

export interface HealthData {
  items: HealthItem[]
  degraded: boolean
  failed: string[]
}

export async function adminDashboard(token: string): Promise<DashboardData> {
  return request<DashboardData>('/admin/dashboard', { headers: bearer(token) })
}

export async function adminHealth(token: string): Promise<HealthData> {
  return request<HealthData>('/admin/health', { headers: bearer(token) })
}

// ---------- 管理端：检索调优（RAG） ----------

export interface RagSettings {
  settings: Record<string, string>
  defaults: Record<string, string>
}

export interface RagDebugResult {
  question: string
  route: string
  expanded_queries: string[]
  candidates: {
    table: string
    id: string
    score: number | null
    text: string
    preview: string
    source: string
    metadata: Record<string, unknown>
  }[]
  conditions: Record<string, unknown>
  context_block: string
  answer: string | null
}

export interface PromptTemplate {
  id: number
  name: string
  description?: string | null
  system_prompt: string
  is_active: number
  created_at?: string
  updated_at?: string
}

export interface DictItem {
  id: number
  type: string
  word: string
  values: string[]
  values_json?: string | null
}

export async function getRagSettings(token: string): Promise<RagSettings> {
  return request<RagSettings>('/admin/rag/settings', { headers: bearer(token) })
}

export async function putRagSettings(
  token: string,
  patch: {
    vector_top_k?: number
    filter_top_k?: number
    rerank_enabled?: boolean
    blacklist_enabled?: boolean
  },
): Promise<{ updated: Record<string, string> }> {
  return request<{ updated: Record<string, string> }>('/admin/rag/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(patch),
  })
}

export async function ragDebug(
  token: string,
  question: string,
  opts: { top_k?: number; with_answer?: boolean } = {},
): Promise<RagDebugResult> {
  return request<RagDebugResult>('/admin/rag/debug', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify({ question, top_k: opts.top_k ?? 8, with_answer: opts.with_answer ?? true }),
  })
}

export async function listPrompts(token: string): Promise<{ templates: PromptTemplate[] }> {
  return request<{ templates: PromptTemplate[] }>('/admin/rag/prompts', { headers: bearer(token) })
}

export async function createPrompt(
  token: string,
  body: { name: string; system_prompt: string; description?: string | null },
): Promise<{ id: number }> {
  return request<{ id: number }>('/admin/rag/prompts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(body),
  })
}

export async function updatePrompt(
  token: string,
  id: number,
  body: { name?: string; system_prompt?: string; description?: string | null },
): Promise<{ id: number }> {
  return request<{ id: number }>(`/admin/rag/prompts/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(body),
  })
}

export async function deletePrompt(token: string, id: number): Promise<{ id: number }> {
  return request<{ id: number }>(`/admin/rag/prompts/${id}`, {
    method: 'DELETE',
    headers: bearer(token),
  })
}

export async function activatePrompt(
  token: string,
  id: number,
): Promise<{ id: number; active: boolean }> {
  return request<{ id: number; active: boolean }>(`/admin/rag/prompts/${id}/activate`, {
    method: 'POST',
    headers: bearer(token),
  })
}

/** 词典路由：同义词用复数 synonyms（后端路由），黑名单单数 */
const DICT_BASE: Record<'synonym' | 'blacklist', string> = {
  synonym: '/admin/rag/synonyms',
  blacklist: '/admin/rag/blacklist',
}

export async function listDict(
  token: string,
  type: 'synonym' | 'blacklist',
): Promise<{ items: DictItem[] }> {
  return request<{ items: DictItem[] }>(DICT_BASE[type], { headers: bearer(token) })
}

export async function addDict(
  token: string,
  type: 'synonym' | 'blacklist',
  word: string,
  values: string[],
): Promise<{ id: number }> {
  return request<{ id: number }>(DICT_BASE[type], {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify({ word, values }),
  })
}

export async function deleteDict(
  token: string,
  type: 'synonym' | 'blacklist',
  id: number,
): Promise<{ id: number }> {
  return request<{ id: number }>(`${DICT_BASE[type]}/${id}`, {
    method: 'DELETE',
    headers: bearer(token),
  })
}

// ---------- 管理端：内容审核与反馈 ----------

export interface CorrectionItem {
  id: number
  user_id: number
  doc_ref?: string | null
  original_text?: string | null
  corrected_text: string
  reason?: string | null
  status: 'pending' | 'accepted' | 'rejected' | 'discussion'
  admin_reply?: string | null
  created_at?: string
  username?: string | null
  nickname?: string | null
}

export interface BadQuestion {
  question: string
  dislike_count: number
  last_comment: string | null
  last_trace_id: string | null
  last_at: string | null
}

export async function listCorrections(
  token: string,
  status?: string,
): Promise<{ total: number; items: CorrectionItem[]; status: string | null }> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : ''
  return request<{ total: number; items: CorrectionItem[]; status: string | null }>(
    `/admin/corrections${qs}`,
    { headers: bearer(token) },
  )
}

export async function patchCorrection(
  token: string,
  id: number,
  body: { status: string; admin_reply?: string | null },
): Promise<{ correction: CorrectionItem; notified: boolean }> {
  return request<{ correction: CorrectionItem; notified: boolean }>(`/admin/corrections/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(body),
  })
}

export async function badQuestions(token: string): Promise<{ items: BadQuestion[] }> {
  return request<{ items: BadQuestion[] }>('/admin/qc/bad', { headers: bearer(token) })
}

// ---------- 管理端：系统设置（只读） ----------

export interface SystemConfig {
  models: Record<string, Record<string, unknown>>
  database: Record<string, string>
  vector_store: { dir: string }
  limits: Record<string, string | number>
  ingest: Record<string, string | number>
  auth: Record<string, string | number>
}

export async function systemConfig(token: string): Promise<SystemConfig> {
  return request<SystemConfig>('/admin/system', { headers: bearer(token) })
}

// ---------- 审计日志 ----------

export interface AuditLog {
  id: number
  client_ip?: string | null
  question: string
  answer?: string | null
  sources_json?: string | null
  latency_ms?: number | null
  created_at?: string
}

export async function listAuditLogs(
  token: string,
  limit = 50,
): Promise<{ total: number; logs: AuditLog[] }> {
  return request<{ total: number; logs: AuditLog[] }>(`/audit/logs?limit=${limit}`, {
    headers: bearer(token),
  })
}

// ---------- 用户与权限管理（严格管理员 RBAC） ----------

export interface UserRecord {
  id: number
  username: string
  role: 'user' | 'admin'
  nickname?: string | null
  permissions?: string | null
  is_active: number
  created_at?: string
  last_active_at?: string
}

export async function listUsers(token: string): Promise<{ total: number; users: UserRecord[] }> {
  return request<{ total: number; users: UserRecord[] }>('/admin/users', {
    headers: bearer(token),
  })
}

export async function patchUser(
  token: string,
  userId: number,
  patch: { role?: 'user' | 'admin'; is_active?: boolean; permissions?: string[] },
): Promise<UserRecord> {
  const body: Record<string, unknown> = {}
  if (patch.role !== undefined) body.role = patch.role
  if (patch.is_active !== undefined) body.is_active = patch.is_active
  if (patch.permissions !== undefined) body.permissions = patch.permissions
  return request<UserRecord>(`/admin/users/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(body),
  })
}
