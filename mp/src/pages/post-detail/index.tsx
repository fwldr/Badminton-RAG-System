/** 动态详情：点击发现页动态进入 —— 只读展示动态正文与图片（无回复、无点赞）。 */

import { useCallback, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { Image, ScrollView, Text, View } from '@tarojs/components'
import { getPostDetail, type PostItem } from '../../api/user'
import { assetUrl, isImageSrc } from '../../api/request'
import './index.scss'

function fmtTime(s: string): string {
  return (s || '').replace('T', ' ').slice(0, 16)
}

export default function PostDetailPage() {
  const [post, setPost] = useState<PostItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const postId = Number(Taro.getCurrentInstance().router?.params?.id || 0)

  const load = useCallback(async () => {
    if (!postId) {
      setError('参数错误')
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const d = await getPostDetail(postId)
      setPost(d.post)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [postId])

  useEffect(() => {
    void load()
    Taro.setNavigationBarTitle({ title: '动态详情' })
  }, [load])

  if (!post && loading) {
    return <View className="page"><View className="state">加载中…</View></View>
  }
  if (!post) {
    return (
      <View className="page">
        <View className="state">{error || '动态不存在或已删除'}</View>
      </View>
    )
  }

  return (
    <View className="page">
      <ScrollView scrollY className="body" enableBackToTop>
        {/* 动态卡片（只读） */}
        <View className="card">
          <View className="hd">
            {isImageSrc(post.author_avatar) ? (
              <Image className="avatar-img" src={assetUrl(post.author_avatar)} mode="aspectFill" />
            ) : (
              <View className="avatar">{post.author_avatar || '🧑'}</View>
            )}
            <View className="who">
              <Text className="nn">{post.author_nickname}</Text>
              <Text className="tm">{fmtTime(post.created_at)}</Text>
            </View>
          </View>
          <Text className="tx">{post.content}</Text>
          {post.images.length > 0 && (
            <View className="imgs">
              {post.images.map((im) => (
                <View
                  key={im}
                  className="im"
                  onClick={() => Taro.previewImage({ urls: post.images.map((x) => assetUrl(x)), current: assetUrl(im) })}
                >
                  <Image src={assetUrl(im)} mode="aspectFill" lazyLoad />
                </View>
              ))}
            </View>
          )}
        </View>
        <View className="pad" />
      </ScrollView>
    </View>
  )
}
