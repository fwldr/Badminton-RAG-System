/* 用户端 API：会话/收藏/文件夹/动态（含回复）/热门/纠错/通知/上传（/user/*） */

import { request } from './client'

function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` }
}

// ---------- 会话（历史对话记录） ----------

export interface SourceRef {
  table: string
  brand: string
  model: string
}

export interface MessageData {
  id: number
  role: 'user' | 'assistant'
  content: string
  sources: SourceRef[]
  trace_id: string | null
  cached: number
  created_at: string
}

export interface Conversation {
  id: number
  session_id: string
  title: string
  tag: string | null
  is_favorite: number
  created_at: string
  updated_at: string
  msg_count: number
  last_answer: string | null
}

export interface ConversationDetail extends Conversation {
  messages: MessageData[]
}

export async function listConversations(
  token: string,
  params: { q?: string; tag?: string; favorite?: boolean; limit?: number; offset?: number } = {},
): Promise<{ total: number; conversations: Conversation[] }> {
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  if (params.tag) qs.set('tag', params.tag)
  if (params.favorite) qs.set('favorite', 'true')
  qs.set('limit', String(params.limit ?? 50))
  qs.set('offset', String(params.offset ?? 0))
  return request(`/user/conversations?${qs}`, { headers: bearer(token) })
}

export async function getConversation(token: string, id: number): Promise<ConversationDetail> {
  return request(`/user/conversations/${id}`, { headers: bearer(token) })
}

export async function patchConversation(
  token: string,
  id: number,
  patch: { title?: string; tag?: string; is_favorite?: boolean },
): Promise<Conversation> {
  return request(`/user/conversations/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(patch),
  })
}

export async function deleteConversation(token: string, id: number): Promise<{ id: number }> {
  return request(`/user/conversations/${id}`, { method: 'DELETE', headers: bearer(token) })
}

// ---------- 收藏夹与文件夹 ----------

export interface FavoriteFolder {
  id: number
  name: string
  fav_count: number
}

export interface Favorite {
  id: number
  folder_id: number | null
  question: string
  answer: string
  sources: SourceRef[]
  created_at: string
}

export async function listFolders(token: string): Promise<{ folders: FavoriteFolder[] }> {
  return request('/user/folders', { headers: bearer(token) })
}

export async function createFolder(token: string, name: string): Promise<{ id: number; name: string }> {
  return request('/user/folders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify({ name }),
  })
}

export async function deleteFolder(token: string, id: number): Promise<{ id: number }> {
  return request(`/user/folders/${id}`, { method: 'DELETE', headers: bearer(token) })
}

export async function listFavorites(
  token: string,
  params: { q?: string; folder_id?: number | null; limit?: number; offset?: number } = {},
): Promise<{ total: number; favorites: Favorite[] }> {
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  if (params.folder_id != null) qs.set('folder_id', String(params.folder_id))
  qs.set('limit', String(params.limit ?? 50))
  qs.set('offset', String(params.offset ?? 0))
  return request(`/user/favorites?${qs}`, { headers: bearer(token) })
}

export async function createFavorite(
  token: string,
  body: { question: string; answer: string; sources?: SourceRef[]; folder_id?: number | null },
): Promise<{ id: number }> {
  return request('/user/favorites', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(body),
  })
}

export async function patchFavorite(
  token: string,
  id: number,
  folder_id: number | null,
): Promise<{ id: number; folder_id: number | null }> {
  return request(`/user/favorites/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify({ folder_id }),
  })
}

export async function deleteFavorite(token: string, id: number): Promise<{ id: number }> {
  return request(`/user/favorites/${id}`, { method: 'DELETE', headers: bearer(token) })
}

// ---------- 动态 / 热门 ----------

export interface Post {
  id: number
  content: string
  images: string[]
  likes: number
  liked: boolean
  reply_count: number
  author_nickname: string
  author_avatar: string | null
  created_at: string
}

export async function listPosts(
  token: string,
  params: { limit?: number; offset?: number } = {},
): Promise<{ total: number; posts: Post[] }> {
  const qs = new URLSearchParams()
  qs.set('limit', String(params.limit ?? 50))
  qs.set('offset', String(params.offset ?? 0))
  return request(`/user/posts?${qs}`, { headers: bearer(token) })
}

export async function createPost(token: string, content: string, images: string[]): Promise<{ id: number }> {
  return request('/user/posts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify({ content, images }),
  })
}

export async function likePost(token: string, id: number): Promise<{ id: number; liked: boolean; likes: number }> {
  return request(`/user/posts/${id}/like`, { method: 'POST', headers: bearer(token) })
}

// ---------- 动态回复（楼中楼 + 回复点赞） ----------

export interface PostReply {
  id: number
  user_id: number
  author_nickname: string
  author_avatar: string | null
  content: string
  created_at: string
  likes: number
  liked: boolean
  reply_to_nickname: string
  /** 树挂载点（一级回复 id），二级回复上回复时以此作为 parent_id */
  parent_id?: number
  children: PostReply[]
}

export async function listPostReplies(token: string, postId: number): Promise<{ replies: PostReply[] }> {
  return request(`/user/posts/${postId}/replies`, { headers: bearer(token) })
}

export async function createReply(
  token: string,
  postId: number,
  body: { content: string; parent_id?: number | null },
): Promise<{ id: number }> {
  return request(`/user/posts/${postId}/replies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(body),
  })
}

export async function likeReply(token: string, replyId: number): Promise<{ id: number; liked: boolean; likes: number }> {
  return request(`/user/replies/${replyId}/like`, { method: 'POST', headers: bearer(token) })
}

export async function hotQuestions(token: string): Promise<{ hot: { question: string; score: number }[] }> {
  return request('/user/hot', { headers: bearer(token) })
}

export async function uploadPostImage(token: string, file: File): Promise<{ path: string }> {
  const form = new FormData()
  form.append('file', file)
  return request('/user/uploads', { method: 'POST', headers: bearer(token), body: form })
}

// ---------- 纠错 / 通知 ----------

export interface Correction {
  id: number
  doc_ref: string | null
  original_text: string | null
  corrected_text: string
  reason: string | null
  status: string
  admin_reply: string | null
  created_at: string
}

export async function submitCorrection(
  token: string,
  body: { doc_ref?: string; original_text?: string; corrected_text: string; reason?: string },
): Promise<{ id: number; status: string }> {
  return request('/user/corrections', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify(body),
  })
}

export async function listCorrections(
  token: string,
): Promise<{ corrections: Correction[] }> {
  return request('/user/corrections', { headers: bearer(token) })
}

export interface Notification {
  id: number
  type: string
  title: string
  content: string | null
  is_read: number
  created_at: string
}

export async function listNotifications(
  token: string,
): Promise<{ unread: number; notifications: Notification[] }> {
  return request('/user/notifications', { headers: bearer(token) })
}

export async function markNotificationsRead(
  token: string,
  ids?: number[],
): Promise<{ updated: number }> {
  return request('/user/notifications/read', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...bearer(token) },
    body: JSON.stringify({ ids: ids ?? null }),
  })
}
