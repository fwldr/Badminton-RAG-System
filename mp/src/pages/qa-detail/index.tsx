/** 问答分享详情：从聊天气泡「分享」进入；转发朋友（onShareAppMessage）+ 保存图片海报（canvas）。 */

import { useState } from 'react'
import Taro, { useRouter, useShareAppMessage } from '@tarojs/taro'
import { Canvas, Text, View } from '@tarojs/components'
import { useUserStore } from '../../store/user'
import './index.scss'

interface QaParams {
  q: string
  a: string
  sources?: string // JSON [{table,brand,model}]
}

export default function QaDetailPage() {
  const router = useRouter()
  const user = useUserStore((s) => s.user)
  const [saved, setSaved] = useState(false)

  const params: QaParams = {
    q: decodeURIComponent(router.params.q || '羽毛球知识问答'),
    a: decodeURIComponent(router.params.a || ''),
    sources: router.params.sources ? decodeURIComponent(router.params.sources) : undefined,
  }
  const sources = params.sources ? (JSON.parse(params.sources) as { table: string; brand: string; model: string }[]) : []

  useShareAppMessage(() => ({
    title: `羽问｜${params.q}`,
    path: `/pages/qa-detail/index?q=${encodeURIComponent(params.q)}&a=${encodeURIComponent(params.a)}`,
  }))

  // ---- canvas 海报（旧 API：createCanvasContext + canvasToTempFilePath） ----

  const wrapLine = (text: string, per: number, max: number): string[] => {
    const lines: string[] = []
    for (let i = 0; i < text.length && lines.length < max; i += per) {
      lines.push(text.slice(i, i + per))
    }
    return lines
  }

  const drawPoster = (): Promise<string> => {
    return new Promise((resolve) => {
      const ctx = Taro.createCanvasContext('poster')
      const W = 340
      let y = 0
      // 头部品牌
      ctx.setFillStyle('#064e3b')
      ctx.fillRect(0, 0, W, 90)
      ctx.setFillStyle('#ffffff')
      ctx.setFontSize(26)
      ctx.fillText('羽问 · 羽毛球知识问答', 20, 52)
      y = 90
      // 问题
      ctx.setFillStyle('#122019')
      ctx.setFontSize(20)
      y += 40
      for (const line of wrapLine(params.q, 16, 2)) {
        ctx.font = 'bold 20px sans-serif'
        ctx.fillText(line, 20, y)
        y += 30
      }
      y += 10
      // 回答
      ctx.setFillStyle('#44554b')
      for (const line of wrapLine(params.a, 18, 8)) {
        ctx.fillText(line, 20, y)
        y += 28
      }
      y += 10
      // 来源
      if (sources.length > 0) {
        ctx.setFillStyle('#047857')
        ctx.fillText(`引用来源 ${sources.length} 条`, 20, y)
        y += 24
      }
      // 页脚
      ctx.setFillStyle('#7c8b82')
      ctx.setFontSize(12)
      ctx.fillText('仅依据专业资料回答 · 内容仅供学习参考', 20, Math.min(y, 320) + 18)

      ctx.draw(false, () => {
        Taro.canvasToTempFilePath({
          canvasId: 'poster',
          success: (res) => resolve(res.tempFilePath),
          fail: () => resolve(''),
        })
      })
    })
  }

  const savePoster = async () => {
    if (saved) return
    try {
      const filePath = await drawPoster()
      if (!filePath) throw new Error('no canvas')
      await Taro.saveImageToPhotosAlbum({
        filePath,
        async success() {
          setSaved(true)
          Taro.showToast({ title: '✅ 海报已保存到相册', icon: 'none' })
        },
        fail(err) {
          if (String(err.errMsg || '').includes('auth cancel')) return
          Taro.showToast({ title: '保存失败，请授权相册权限', icon: 'none' })
        },
      })
    } catch {
      Taro.showToast({ title: '海报生成失败', icon: 'none' })
    }
  }

  const shareToFriend = () => {
    Taro.showToast({ title: '点击右上角「转发」分享', icon: 'none' })
  }

  return (
    <View className="page">
      <View className="ans">
        <View className="q">{params.q}</View>
        <View className="meta">
          <Text className="badge">问答分享</Text>
          <Text>{user?.nickname || '羽问用户'} · 已认证知识库</Text>
        </View>
        <View className="txt">{params.a || '（暂无内容）'}</View>
        {sources.length > 0 && (
          <>
            <View className="src-hd">📎 引用来源（{sources.length} 条）</View>
            <View className="src-tbl">
              <View className="row hd"><Text>表</Text><Text>品牌</Text><Text>型号</Text></View>
              {sources.map((s, i) => (
                <View key={i} className="row">
                  <Text>{s.table}</Text><Text>{s.brand}</Text><Text>{s.model}</Text>
                </View>
              ))}
            </View>
          </>
        )}
      </View>

      <View className="actions">
        <View className="btn primary" onClick={shareToFriend}>↗ 转发朋友</View>
        <View className="btn ghost" onClick={() => void savePoster()}>🖼️ 保存图片</View>
      </View>
      <View className="note">{saved ? '海报已保存' : '生成分享海报 · 内容以转发时为准'}</View>

      {/* 离屏画布 */}
      <Canvas canvasId="poster" className="poster-canvas" />
    </View>
  )
}
