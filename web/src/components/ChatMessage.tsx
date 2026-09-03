/* 消息气泡：answer + 引用可点 + cached badge + trace 链接
   + 辅助操作（复制 / 收藏）+ 赞 / 踩（点踩弹窗补充原因与说明）
   + 图片文档：内联渲染 markdown 图片 / [图片] 文件名 占位，未内联的走底部图集 */

import { useState, type ReactNode } from 'react'
import { sendFeedback } from '../api/client'
import { createFavorite } from '../api/user'

export interface ChatMessageData {
  role: 'user' | 'assistant'
  content: string
  question?: string
  sources?: { table: string; brand: string; model: string }[]
  images?: { url: string; title?: string }[]
  clarification?: string | null
  trace_id?: string
  cached?: boolean
  langfuse_url?: string | null
  route?: string
  retry_count?: number
}

interface Props {
  msg: ChatMessageData
  sessionId: string
  token?: string | null
  showSources?: boolean
}

const DISLIKE_REASONS = ['答案有误', '答非所问', '内容过时', '其他']

/** 图片引用列表里按文件名/URL 匹配（[图片] xx.png 占位内联用） */
function matchImage(images: { url: string; title?: string }[], fileName: string) {
  const name = fileName.trim()
  return images.find(
    (im) => (im.title && (im.title === name || im.title.includes(name))) || im.url.includes(name),
  )
}

/** 把回答文本切成 文本/图片 混合节点：支持 markdown ![alt](url) 与 [图片] 文件名 占位。
    返回 [节点数组, 已被内联的图片 URL 集合]（未内联的走底部图集兜底）。 */
function renderContent(content: string, images: { url: string; title?: string }[]): [ReactNode[], Set<string>] {
  const used = new Set<string>()
  let key = 0
  const nodes: ReactNode[] = []
  const pushText = (text: string) => {
    if (text) nodes.push(<span key={key++}>{text}</span>)
  }
  // 1) markdown 图片语法 ![alt](url)
  const mdRe = /!\[([^\]]*)\]\(([^)]+)\)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = mdRe.exec(content)) !== null) {
    pushText(content.slice(last, m.index))
    const url = m[2].trim()
    used.add(url)
    nodes.push(<img key={key++} className="msg-img" src={url} alt={m[1] || '图片'} loading="lazy" />)
    last = mdRe.lastIndex
  }
  pushText(content.slice(last))
  // 2) [图片] 文件名 占位 → 图片列表里匹配则内联（保留文本节点内的其他文字）
  const phRe = /\[图片\]\s*([^\s，。；、]+\.(?:png|jpe?g|webp|bmp))/gi
  const finalNodes: ReactNode[] = []
  for (const nd of nodes) {
    if (!nd || typeof nd !== 'object' || !('props' in nd) || typeof (nd.props as { src?: unknown }).src === 'string') {
      finalNodes.push(nd)
      continue
    }
    const text = (nd.props as { children: string }).children
    let pLast = 0
    let pm: RegExpExecArray | null
    while ((pm = phRe.exec(text)) !== null) {
      if (pLast < pm.index) finalNodes.push(<span key={key++}>{text.slice(pLast, pm.index)}</span>)
      const im = matchImage(images, pm[1])
      if (im) {
        used.add(im.url)
        finalNodes.push(<img key={key++} className="msg-img" src={im.url} alt={im.title || '图片'} loading="lazy" />)
      } else {
        finalNodes.push(<span key={key++}>{pm[0]}</span>)
      }
      pLast = phRe.lastIndex
    }
    if (pLast < text.length) finalNodes.push(<span key={key++}>{text.slice(pLast)}</span>)
  }
  return [finalNodes, used]
}

export default function ChatMessage({ msg, sessionId, token, showSources = true }: Props) {
  const [fb, setFb] = useState<'like' | 'dislike' | null>(null)
  const [fav, setFav] = useState<boolean | null>(null)
  const [showDislike, setShowDislike] = useState(false)
  const [reason, setReason] = useState(DISLIKE_REASONS[0])
  const [comment, setComment] = useState('')
  const [copied, setCopied] = useState(false)

  const submitFeedback = async (rating: 1 | -1) => {
    if (fb) return
    if (rating === -1) {
      setShowDislike(true)
      return
    }
    try {
      await sendFeedback(
        { session_id: sessionId, question: msg.question || '', answer: msg.content, rating, trace_id: msg.trace_id },
        token,
      )
      setFb('like')
    } catch {
      // 反馈失败不打扰用户
    }
  }

  const submitDislike = async () => {
    const fullComment = reason === '其他' && comment.trim() ? comment.trim() : `${reason}${comment.trim() ? `：${comment.trim()}` : ''}`
    setShowDislike(false)
    setComment('')
    try {
      await sendFeedback(
        {
          session_id: sessionId,
          question: msg.question || '',
          answer: msg.content,
          rating: -1,
          comment: fullComment,
          trace_id: msg.trace_id,
        },
        token,
      )
      setFb('dislike')
    } catch {
      setFb('dislike')
    }
  }

  const handleFavorite = async () => {
    try {
      await createFavorite(token || '', {
        question: msg.question || '',
        answer: msg.content,
        sources: msg.sources || [],
      })
      setFav(true)
    } catch {
      setFav(false)
    }
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // 剪贴板不可用时忽略
    }
  }

  if (msg.role === 'user') {
    return (
      <div className="msg user">
        <div className="avatar">🧑</div>
        <div className="bubble">
          <div className="text">{msg.content}</div>
        </div>
      </div>
    )
  }

  const images = msg.images || []
  const [contentNodes, usedUrls] = renderContent(msg.content, images)
  // 未在正文内联的图片 → 底部图集兜底展示
  const unshownImages = images.filter((im) => !usedUrls.has(im.url))

  return (
    <div className="msg assistant">
      <div className="avatar">🏸</div>
      <div className="bubble">
        <div className="text">{contentNodes}</div>

        {unshownImages.length > 0 && (
          <div className="msg-images">
            {unshownImages.map((im) => (
              <figure key={im.url} className="msg-image-item">
                <img className="msg-img" src={im.url} alt={im.title || '图片'} loading="lazy" />
                {im.title && <figcaption>{im.title}</figcaption>}
              </figure>
            ))}
          </div>
        )}

        {msg.clarification && <div className="clarify">💡 {msg.clarification}</div>}

        <div className="meta">
          {msg.cached && <span className="badge cached">⚡ 秒回（缓存命中）</span>}
          {msg.retry_count && msg.retry_count > 0 ? (
            <span className="badge retry">重试 {msg.retry_count} 次</span>
          ) : null}
          <div className="actions">
            {msg.question && (
              <button
                className={`icon-btn${fav === true ? ' liked' : ''}`}
                disabled={fav === true}
                onClick={() => void handleFavorite()}
                title="收藏到工作台"
              >
                {fav === true ? '⭐ 已收藏' : '⭐ 收藏'}
              </button>
            )}
            <button className="icon-btn" onClick={() => void handleCopy()} title="复制答案">
              {copied ? '✅ 已复制' : '📋 复制'}
            </button>
            <button
              className={`icon-btn${fb === 'like' ? ' liked' : ''}`}
              disabled={!!fb}
              onClick={() => void submitFeedback(1)}
              title="有帮助"
            >
              👍
            </button>
            <button
              className={`icon-btn${fb === 'dislike' ? ' disliked' : ''}`}
              disabled={!!fb}
              onClick={() => void submitFeedback(-1)}
              title="没帮助（点击后补充原因）"
            >
              👎
            </button>
          </div>
        </div>

        {showSources && msg.sources && msg.sources.length > 0 && (
          <details className="source-popover">
            <summary>📎 引用来源（{msg.sources.length} 条）</summary>
            <table>
              <thead>
                <tr>
                  <th>表</th>
                  <th>品牌</th>
                  <th>型号</th>
                </tr>
              </thead>
              <tbody>
                {msg.sources.map((s, i) => (
                  <tr key={i}>
                    <td>{s.table}</td>
                    <td>{s.brand}</td>
                    <td>{s.model}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </div>

      {showDislike && (
        <div className="modal-mask" onClick={() => setShowDislike(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h4>👎 这个回答有什么问题？</h4>
            <div className="reason-options">
              {DISLIKE_REASONS.map((r) => (
                <button
                  key={r}
                  className={reason === r ? 'active' : ''}
                  onClick={() => setReason(r)}
                >
                  {r}
                </button>
              ))}
            </div>
            <textarea
              placeholder="补充说明（可选）"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={2}
            />
            <div className="modal-actions">
              <button className="icon-btn" onClick={() => setShowDislike(false)}>
                取消
              </button>
              <button className="send-btn" onClick={() => void submitDislike()}>
                提交反馈
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
