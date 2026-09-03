/* 发现页：热门问答排行 + 球友动态（文本+图片+点赞+楼中楼回复） */

import { useEffect, useRef, useState } from 'react'
import {
  hotQuestions,
  listPosts,
  likePost,
  uploadPostImage,
  createPost,
  listPostReplies,
  createReply,
  likeReply,
  type Post,
  type PostReply,
} from '../api/user'

interface Props {
  token: string
  onAsk: (question: string, scope: string | null) => void
}

interface ReplyTarget {
  parentId: number | null
  nickname: string
}

export default function DiscoverPage({ token, onAsk }: Props) {
  const [hot, setHot] = useState<{ question: string; score: number }[]>([])
  const [posts, setPosts] = useState<Post[]>([])
  const [content, setContent] = useState('')
  const [images, setImages] = useState<string[]>([])
  const [publishing, setPublishing] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // 回复区状态：按动态 id 管理（展开状态 / 回复树 / 输入框 / 回复目标 / 发送中）
  const [openReplies, setOpenReplies] = useState<Record<number, boolean>>({})
  const [repliesMap, setRepliesMap] = useState<Record<number, PostReply[]>>({})
  const [replyInputs, setReplyInputs] = useState<Record<number, string>>({})
  const [replyTargets, setReplyTargets] = useState<Record<number, ReplyTarget | null>>({})
  const [sendingReplies, setSendingReplies] = useState<Record<number, boolean>>({})

  const load = async () => {
    try {
      const [h, p] = await Promise.all([hotQuestions(token), listPosts(token)])
      setHot(h.hot)
      setPosts(p.posts)
    } catch {
      setMsg('加载失败（请确认已登录）')
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleUploadImage = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return
    try {
      const res = await uploadPostImage(token, file)
      setImages((prev) => [...prev, res.path].slice(0, 3))
      setMsg('图片已上传（最多 3 张）')
      if (fileRef.current) fileRef.current.value = ''
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '图片上传失败')
    }
  }

  const handlePublish = async () => {
    if (!content.trim() && images.length === 0) return
    setPublishing(true)
    setMsg(null)
    try {
      await createPost(token, content.trim(), images)
      setContent('')
      setImages([])
      setMsg('✅ 发布成功')
      await load()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '发布失败')
    } finally {
      setPublishing(false)
    }
  }

  const handleLike = async (p: Post) => {
    try {
      const res = await likePost(token, p.id)
      setPosts((prev) => prev.map((x) => (x.id === p.id ? { ...x, liked: res.liked, likes: res.likes } : x)))
    } catch {
      // 忽略
    }
  }

  // ---------- 回复 ----------

  const toggleReplies = async (postId: number) => {
    const next = !openReplies[postId]
    setOpenReplies((prev) => ({ ...prev, [postId]: next }))
    if (next && !repliesMap[postId]) {
      try {
        const res = await listPostReplies(token, postId)
        setRepliesMap((prev) => ({ ...prev, [postId]: res.replies }))
      } catch (e) {
        setMsg(e instanceof Error ? e.message : '回复加载失败')
      }
    }
  }

  const setReplyText = (postId: number, text: string) => {
    setReplyInputs((prev) => ({ ...prev, [postId]: text }))
  }

  const startReply = (postId: number, target: ReplyTarget) => {
    setReplyTargets((prev) => ({ ...prev, [postId]: target }))
    setOpenReplies((prev) => ({ ...prev, [postId]: true }))
  }

  const cancelReply = (postId: number) => {
    setReplyTargets((prev) => ({ ...prev, [postId]: null }))
  }

  const sendReply = async (postId: number) => {
    const text = (replyInputs[postId] ?? '').trim()
    if (!text) return
    setSendingReplies((prev) => ({ ...prev, [postId]: true }))
    try {
      const target = replyTargets[postId] ?? null
      await createReply(token, postId, { content: text, parent_id: target?.parentId ?? null })
      setReplyText(postId, '')
      cancelReply(postId)
      const res = await listPostReplies(token, postId)
      setRepliesMap((prev) => ({ ...prev, [postId]: res.replies }))
      await load() // 以服务端数据刷新 reply_count / liked
      setOpenReplies((prev) => ({ ...prev, [postId]: true }))
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '回复失败')
    } finally {
      setSendingReplies((prev) => ({ ...prev, [postId]: false }))
    }
  }

  const handleReplyLike = async (postId: number, replyId: number) => {
    const apply = (r: PostReply): PostReply =>
      r.id === replyId
        ? { ...r, liked: !r.liked, likes: r.liked ? r.likes - 1 : r.likes + 1 }
        : { ...r, children: r.children.map(apply) }
    setRepliesMap((prev) => ({ ...prev, [postId]: (prev[postId] ?? []).map(apply) }))
    try {
      const res = await likeReply(token, replyId)
      const applyRes = (r: PostReply): PostReply =>
        r.id === replyId ? { ...r, liked: res.liked, likes: res.likes } : { ...r, children: r.children.map(applyRes) }
      setRepliesMap((prev) => ({ ...prev, [postId]: (prev[postId] ?? []).map(applyRes) }))
    } catch {
      // 忽略
    }
  }

  const renderReplyItem = (postId: number, r: PostReply, isChild = false) => (
    <div className={`reply-item${isChild ? ' reply-sub' : ''}`} key={r.id}>
      <div className="reply-head">
        <span className="avatar-dot">{r.author_avatar || r.author_nickname?.[0] || '👤'}</span>
        <span className="reply-author">{r.author_nickname}</span>
        {isChild && r.reply_to_nickname && (
          <span className="muted reply-to">回复 @{r.reply_to_nickname}</span>
        )}
        <span className="muted reply-time">{r.created_at}</span>
      </div>
      <p className="reply-content">{r.content}</p>
      <div className="reply-actions">
        <button
          className={`chip like-btn${r.liked ? ' active' : ''}`}
          onClick={() => void handleReplyLike(postId, r.id)}
        >
          👍 {r.likes}
        </button>
        <button
          className="chip"
          onClick={() =>
            startReply(postId, {
              parentId: isChild && r.parent_id != null ? r.parent_id : r.id,
              nickname: r.author_nickname,
            })
          }
        >
          回复
        </button>
      </div>
    </div>
  )

  const renderReplies = (postId: number) => {
    const list = repliesMap[postId] ?? []
    const target = replyTargets[postId] ?? null
    const input = replyInputs[postId] ?? ''
    return (
      <div className="reply-section">
        {list.length === 0 && <p className="muted reply-empty">暂无回复，快来抢沙发～</p>}
        {list.map((r) => (
          <div key={r.id}>
            {renderReplyItem(postId, r)}
            {r.children.length > 0 && <div className="reply-children">{r.children.map((c) => renderReplyItem(postId, c, true))}</div>}
          </div>
        ))}
        <div className="reply-input-row">
          <input
            className="reply-input"
            placeholder={target ? `回复 @${target.nickname}…` : '写下你的回复…'}
            value={input}
            maxLength={500}
            onChange={(e) => setReplyText(postId, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void sendReply(postId)
            }}
          />
          <button
            className="send-btn"
            disabled={sendingReplies[postId] || !input.trim()}
            onClick={() => void sendReply(postId)}
          >
            {sendingReplies[postId] ? '发送中…' : target ? '回复' : '发送'}
          </button>
          {target && (
            <button className="chip" onClick={() => cancelReply(postId)}>
              取消
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="page discover-page">
      <header className="page-header">
        <span>🧭 发现</span>
        <span className="muted">热门 · 球友动态</span>
      </header>

      <div className="scroll-area">
        <h3 className="section-title">🔥 热门问答</h3>
        <div className="hot-list">
          {hot.map((h, i) => (
            <button className="hot-item" key={i} onClick={() => onAsk(h.question, null)}>
              <span className="hot-rank">{i + 1}</span>
              <span className="hot-q">{h.question}</span>
              <span className="hot-score">{h.score} ❤️</span>
            </button>
          ))}
          {hot.length === 0 && <p className="muted">暂无热门（多点赞、多收藏会出现在这里）</p>}
        </div>

        <h3 className="section-title">🏸 球友动态</h3>
        <div className="post-editor">
          <textarea
            placeholder="分享训练心得、比赛经验或提问（文本 + 图片）…"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={3}
            maxLength={1000}
          />
          <div className="post-editor-actions">
            <div className="post-images">
              {images.map((p) => (
                <img key={p} src={p} alt="动态配图" className="post-thumb" />
              ))}
              <button className="icon-btn" onClick={() => fileRef.current?.click()}>
                🖼️ 图片
              </button>
              <input ref={fileRef} type="file" accept="image/*" hidden onChange={() => void handleUploadImage()} />
            </div>
            <button className="send-btn" disabled={publishing || (!content.trim() && images.length === 0)} onClick={() => void handlePublish()}>
              {publishing ? '发布中…' : '发布'}
            </button>
          </div>
        </div>

        <div className="post-list">
          {posts.map((p) => (
            <article className="post-card" key={p.id}>
              <div className="post-head">
                <span className="avatar-dot">{p.author_avatar || p.author_nickname?.[0] || '👤'}</span>
                <div>
                  <div className="post-author">{p.author_nickname}</div>
                  <div className="muted">{p.created_at}</div>
                </div>
              </div>
              <p className="post-content">{p.content}</p>
              {p.images.length > 0 && (
                <div className="post-gallery">
                  {p.images.map((img) => (
                    <img key={img} src={img} alt="动态配图" />
                  ))}
                </div>
              )}
              <div className="post-actions">
                <button className={`chip like-btn${p.liked ? ' active' : ''}`} onClick={() => void handleLike(p)}>
                  👍 {p.likes}
                </button>
                <button className="chip" onClick={() => void toggleReplies(p.id)}>
                  💬 {p.reply_count} {openReplies[p.id] ? '收起' : '评论'}
                </button>
              </div>
              {openReplies[p.id] && renderReplies(p.id)}
            </article>
          ))}
        </div>
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  )
}
