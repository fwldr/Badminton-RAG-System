/** 会话详情：历史消息回放 + 「继续提问」回到提问页续聊。 */

import { useCallback, useEffect, useState } from 'react'
import Taro, { useRouter } from '@tarojs/taro'
import { Text, View } from '@tarojs/components'
import { getConversation, type ConvMessage } from '../../api/user'
import { useUserStore } from '../../store/user'
import './index.scss'

export default function ConvDetailPage() {
  const router = useRouter()
  const user = useUserStore((s) => s.user)
  const [messages, setMessages] = useState<ConvMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const convId = Number(router.params.id || 0)
    if (!convId) {
      setError('参数错误')
      setLoading(false)
      return
    }
    try {
      const detail = await getConversation(convId)
      setMessages(detail.messages || [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [router.params.id])

  useEffect(() => {
    if (!user) {
      Taro.reLaunch({ url: '/pages/login/index' })
      return
    }
    void load()
  }, [user, load])

  const continueAsk = () => {
    // 当前会话信息已在 storage（mp_active_conv），切换 Tab 后由提问页接管续聊
    Taro.switchTab({ url: '/pages/chat/index' })
  }

  return (
    <View className="page">
      {messages.map((m) => (
        <View key={m.id}>
          {m.role === 'user' ? (
            <View className="msg user">
              <View className="avatar me">{user?.avatar || '🧑'}</View>
              <View className="bubble">{m.content}</View>
            </View>
          ) : (
            <View className="msg assistant">
              <View className="avatar">🏸</View>
              <View className="bubble">
                <Text>{m.content}</Text>
                {m.sources && m.sources.length > 0 && (
                  <View className="at">📎 引用来源 {m.sources.length} 条{m.cached === 1 ? ' · ⚡ 缓存' : ''}</View>
                )}
              </View>
            </View>
          )}
        </View>
      ))}

      {loading && <View className="state">加载中…</View>}
      {!loading && error && (
        <View className="state" onClick={() => void load()}>{error}（点击重试）</View>
      )}
      {!loading && !error && messages.length === 0 && <View className="state">该会话暂无消息</View>}

      {!loading && !error && messages.length > 0 && (
        <View className="cta" onClick={continueAsk}>继续提问 ⏎</View>
      )}
    </View>
  )
}
