/** 提问首页（W2 完整版）：问候态 / 预设卡 / 范围限定 / 气泡全状态（Markdown·缓存·失败重试）/
 *  引用来源面板（遵循 pref_show_sources）/ 图片内联与预览 / 赞踩反馈。
 *  对应设计方案 §4.2 与原型 chat.html。 */

import { useCallback, useEffect, useRef, useState } from 'react'
import Taro, { useDidShow } from '@tarojs/taro'
import { Image, Input, Picker, ScrollView, Text, View } from '@tarojs/components'
import { chat, sendFeedback, type ChatMessageData, type ChatResponse } from '../../api/chat'
import { assetUrl, isImageSrc } from '../../api/request'
import { createFavorite, getConversation } from '../../api/user'
import Md from '../../components/md'
import { useUserStore } from '../../store/user'
import './index.scss'

const PRESETS = ['最新双打发球规则', '反手高远球动作要领', '4U 和 5U 球拍有什么区别？']
const SCOPES = [
  { key: 'all', label: '🌐 全部' },
  { key: 'rules', label: '📜 仅规则' },
  { key: 'technique', label: '🏸 仅技术' },
  { key: 'equipment', label: '🎾 仅装备' },
  { key: 'document', label: '📄 仅文档' },
]

function newSessionId(): string {
  return `mp-${Date.now()}-${Math.floor(Math.random() * 1e6)}`
}

export default function ChatPage() {
  const user = useUserStore((s) => s.user)
  const [messages, setMessages] = useState<ChatMessageData[]>([])
  const [input, setInput] = useState('')
  const [scope, setScope] = useState(0)
  const [loading, setLoading] = useState(false)
  const [toast, setToast] = useState('')
  const [openSources, setOpenSources] = useState<Record<number, boolean>>({})
  const sidRef = useRef<string>('')
  const [scrollTop, setScrollTop] = useState(0)

  // 偏好：显示引用来源（关闭时隐藏面板）
  const showSourcesPref = (user?.pref_show_sources ?? 1) === 1

  useEffect(() => {
    if (!user) {
      Taro.reLaunch({ url: '/pages/login/index' })
      return
    }
    if (!sidRef.current) {
      sidRef.current = Taro.getStorageSync('mp_session_id') || newSessionId()
      Taro.setStorageSync('mp_session_id', sidRef.current)
    }
  }, [user])

  // 发现页「一键提问」/ 工作台「会话详情→继续提问」：读取预填问题或激活会话
  useDidShow(async () => {
    try {
      // 1) 激活会话（续聊）：恢复 session_id + 回放历史消息
      const rawConv = Taro.getStorageSync('mp_active_conv')
      if (rawConv) {
        Taro.removeStorageSync('mp_active_conv')
        const c = JSON.parse(rawConv) as { convId?: number; sessionId?: string }
        if (c.sessionId) sidRef.current = c.sessionId
        if (c.convId) {
          const detail = await getConversation(c.convId)
          setMessages(
            (detail.messages || []).map((m) => ({
              role: m.role === 'user' ? 'user' : 'assistant',
              content: m.content,
              sources: m.sources || [],
              cached: m.cached === 1,
            })),
          )
          scrollBottom()
        }
        return
      }
      // 2) 预填问题（发现页热门一键提问）
      const prefill = Taro.getStorageSync('mp_prefill')
      if (prefill) {
        Taro.removeStorageSync('mp_prefill')
        void handleSend(String(prefill))
      }
    } catch {
      /* 忽略 */
    }
  })

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(''), 3000)
  }, [])

  const scrollBottom = () => {
    // 微信小程序中 scroll-view 没有 scrollIntoView 方法（它是 scroll-view 的属性），
    // 正确做法：等新消息渲染后，用递增的 scrollTop 触发滚动到底部
    setTimeout(() => setScrollTop((v) => v + 100000), 80)
  }

  const handleSend = async (raw?: string) => {
    const question = (raw ?? input).trim()
    if (!question || loading) return
    if (!sidRef.current) sidRef.current = newSessionId()
    const useScope = SCOPES[scope].key === 'all' ? null : SCOPES[scope].key

    setMessages((prev) => [...prev, { role: 'user', content: question }])
    setInput('')
    setLoading(true)
    scrollBottom()
    try {
      const resp: ChatResponse = await chat(sidRef.current, question, useScope)
      setMessages((prev) => {
        const next = [
          ...prev,
          {
            role: 'assistant' as const,
            content: resp.answer,
            question,
            sources: resp.sources,
            images: resp.images || [],
            cached: resp.cached,
          },
        ]
        return next
      })
      if (resp.cached) showToast('⚡ 命中缓存，秒回')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '网络错误'
      setMessages((prev) => [...prev, { role: 'assistant', content: `（请求失败：${msg}）`, question, failed: true }])
    } finally {
      setLoading(false)
      scrollBottom()
    }
  }

  const retry = (m: ChatMessageData) => {
    if (m.question) void handleSend(m.question)
  }

  const toggleSource = (idx: number) => {
    setOpenSources((prev) => ({ ...prev, [idx]: !prev[idx] }))
  }

  const copyAnswer = (m: ChatMessageData) => {
    Taro.setClipboardData({ data: m.content }).catch(() => undefined)
    showToast('已复制')
  }

  const previewImage = (urls: string[], current: string) => {
    Taro.previewImage({ urls, current }).catch(() => undefined)
  }

  const rate = (m: ChatMessageData, rating: 1 | -1) => {
    sendFeedback({
      session_id: sidRef.current,
      question: m.question || '',
      answer: m.content,
      rating,
    }).catch(() => {
      showToast('反馈提交失败')
    })
    showToast(rating === 1 ? '感谢反馈 👍' : '感谢反馈，已记录')
  }

  const favorite = async (m: ChatMessageData) => {
    try {
      await createFavorite({
        question: m.question || m.content.slice(0, 30),
        answer: m.content,
        sources: m.sources || [],
      })
      showToast('⭐ 已收藏到工作台')
    } catch {
      showToast('收藏失败')
    }
  }

  const shareToDetail = (m: ChatMessageData) => {
    const q = m.question || '羽毛球知识问答'
    Taro.navigateTo({
      url:
        `/pages/qa-detail/index?q=${encodeURIComponent(q)}` +
        `&a=${encodeURIComponent(m.content)}` +
        `&sources=${encodeURIComponent(JSON.stringify(m.sources || []))}`,
    })
  }

  return (
    <View className="page">
      {/* 问候态 */}
      {messages.length === 0 && (
        <View className="welcome">
          <View className="hero-avatar">🏸</View>
          <View className="hi">你好，{user?.nickname || '球友'}！<View className="dot" /></View>
          <View className="sub">
            我是羽问～可以问我规则、技术、装备等问题（支持多轮对话）。
            {'\n'}下方预设卡片可一键提问。
          </View>
        </View>
      )}

      <ScrollView scrollX enableFlex>
        <View className="preset-strip">
          {PRESETS.map((p) => (
            <View key={p} className={`chip${p === PRESETS[0] ? ' active' : ''}`} onClick={() => void handleSend(p)}>
              {p}
            </View>
          ))}
        </View>
      </ScrollView>
      <View className="cat-row">
        <Picker
          mode="selector"
          range={SCOPES.map((s) => s.label)}
          onChange={(e) => setScope(Number(e.detail.value))}
        >
          <View className="chip amber scope-pill">{SCOPES[scope].label} ▾</View>
        </Picker>
      </View>

      {(messages.length > 0 || loading) && (
        <ScrollView scrollY scrollWithAnimation className="messages-fill" scrollTop={scrollTop}>
        <View className="msg-list">
        {messages.map((m, i) => {
          if (m.role === 'user') {
            return (
              <View key={i} className="msg user">
              <View className="avatar me">
                {isImageSrc(user?.avatar) ? (
                  <Image
                    className="img"
                    src={assetUrl(user!.avatar)}
                    mode="aspectFill"
                    style={{ width: '68rpx', height: '68rpx', borderRadius: '50%' }}
                  />
                ) : (
                  <Text>{user?.avatar || '🧑'}</Text>
                )}
              </View>
                <View className="bubble">{m.content}</View>
              </View>
            )
          }
          const imgs = m.images || []
          const showSources = showSourcesPref && m.sources && m.sources.length > 0
          return (
            <View key={i} className="msg assistant">
              <View className="avatar">🏸</View>
              <View className="bubble">
                {m.cached && <Text className="cached-badge">⚡ 秒回（缓存命中）</Text>}
                <Md text={m.content} />

                {/* 图片文档：内联展示 + 点击预览 */}
                {imgs.length > 0 && (
                  <View className="msg-images">
                    {imgs.map((im) => (
                      <View key={im.url} className="im" onClick={() => previewImage(imgs.map((x) => assetUrl(x.url)), assetUrl(im.url))}>
                        <Image src={assetUrl(im.url)} mode="widthFix" lazyLoad />
                        {im.title && <View className="cap">{im.title}</View>}
                      </View>
                    ))}
                  </View>
                )}

                {/* 引用来源（偏好关闭时整体隐藏） */}
                {showSources && (
                  <>
                    <View className="src-hd" onClick={() => toggleSource(i)}>
                      <Text>📎 引用来源（{m.sources!.length} 条）</Text>
                      <Text className="toggle">{openSources[i] ? '▴ 收起' : '▾ 展开'}</Text>
                    </View>
                    {openSources[i] && (
                      <View className="src-tbl">
                        <View className="row hd">
                          <Text>表</Text><Text>品牌</Text><Text>型号</Text>
                        </View>
                        {m.sources!.map((s, j) => (
                          <View key={j} className="row">
                            <Text>{s.table}</Text><Text>{s.brand}</Text><Text>{s.model}</Text>
                          </View>
                        ))}
                      </View>
                    )}
                  </>
                )}

                {/* 失败重试 */}
                {m.failed && (
                  <View className="fail-row">
                    <Text className="fail-tip">请求失败</Text>
                    <Text className="retry-btn" onClick={() => retry(m)}>重试</Text>
                  </View>
                )}

                <View className="act-row">
                  <Text className="act" onClick={() => void favorite(m)}>⭐ 收藏</Text>
                  <Text className="act" onClick={() => shareToDetail(m)}>↗ 分享</Text>
                  <Text className="act" onClick={() => copyAnswer(m)}>📋 复制</Text>
                  <Text className="act" onClick={() => rate(m, 1)}>👍</Text>
                  <Text className="act" onClick={() => rate(m, -1)}>👎</Text>
                </View>
              </View>
            </View>
          )
        })}
        {loading && (
          <View className="msg assistant">
            <View className="avatar">🏸</View>
            <View className="bubble">
              <View className="typing"><View className="dot" /><View className="dot" /><View className="dot" /></View>
            </View>
          </View>
        )}
        </View>
        </ScrollView>
      )}

      <View className="composer">
        <Input
          className="input"
          value={input}
          placeholder="输入你的羽毛球问题，回车发送"
          placeholderStyle="color:#a3b0a8"
          confirmType="send"
          onInput={(e) => setInput(e.detail.value)}
          onConfirm={() => void handleSend()}
        />
        <View className={`send${loading || !input.trim() ? ' disabled' : ''}`} onClick={() => void handleSend()}>
          发送
        </View>
      </View>

      {toast && <View className="toast">{toast}</View>}
    </View>
  )
}
