/* 首页（智能问答中枢）：会话 + 预设问题卡片 + 范围限定 + 语音输入 + 回答辅助操作 */

import { useEffect, useRef, useState } from 'react'
import { chat, type ChatResponse } from '../api/client'
import type { AuthUser } from '../api/auth'
import { newSessionId } from '../api/sessions'
import ChatMessage, { type ChatMessageData } from '../components/ChatMessage'

export interface ChatInit {
  sessionId: string | null
  title: string
  messages: ChatMessageData[]
}

export interface AskRequest {
  key: number
  question: string
  scope: string | null
}

interface Props {
  token: string
  user: AuthUser
  initial: ChatInit
  ask: AskRequest | null
  onAskConsumed: () => void
  onChanged: () => void
  onAdmin: () => void
  onLogout: () => void
}

const PRESETS = [
  '最新双打发球规则',
  '如何预防网球肘',
  '反手高远球动作要领',
  '4U 和 5U 球拍有什么区别？',
]

const SCOPES: { key: string; label: string }[] = [
  { key: 'all', label: '🌐 全部' },
  { key: 'rules', label: '📜 仅规则' },
  { key: 'technique', label: '🏸 仅技术' },
  { key: 'equipment', label: '🎾 仅装备' },
  { key: 'document', label: '📄 仅文档' },
]

/** 从 /chat 响应组装 assistant 消息（从 trace 提取 route / retry_count） */
function toMessage(question: string, resp: ChatResponse): ChatMessageData {
  let route: string | undefined
  let retry_count: number | undefined
  for (const t of resp.trace) {
    const out = t.output || {}
    if (!route && typeof out.route === 'string') route = out.route
    if (retry_count === undefined && typeof out.retry_count === 'number') retry_count = out.retry_count
  }
  return {
    role: 'assistant',
    content: resp.answer,
    question,
    sources: resp.sources,
    images: resp.images || [],
    clarification: resp.clarification,
    trace_id: resp.trace_id,
    cached: resp.cached,
    langfuse_url: resp.langfuse_url,
    route,
    retry_count,
  }
}

export default function ChatPage({
  token,
  user,
  initial,
  ask,
  onAskConsumed,
  onChanged,
  onAdmin,
  onLogout,
}: Props) {
  const [messages, setMessages] = useState<ChatMessageData[]>(initial.messages)
  const [sid, setSid] = useState<string | null>(initial.sessionId)
  const [input, setInput] = useState('')
  const [scope, setScope] = useState('all')
  const [loading, setLoading] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const recogRef = useRef<{ stop: () => void } | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // 来自「发现」页的一键提问（范围预置）
  // ref 防抖：React StrictMode（dev）会双跑 effect，避免同一条问题被发送两次
  const handledAskKey = useRef<number | null>(null)
  useEffect(() => {
    if (ask && handledAskKey.current !== ask.key) {
      handledAskKey.current = ask.key
      onAskConsumed()
      void handleSend(ask.question, ask.scope)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask?.key])

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 3500)
  }

  const handleSend = async (rawQuestion?: string, rawScope?: string | null) => {
    const question = (rawQuestion ?? input).trim()
    const useScope = rawScope === undefined ? (scope === 'all' ? null : scope) : rawScope
    if (!question || loading) return
    const sid2 = sid ?? newSessionId()
    if (!sid) setSid(sid2)

    setMessages((prev) => [...prev, { role: 'user', content: question }])
    setInput('')
    setLoading(true)
    try {
      const resp = await chat(sid2, question, token, useScope)
      setMessages((prev) => [...prev, toMessage(question, resp)])
      if (resp.cached) showToast('⚡ 命中 FAQ 缓存，秒回')
      onChanged()
    } catch (e) {
      const msg = e instanceof Error ? e.message : '网络错误'
      showToast(`❌ ${msg}`)
      setMessages((prev) => [...prev, { role: 'assistant', content: `（请求失败：${msg}）` }])
    } finally {
      setLoading(false)
    }
  }

  const toggleVoice = () => {
    const SR = (window as unknown as { webkitSpeechRecognition?: new () => unknown }).webkitSpeechRecognition
      || (window as unknown as { SpeechRecognition?: new () => unknown }).SpeechRecognition
    if (!SR) {
      showToast('当前浏览器不支持语音输入（建议使用 Chrome）')
      return
    }
    if (recording && recogRef.current) {
      recogRef.current.stop()
      setRecording(false)
      return
    }
    try {
      const recog = new SR() as unknown as {
        lang: string
        interimResults: boolean
        onresult: (e: { results: { [i: number]: { 0: { transcript: string } } } }) => void
        onend: () => void
        onerror: () => void
        start: () => void
        stop: () => void
      }
      recog.lang = 'zh-CN'
      recog.interimResults = false
      recog.onresult = (e) => {
        const text = e.results[0]?.[0]?.transcript || ''
        if (text) setInput((prev) => (prev ? `${prev} ${text}` : text))
      }
      recog.onend = () => setRecording(false)
      recog.onerror = () => setRecording(false)
      recog.start()
      recogRef.current = recog
      setRecording(true)
    } catch {
      showToast('语音输入启动失败')
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSend()
    }
  }

  const isAdmin = user.role === 'admin'
  // 偏好设置：显示引用来源（pref_show_sources，默认显示）
  const showSources = (user.pref_show_sources ?? 1) === 1

  return (
    <div className="page chat-page">
      <header className="page-header">
        <div>
          <span className="dot" />
          <span>
            {user.nickname || user.username}（{isAdmin ? '管理员' : '用户'}）· 在线
          </span>
        </div>
        <div className="header-actions">
          {isAdmin && (
            <button className="icon-btn" onClick={onAdmin} title="管理端">
              🛡️
            </button>
          )}
          <button className="icon-btn" onClick={onLogout} title="退出登录">
            退出
          </button>
        </div>
      </header>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="empty">
            <div className="big">🏸</div>
            <p>你好，{user.nickname || user.username}！我是羽问～</p>
            <p>可以问我规则、技术、装备、伤病康复等问题（支持多轮对话）</p>
          </div>
        )}
        {messages.map((m, i) => (
          <ChatMessage key={i} msg={m} sessionId={sid ?? ''} token={token} showSources={showSources} />
        ))}
        {loading && (
          <div className="msg assistant">
            <div className="avatar">🏸</div>
            <div className="bubble">
              <div className="typing">
                思考中
                <i />
                <i />
                <i />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="composer-wrap">
        <div className="scope-row">
          <div className="preset-chips">
            {PRESETS.map((p) => (
              <button key={p} className="chip" disabled={loading} onClick={() => void handleSend(p)}>
                {p}
              </button>
            ))}
          </div>
          <select
            className="scope-select"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            title="检索范围"
          >
            {SCOPES.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault()
            void handleSend()
          }}
        >
          <button
            type="button"
            className={`icon-btn mic${recording ? ' recording' : ''}`}
            onClick={toggleVoice}
            title={recording ? '停止录音' : '语音输入'}
          >
            {recording ? '⏹' : '🎤'}
          </button>
          <textarea
            value={input}
            placeholder="输入你的羽毛球问题，回车发送（Shift+Enter 换行）"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
          />
          <button type="submit" className="send-btn" disabled={loading || !input.trim()}>
            发送
          </button>
        </form>
      </div>
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
