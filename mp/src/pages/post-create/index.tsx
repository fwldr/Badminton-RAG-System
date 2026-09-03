/** 发布动态：文本（服务端 msgSecCheck 拦截违规）+ 图片 1~3 张（/user/uploads 直传）。 */

import { useState } from 'react'
import Taro from '@tarojs/taro'
import { Image, Text, Textarea, View } from '@tarojs/components'
import { createPost, uploadImage } from '../../api/user'
import { assetUrl } from '../../api/request'
import './index.scss'

const MAX_IMAGES = 3

export default function PostCreatePage() {
  const [content, setContent] = useState('')
  const [images, setImages] = useState<string[]>([]) // 本地上传后的 /uploads/xxx
  const [picking, setPicking] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const pickImages = async () => {
    if (images.length >= MAX_IMAGES) {
      Taro.showToast({ title: `最多 ${MAX_IMAGES} 张图片`, icon: 'none' })
      return
    }
    setPicking(true)
    try {
      const res = await Taro.chooseMedia({
        count: MAX_IMAGES - images.length,
        mediaType: ['image'],
        sizeType: ['compressed'],
      })
      const up: string[] = []
      for (const file of res.tempFiles) {
        const r = await uploadImage(file.tempFilePath)
        up.push(r.path)
      }
      setImages((prev) => [...prev, ...up])
    } catch (e) {
      Taro.showToast({ title: e instanceof Error ? e.message : '图片选择失败', icon: 'none' })
    } finally {
      setPicking(false)
    }
  }

  const removeImage = (path: string) => {
    setImages((prev) => prev.filter((x) => x !== path))
  }

  const submit = async () => {
    const text = content.trim()
    if (!text || submitting) return
    setSubmitting(true)
    try {
      await createPost({ content: text, images })
      Taro.showToast({ title: '发布成功', icon: 'success' })
      setTimeout(() => Taro.navigateBack(), 800)
    } catch (e) {
      Taro.showToast({ title: e instanceof Error ? e.message : '发布失败', icon: 'none' })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <View className="page">
      <View className="editor">
        <Textarea
          className="textarea"
          value={content}
          maxlength={1000}
          placeholder="分享你的羽球时刻…（发布前会自动检查内容安全）"
          placeholderStyle="color:#a3b0a8"
          onInput={(e) => setContent(e.detail.value)}
        />
        <View className="counter">{content.length}/1000</View>
        <View className="picks">
          {images.map((im) => (
            <View key={im} className="pick">
              <Image src={assetUrl(im)} mode="aspectFill" />
              <View className="rm" onClick={() => removeImage(im)}>✕</View>
            </View>
          ))}
          {images.length < MAX_IMAGES && (
            <View className="pick add" onClick={() => void pickImages()}>
              <View className="p">＋</View>
              <Text>{picking ? '上传中…' : '图片'}</Text>
            </View>
          )}
        </View>
      </View>

      <View className={`submit${submitting || !content.trim() ? ' disabled' : ''}`} onClick={() => void submit()}>
        {submitting ? '发布中…' : '发布动态'}
      </View>
      <View className="hint">内容经微信内容安全校验；违规内容将无法发布。</View>
    </View>
  )
}
