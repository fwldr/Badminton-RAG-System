/** 用户端数据：目录/热门/会话/收藏/动态/通知/纠错（全部映射现有后端接口）。 */

import Taro from '@tarojs/taro'
import { API_BASE_URL, getToken, request, type ListResp } from './request'

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '' && v !== false) parts.push(`${k}=${encodeURIComponent(String(v))}`)
  }
  return parts.length ? `?${parts.join('&')}` : ''
}

// ---------- 知识库目录（公开） ----------

export interface CatalogGroup {
  name: string
  tables: { table: string; chunks: number }[]
  total?: number
}

export function getCatalog(): Promise<{ groups: CatalogGroup[] }> {
  return request('/kb/catalog', { auth: false })
}

// ---------- 热门问答（登录可选；后端 /user/hot 需登录） ----------

export interface HotItem {
  question: string
  score: number
}

export function getHot(): Promise<{ hot: HotItem[] }> {
  return request('/user/hot', { auth: true })
}

// ---------- 会话（登录） ----------

export interface Conversation {
  id: number
  session_id: string
  title: string
  tag: string | null
  is_favorite: number
  updated_at: string
  message_count?: number
}

export interface ConvMessage {
  id: number
  role: string
  content: string
  sources: { table: string; brand: string; model: string }[]
  trace_id: string | null
  cached: number
  created_at: string
}

export function listConversations(params: { q?: string; tag?: string; favorite?: boolean } = {}): Promise<{ conversations: Conversation[]; total: number }> {
  return request(`/user/conversations${qs({ q: params.q, tag: params.tag, favorite: params.favorite ? 1 : undefined })}`)
}

export function getConversation(convId: number): Promise<Conversation & { messages: ConvMessage[] }> {
  return request(`/user/conversations/${convId}`)
}

export function patchConversation(
  convId: number,
  fields: { title?: string; tag?: string | null; is_favorite?: boolean },
): Promise<Conversation> {
  return request(`/user/conversations/${convId}`, { method: 'PATCH', data: fields })
}

export function deleteConversation(convId: number): Promise<{ id: number }> {
  return request(`/user/conversations/${convId}`, { method: 'DELETE' })
}

// ---------- 收藏夹与文件夹（登录） ----------

export interface FavoriteFolder {
  id: number
  name: string
  count?: number
}

export interface FavoriteItem {
  id: number
  folder_id: number | null
  question: string
  answer: string
  created_at: string
}

export function listFolders(): Promise<{ folders: FavoriteFolder[] }> {
  return request('/user/folders')
}

export function createFolder(name: string): Promise<{ id: number }> {
  return request('/user/folders', { method: 'POST', data: { name } })
}

export function deleteFolder(folderId: number): Promise<{ id: number }> {
  return request(`/user/folders/${folderId}`, { method: 'DELETE' })
}

export function listFavorites(params: { folder_id?: number; q?: string } = {}): Promise<ListResp<FavoriteItem>> {
  return request(`/user/favorites${qs(params)}`)
}

export function createFavorite(payload: { question: string; answer: string; sources?: { table: string; brand: string; model: string }[] }): Promise<{ id: number }> {
  return request('/user/favorites', { method: 'POST', data: payload })
}

export function moveFavorite(favId: number, folder_id: number | null): Promise<{ id: number }> {
  return request(`/user/favorites/${favId}`, { method: 'PATCH', data: { folder_id } })
}

export function deleteFavorite(favId: number): Promise<{ id: number }> {
  return request(`/user/favorites/${favId}`, { method: 'DELETE' })
}

// ---------- 动态（登录） ----------

export interface PostItem {
  id: number
  user_id: number
  author_nickname: string
  author_avatar: string | null
  content: string
  images: string[]
  likes: number
  reply_count: number
  liked: boolean
  created_at: string
}

export interface ReplyItem {
  id: number
  user_id: number
  author_nickname: string
  author_avatar: string | null
  content: string
  created_at: string
  likes: number
  liked: boolean
  reply_to_nickname: string
  parent_id?: number
  children?: ReplyItem[]
}

export function getPostDetail(postId: number): Promise<{ post: PostItem }> {
  return request(`/user/posts/${postId}`)
}

export function listReplies(postId: number): Promise<{ replies: ReplyItem[] }> {
  return request(`/user/posts/${postId}/replies`)
}

export function createReply(postId: number, content: string, parentId?: number): Promise<{ id: number }> {
  return request(`/user/posts/${postId}/replies`, { method: 'POST', data: { content, parent_id: parentId } })
}

export function toggleReplyLike(replyId: number): Promise<{ id: number; liked: boolean; likes: number }> {
  return request(`/user/replies/${replyId}/like`, { method: 'POST' })
}

export function listPosts(params: { limit?: number; offset?: number } = {}): Promise<{ posts: PostItem[]; total: number }> {
  return request(`/user/posts${qs(params)}`)
}

export function createPost(payload: { content: string; images?: string[] }): Promise<{ id: number }> {
  return request('/user/posts', { method: 'POST', data: payload })
}

export function togglePostLike(postId: number): Promise<{ id: number; liked: boolean; likes: number }> {
  return request(`/user/posts/${postId}/like`, { method: 'POST' })
}

// ---------- 通知（登录） ----------

export interface NotificationItem {
  id: number
  type: string
  title: string
  content: string
  is_read: number
  created_at: string
}

export function listNotifications(): Promise<{ unread: number; notifications: NotificationItem[] }> {
  return request('/user/notifications')
}

export function markNotificationsRead(ids?: number[]): Promise<{ updated: number }> {
  return request('/user/notifications/read', { method: 'POST', data: { ids: ids || null } })
}

// ---------- 纠错（登录） ----------

export interface CorrectionItem {
  id: number
  doc_ref: string | null
  original_text: string | null
  corrected_text: string
  reason: string | null
  status: 'pending' | 'accepted' | 'rejected' | 'discussion'
  admin_reply: string | null
  created_at: string
}

export function listCorrections(): Promise<{ corrections: CorrectionItem[] }> {
  return request('/user/corrections')
}

export function submitCorrection(payload: {
  doc_ref?: string
  original_text?: string
  corrected_text: string
  reason?: string
}): Promise<{ id: number; status: string }> {
  return request('/user/corrections', { method: 'POST', data: payload })
}

// ---------- 图片上传（动态配图，≤2MB） ----------

export function uploadImage(filePath: string): Promise<{ path: string }> {
  // wx.uploadFile 与统一 request 不同（multipart），单独走 Taro.uploadFile
  return new Promise((resolve, reject) => {
    Taro.uploadFile({
      url: `${API_BASE_URL}/user/uploads`,
      filePath,
      name: 'file',
      header: { Authorization: `Bearer ${getToken()}` },
      success: (res) => {
        try {
          const body = JSON.parse(res.data || '{}')
          if (body.code === 0) resolve(body.data)
          else reject(new Error(body.message || '上传失败'))
        } catch {
          reject(new Error('上传失败'))
        }
      },
      fail: () => reject(new Error('上传失败')),
    })
  })
}
