/** 工作台（W3 完整）：会话历史 + 收藏夹（文件夹管理/移动/删除、会话重命名/标签/收藏/删除）。 */

import { useCallback, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { ScrollView, Text, View } from '@tarojs/components'
import {
  createFolder,
  deleteConversation,
  deleteFavorite,
  deleteFolder,
  listConversations,
  listFavorites,
  listFolders,
  moveFavorite,
  patchConversation,
  type Conversation,
  type FavoriteFolder,
  type FavoriteItem,
} from '../../api/user'
import { useUserStore } from '../../store/user'
import './index.scss'

const TAGS = ['全部', '规则类', '技术类', '装备类', '⭐ 已收藏']

// Taro 4.1.8 类型定义落后于基础库（editable/content 缺失），用 any 桥接运行参数
const showModalRaw = Taro.showModal as unknown as (o: Record<string, unknown>) => Promise<any> | void

function promptModal(title: string, placeholderText: string): Promise<string | null> {
  return new Promise((resolve) => {
    showModalRaw({
      title,
      editable: true,
      placeholderText,
      success: (res: { confirm: boolean; content: string }) => resolve(res.confirm ? res.content || null : null),
      fail: () => resolve(null),
    })
  })
}

function confirmModal(title: string, content: string): Promise<boolean> {
  return new Promise((resolve) => {
    showModalRaw({
      title,
      content,
      confirmColor: '#dc2626',
      success: (res: { confirm: boolean }) => resolve(!!res.confirm),
      fail: () => resolve(false),
    })
  })
}

interface ActiveConv {
  convId: number
  sessionId: string
  title: string
}

export default function WorkbenchPage() {
  const user = useUserStore((s) => s.user)
  const [convs, setConvs] = useState<Conversation[]>([])
  const [folders, setFolders] = useState<FavoriteFolder[]>([])
  const [favs, setFavs] = useState<FavoriteItem[]>([])
  const [tab, setTab] = useState(0)
  const [folderFilter, setFolderFilter] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadConvs = useCallback(async () => {
    if (!user) return
    try {
      const wanted = TAGS[tab]
      const c = await listConversations({
        tag: wanted === '全部' || wanted === '⭐ 已收藏' ? undefined : wanted,
        favorite: wanted === '⭐ 已收藏',
      })
      setConvs(c.conversations || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    }
  }, [user, tab])

  const loadFavs = useCallback(async () => {
    if (!user) return
    try {
      const [f, fd] = await Promise.all([listFavorites({ folder_id: folderFilter ?? undefined }), listFolders()])
      setFavs(f.items || [])
      setFolders(fd.folders || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    }
  }, [user, folderFilter])

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError('')
    await Promise.all([loadConvs(), loadFavs()])
    setLoading(false)
  }, [loadConvs, loadFavs])

  useEffect(() => {
    if (!user) {
      Taro.reLaunch({ url: '/pages/login/index' })
      return
    }
    void loadAll()
  }, [user, loadAll])

  // ---------- 会话操作 ----------

  const openConversation = (c: Conversation) => {
    const active: ActiveConv = { convId: c.id, sessionId: c.session_id, title: c.title }
    Taro.setStorageSync('mp_active_conv', JSON.stringify(active))
    Taro.navigateTo({ url: '/pages/conv-detail/index' })
  }

  const renameConv = async (c: Conversation) => {
    const title = await promptModal('重命名会话', c.title)
    if (title == null) return
    await patchConversation(c.id, { title: title || undefined })
    void loadConvs()
  }

  const tagConv = async (c: Conversation) => {
    Taro.showActionSheet({
      itemList: ['规则类', '技术类', '装备类', '去掉标签'],
      success: async (res) => {
        const tag = ['规则类', '技术类', '装备类'][res.tapIndex]
        await patchConversation(c.id, { tag: tag || null })
        void loadConvs()
      },
    })
  }

  const favConv = async (c: Conversation) => {
    await patchConversation(c.id, { is_favorite: c.is_favorite !== 1 })
    void loadConvs()
  }

  const delConv = async (c: Conversation) => {
    const yes = await confirmModal('删除会话', `确定删除「${c.title}」？`)
    if (!yes) return
    await deleteConversation(c.id)
    void loadConvs()
  }

  // ---------- 收藏操作 ----------

  const addFolder = async () => {
    const name = await promptModal('新建收藏夹', '如：基础入门')
    if (!name) return
    await createFolder(name)
    void loadFavs()
  }

  const delFolder = async (f: FavoriteFolder, e: { stopPropagation?: () => void }) => {
    e.stopPropagation?.()
    const yes = await confirmModal('删除收藏夹', `删除「${f.name}」？收藏会保留为未分类。`)
    if (!yes) return
    await deleteFolder(f.id)
    if (folderFilter === f.id) setFolderFilter(null)
    void loadFavs()
  }

  const moveFav = (fv: FavoriteItem) => {
    const names = folders.map((f) => f.name)
    Taro.showActionSheet({
      itemList: [...names, '未分类'],
      success: async (res) => {
        const target = res.tapIndex < folders.length ? folders[res.tapIndex].id : null
        await moveFavorite(fv.id, target)
        void loadFavs()
      },
    })
  }

  const delFav = async (fv: FavoriteItem) => {
    const yes = await confirmModal('删除收藏', fv.question)
    if (!yes) return
    await deleteFavorite(fv.id)
    void loadFavs()
  }

  const folderLabel = (fv: FavoriteItem) =>
    folders.find((f) => f.id === fv.folder_id)?.name || '未分类'

  return (
    <View className="page">
      <ScrollView scrollX enableFlex>
        <View className="filter-strip" style={{ display: 'flex' }}>
          {TAGS.map((t, i) => (
            <View key={t} className={`chip${tab === i ? ' active' : ''}`} onClick={() => setTab(i)}>
              {t}
            </View>
          ))}
        </View>
      </ScrollView>

      {tab !== 4 && (
        <>
          <View className="sec-hd">
            <Text className="t">会话记录 · {convs.length}</Text>
          </View>
          {convs.map((c) => (
            <View key={c.id} className="conv" onClick={() => openConversation(c)}>
              <View className="thumb">🏸</View>
              <View className="body">
                <View className="tt">
                  <Text className="t">{c.title}</Text>
                  <time>{c.updated_at}</time>
                </View>
                <View className="prev">{c.message_count ? `共 ${c.message_count} 条消息` : '点击查看详情'}</View>
                <View className="tags">
                  {c.tag && <Text className="tag">{c.tag}</Text>}
                  {c.is_favorite === 1 && <Text className="tag">⭐ 收藏</Text>}
                  <View className="ops">
                    <Text className="op" onClick={(e) => { e.stopPropagation(); renameConv(c) }}>改名</Text>
                    <Text className="op" onClick={(e) => { e.stopPropagation(); tagConv(c) }}>标签</Text>
                    <Text className="op" onClick={(e) => { e.stopPropagation(); void favConv(c) }}>{c.is_favorite === 1 ? '取消⭐' : '⭐'}</Text>
                    <Text className="op" style={{ color: '#dc2626' }} onClick={(e) => { e.stopPropagation(); delConv(c) }}>删除</Text>
                  </View>
                </View>
              </View>
            </View>
          ))}
          {!loading && !error && convs.length === 0 && (
            <View className="state">暂无会话，去「提问」页开始第一段对话吧</View>
          )}
        </>
      )}

      {tab === 4 && (
        <>
          <View className="sec-hd">
            <Text className="t">📌 收藏夹</Text>
            <Text className="more" onClick={addFolder}>＋ 新建</Text>
          </View>
          <View>
            {folders.map((f) => (
              <View key={f.id} className="folder" onClick={() => setFolderFilter(folderFilter === f.id ? null : f.id)}>
                📁 {f.name}{typeof f.count === 'number' ? ` · ${f.count}` : ''}
                <Text className="x" onClick={(e) => void delFolder(f, e)}>✕</Text>
              </View>
            ))}
            {folders.length === 0 && <View className="state">暂无收藏夹</View>}
          </View>

          <View className="sec-hd">
            <Text className="t">⭐ 收藏列表 · {favs.length}</Text>
          </View>
          {favs.map((fv) => (
            <View key={fv.id} className="fav">
              <Text className="q">{fv.question}</Text>
              <Text className="a">{fv.answer}</Text>
              <View className="ft">
                <Text className="op on" onClick={() => moveFav(fv)}>📁 {folderLabel(fv)}</Text>
                <Text className="op" style={{ color: '#dc2626' }} onClick={() => delFav(fv)}>删除</Text>
              </View>
            </View>
          ))}
          {!loading && !error && favs.length === 0 && <View className="state">暂无收藏，可在问答气泡点 ⭐ 收藏</View>}
        </>
      )}

      {loading && <View className="state">加载中…</View>}
      {!loading && error && (
        <View className="state" onClick={() => void loadAll()}>{error}（点击重试）</View>
      )}
    </View>
  )
}
