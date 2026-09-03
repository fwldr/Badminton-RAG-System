/* 工作台：历史对话记录（搜索/标签/重命名/收藏/删除）+ 个人收藏夹（文件夹分类） */

import { useCallback, useEffect, useState } from 'react'
import {
  createFolder,
  deleteConversation,
  deleteFavorite,
  deleteFolder,
  getConversation,
  listConversations,
  listFavorites,
  listFolders,
  patchConversation,
  patchFavorite,
  type Conversation,
  type Favorite,
  type FavoriteFolder,
} from '../api/user'
import type { ChatMessageData } from '../components/ChatMessage'

export interface OpenPayload {
  sessionId: string
  title: string
  messages: ChatMessageData[]
}

interface Props {
  token: string
  refreshKey: number
  onOpen: (payload: OpenPayload) => void
}

const TAGS = ['规则类', '技术类', '装备类', '其他']

function detailToMessages(rows: { role: string; content: string; sources: { table: string; brand: string; model: string }[]; trace_id: string | null; cached: number }[]): ChatMessageData[] {
  let lastUser = ''
  return rows.map((m) => {
    if (m.role === 'user') {
      lastUser = m.content
      return { role: 'user', content: m.content } as ChatMessageData
    }
    return {
      role: 'assistant',
      content: m.content,
      question: lastUser || undefined,
      sources: m.sources,
      trace_id: m.trace_id || undefined,
      cached: m.cached === 1,
    } as ChatMessageData
  })
}

export default function WorkbenchPage({ token, refreshKey, onOpen }: Props) {
  const [tab, setTab] = useState<'history' | 'favorites'>('history')
  const [q, setQ] = useState('')
  const [tag, setTag] = useState('')
  const [onlyFav, setOnlyFav] = useState(false)
  const [convs, setConvs] = useState<Conversation[]>([])
  const [total, setTotal] = useState(0)
  const [favs, setFavs] = useState<Favorite[]>([])
  const [favTotal, setFavTotal] = useState(0)
  const [folders, setFolders] = useState<FavoriteFolder[]>([])
  const [folderFilter, setFolderFilter] = useState<number | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const loadConvs = useCallback(async () => {
    try {
      const data = await listConversations(token, { q: q || undefined, tag: tag || undefined, favorite: onlyFav })
      setConvs(data.conversations)
      setTotal(data.total)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '加载会话失败')
    }
  }, [token, q, tag, onlyFav])

  const loadFavs = useCallback(async () => {
    try {
      const [f, fd] = await Promise.all([
        listFavorites(token, { folder_id: folderFilter }),
        listFolders(token),
      ])
      setFavs(f.favorites)
      setFavTotal(f.total)
      setFolders(fd.folders)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '加载收藏失败')
    }
  }, [token, folderFilter])

  useEffect(() => {
    void loadConvs()
  }, [loadConvs, refreshKey])

  useEffect(() => {
    void loadFavs()
  }, [loadFavs, refreshKey])

  const openConversation = async (c: Conversation) => {
    try {
      const detail = await getConversation(token, c.id)
      onOpen({ sessionId: c.session_id, title: c.title, messages: detailToMessages(detail.messages) })
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '打开失败')
    }
  }

  const rename = async (c: Conversation) => {
    const title = window.prompt('重命名会话', c.title)
    if (title == null || !title.trim()) return
    try {
      await patchConversation(token, c.id, { title: title.trim() })
      void loadConvs()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '重命名失败')
    }
  }

  const toggleFav = async (c: Conversation) => {
    try {
      await patchConversation(token, c.id, { is_favorite: c.is_favorite !== 1 })
      void loadConvs()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '收藏失败')
    }
  }

  const removeConv = async (c: Conversation) => {
    if (!window.confirm(`确定删除会话「${c.title}」？（消息一并删除）`)) return
    try {
      await deleteConversation(token, c.id)
      void loadConvs()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '删除失败')
    }
  }

  const newFolder = async () => {
    const name = window.prompt('文件夹名称', '')
    if (!name?.trim()) return
    try {
      await createFolder(token, name.trim())
      void loadFavs()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '新建文件夹失败')
    }
  }

  const removeFolder = async (f: FavoriteFolder) => {
    if (!window.confirm(`删除文件夹「${f.name}」？（收藏保留为未分类）`)) return
    try {
      await deleteFolder(token, f.id)
      setFolderFilter(null)
      void loadFavs()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '删除文件夹失败')
    }
  }

  const moveFav = async (f: Favorite, folderId: number | null) => {
    try {
      await patchFavorite(token, f.id, folderId)
      void loadFavs()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '移动失败')
    }
  }

  const removeFav = async (f: Favorite) => {
    if (!window.confirm('删除这条收藏？')) return
    try {
      await deleteFavorite(token, f.id)
      void loadFavs()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '删除失败')
    }
  }

  return (
    <div className="page work-page">
      <header className="page-header">
        <span>🗂️ 工作台</span>
        <div className="tabs-inline">
          <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>
            历史记录
          </button>
          <button className={tab === 'favorites' ? 'active' : ''} onClick={() => setTab('favorites')}>
            收藏夹
          </button>
        </div>
      </header>

      <div className="scroll-area">
        {tab === 'history' ? (
          <>
            <div className="filter-row">
              <input placeholder="搜索历史…" value={q} onChange={(e) => setQ(e.target.value)} />
              <select value={tag} onChange={(e) => setTag(e.target.value)}>
                <option value="">全部标签</option>
                {TAGS.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <label className="chip">
                <input type="checkbox" checked={onlyFav} onChange={(e) => setOnlyFav(e.target.checked)} />
                仅收藏
              </label>
            </div>
            <p className="muted">共 {total} 个会话，点击「打开」可在首页续聊</p>
            <div className="conv-list">
              {convs.map((c) => (
                <div className="conv-card" key={c.id}>
                  <div className="conv-main" onClick={() => void openConversation(c)}>
                    <div className="conv-title">
                      {c.is_favorite === 1 ? '⭐ ' : ''}
                      {c.title}
                    </div>
                    <div className="muted conv-sub">
                      {c.msg_count} 条消息 · {c.updated_at}
                      {c.last_answer ? ` · ${c.last_answer.slice(0, 24)}${c.last_answer.length > 24 ? '…' : ''}` : ''}
                    </div>
                  </div>
                  <div className="conv-actions">
                    <button className="icon-btn" onClick={() => void openConversation(c)} title="打开">
                      打开
                    </button>
                    <button className="icon-btn" onClick={() => void rename(c)} title="重命名">
                      改名
                    </button>
                    <button className="icon-btn" onClick={() => void toggleFav(c)} title={c.is_favorite ? '取消收藏' : '收藏'}>
                      {c.is_favorite === 1 ? '⭐' : '☆'}
                    </button>
                    <button className="icon-btn danger" onClick={() => void removeConv(c)} title="删除">
                      ✕
                    </button>
                  </div>
                </div>
              ))}
              {convs.length === 0 && <p className="muted">暂无会话，去首页提问吧</p>}
            </div>
          </>
        ) : (
          <>
            <div className="filter-row">
              <select
                value={folderFilter ?? ''}
                onChange={(e) => setFolderFilter(e.target.value === '' ? null : Number(e.target.value))}
              >
                <option value="">全部收藏</option>
                <option value="0">未分类</option>
                {folders.map((f) => (
                  <option key={f.id} value={f.id}>{f.name}（{f.fav_count}）</option>
                ))}
              </select>
              <button className="chip" onClick={() => void newFolder()}>＋ 新建文件夹</button>
            </div>
            <p className="muted">共 {favTotal} 条收藏</p>
            <div className="fav-list">
              {favs.map((f) => (
                <div className="conv-card fav-card" key={f.id}>
                  <div className="conv-main">
                    <div className="fav-q">Q：{f.question}</div>
                    <div className="fav-a">{f.answer.slice(0, 120)}{f.answer.length > 120 ? '…' : ''}</div>
                  </div>
                  <div className="conv-actions">
                    <select
                      value={f.folder_id ?? 0}
                      onChange={(e) => void moveFav(f, Number(e.target.value) === 0 ? null : Number(e.target.value))}
                      title="移动到文件夹"
                    >
                      <option value={0}>未分类</option>
                      {folders.map((fd) => (
                        <option key={fd.id} value={fd.id}>{fd.name}</option>
                      ))}
                    </select>
                    <button className="icon-btn danger" onClick={() => void removeFav(f)} title="删除收藏">
                      ✕
                    </button>
                  </div>
                </div>
              ))}
              {favs.length === 0 && <p className="muted">暂无收藏（在首页回答下方点「⭐ 收藏」）</p>}
            </div>
            {folders.length > 0 && (
              <div className="folder-row">
                {folders.map((f) => (
                  <button key={f.id} className="chip" onClick={() => void removeFolder(f)} title="删除文件夹">
                    📁 {f.name} ✕
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  )
}
