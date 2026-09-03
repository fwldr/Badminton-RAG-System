/** 问答：POST /chat（Agentic RAG）+ 反馈。 */

import { request } from './request'

export interface ChatSource {
  table: string
  brand: string
  model: string
}

export interface ChatImage {
  url: string
  title?: string
}

export interface ChatResponse {
  answer: string
  sources: ChatSource[]
  images?: ChatImage[]
  clarification: string | null
  trace: { node: string; input: Record<string, unknown>; output: Record<string, unknown> }[]
  trace_id: string
  cached: boolean
}

export interface ChatMessageData {
  role: 'user' | 'assistant'
  content: string
  question?: string
  sources?: ChatSource[]
  images?: ChatImage[]
  cached?: boolean
  failed?: boolean
}

export function chat(
  sessionId: string,
  question: string,
  scope?: string | null,
): Promise<ChatResponse> {
  return request<ChatResponse>('/chat', {
    method: 'POST',
    data: { session_id: sessionId, question, scope: scope || null },
    timeout: 60000,
  })
}

export function sendFeedback(payload: {
  session_id: string
  question: string
  answer: string
  rating: 1 | -1
  comment?: string
}): Promise<unknown> {
  return request('/feedback', { method: 'POST', data: payload })
}
