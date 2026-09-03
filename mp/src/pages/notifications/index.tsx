/** 消息通知：列表 + 全部已读。 */

import { useCallback, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { Text, View } from '@tarojs/components'
import { listNotifications, markNotificationsRead, type NotificationItem } from '../../api/user'
import { useUserStore } from '../../store/user'
import './index.scss'

function icon(type: string): string {
  if (type === 'correction') return '✏️'
  if (type === 'system') return '🔔'
  return '💬'
}

export default function NotificationsPage() {
  const user = useUserStore((s) => s.user)
  const [items, setItems] = useState<NotificationItem[]>([])
  const [unread, setUnread] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await listNotifications()
      setItems(r.notifications || [])
      setUnread(r.unread || 0)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!user) {
      Taro.reLaunch({ url: '/pages/login/index' })
      return
    }
    void load()
  }, [user, load])

  const readAll = async () => {
    await markNotificationsRead()
    await load()
    Taro.showToast({ title: '已全部标记已读', icon: 'success' })
  }

  return (
    <View className="page">
      {unread > 0 && (
        <View className="ops">
          <View className="op" onClick={() => void readAll()}>全部已读（{unread} 未读）</View>
        </View>
      )}
      {items.map((n) => (
        <View key={n.id} className={`notif${n.is_read === 0 ? ' unread' : ''}`}>
          <View className="ic">{icon(n.type)}</View>
          <View className="body">
            <View className="tt">
              <Text>{n.title}</Text>
              {n.is_read === 0 && <View className="un" />}
            </View>
            <Text className="ct">{n.content}</Text>
            <Text className="tm">{n.created_at}</Text>
          </View>
        </View>
      ))}
      {loading && <View className="state">加载中…</View>}
      {!loading && error && (
        <View className="state" onClick={() => void load()}>{error}（点击重试）</View>
      )}
      {!loading && !error && items.length === 0 && <View className="state">暂无通知</View>}
    </View>
  )
}
