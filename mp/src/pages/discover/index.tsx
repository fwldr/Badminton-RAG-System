/** 发现页：本周热门问答（纯展示，点击填入提问框）。
 *  注意：个人主体小程序不提供 UGC 社交功能（球友动态/发布入口已按合规要求移除）。 */

import { useCallback, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { Text, View } from '@tarojs/components'
import { getHot, type HotItem } from '../../api/user'
import './index.scss'

export default function DiscoverPage() {
  const [hot, setHot] = useState<HotItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const h = await getHot()
      setHot(h.hot || [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const ask = (q: string) => {
    Taro.setStorageSync('mp_prefill', q)
    Taro.switchTab({ url: '/pages/chat/index' })
  }

  return (
    <View className="page">
      <View className="sec-hd">
        <Text className="t">🔥 本周热门问答</Text>
      </View>
      {hot.map((h, i) => (
        <View key={`${h.question}-${i}`} className="hot" onClick={() => ask(h.question)}>
          <Text className={`rk${i < 3 ? ' top' : ''}`}>{i + 1}</Text>
          <Text className="q">{h.question}</Text>
          <Text className="n">{h.score} 赞</Text>
        </View>
      ))}

      {loading && <View className="state">加载中…</View>}
      {!loading && hot.length === 0 && !error && <View className="state">暂无热门问答</View>}
      {!loading && error && (
        <View className="state" onClick={() => void load()}>{error}（点击重试）</View>
      )}
    </View>
  )
}
