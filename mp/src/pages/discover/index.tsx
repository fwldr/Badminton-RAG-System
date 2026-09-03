/** 发现页：本周热门问答 / 球友动态流（纯展示，点击进详情）+ 发布入口。 */

import { useCallback, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { Image, Text, View } from '@tarojs/components'
import { getHot, listPosts, type HotItem, type PostItem } from '../../api/user'
import { assetUrl, isImageSrc } from '../../api/request'
import './index.scss'

export default function DiscoverPage() {
  const [hot, setHot] = useState<HotItem[]>([])
  const [posts, setPosts] = useState<PostItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [h, p] = await Promise.all([getHot(), listPosts({ limit: 20 })])
      setHot(h.hot || [])
      setPosts(p.posts || [])
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

      <View className="sec-hd">
        <Text className="t">🏸 球友动态</Text>
        <Text className="more post-add" onClick={() => Taro.navigateTo({ url: '/pages/post-create/index' })}>＋ 发布</Text>
      </View>
      {posts.map((p) => (
        <View key={p.id} className="post">
          <View className="post-main" onClick={() => Taro.navigateTo({ url: `/pages/post-detail/index?id=${p.id}` })}>
            <View className="hd">
              {isImageSrc(p.author_avatar) ? (
                <Image className="avatar-img" src={assetUrl(p.author_avatar)} mode="aspectFill" />
              ) : (
                <View className="avatar">{p.author_avatar || '🧑'}</View>
              )}
              <View className="who">
                <Text className="nn">{p.author_nickname}</Text>
                <Text className="tm">{p.created_at.replace('T', ' ')}</Text>
              </View>
            </View>
            <Text className="tx">{p.content}</Text>
            {p.images.length > 0 && (
              <View className="imgs">
                {p.images.map((im) => (
                  <View
                    key={im}
                    className="im"
                    onClick={(e: { stopPropagation: () => void }) => {
                      e.stopPropagation()
                      Taro.previewImage({ urls: p.images.map((x) => assetUrl(x)), current: assetUrl(im) })
                    }}
                  >
                    <Image src={assetUrl(im)} mode="aspectFill" lazyLoad />
                  </View>
                ))}
              </View>
            )}
          </View>
        </View>
      ))}
      {!loading && posts.length === 0 && <View className="state">还没有球友动态，来发第一条吧！</View>}

      {loading && <View className="state">加载中…</View>}
      {!loading && error && (
        <View className="state" onClick={() => void load()}>{error}（点击重试）</View>
      )}
    </View>
  )
}
