/** 我的纠错：提交表单 + 记录列表；提交成功后请求订阅消息授权（审核结果推送）。 */

import { useCallback, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { Input, Text, Textarea, View } from '@tarojs/components'
import { listCorrections, submitCorrection, type CorrectionItem } from '../../api/user'
import { SUBSCRIBE_TEMPLATE_ID } from '../../config'
import { useUserStore } from '../../store/user'
import './index.scss'

const STATUS_TEXT: Record<string, { label: string; cls: string }> = {
  pending: { label: '待审核', cls: 'pending' },
  accepted: { label: '已采纳', cls: 'accepted' },
  rejected: { label: '已驳回', cls: 'rejected' },
  discussion: { label: '转讨论', cls: 'discussion' },
}

export default function CorrectionsPage() {
  const user = useUserStore((s) => s.user)
  const [docRef, setDocRef] = useState('')
  const [origText, setOrigText] = useState('')
  const [corrText, setCorrText] = useState('')
  const [reason, setReason] = useState('')
  const [items, setItems] = useState<CorrectionItem[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await listCorrections()
      setItems(r.corrections || [])
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

  const askSubscribe = async () => {
    if (!SUBSCRIBE_TEMPLATE_ID) return
    try {
      // Taro 4.1.8 对该 API 的类型定义有漂移（entityIds），运行参数以 tmplIds 为准
      await (Taro as unknown as { requestSubscribeMessage: (o: { tmplIds: string[] }) => Promise<unknown> })
        .requestSubscribeMessage({ tmplIds: [SUBSCRIBE_TEMPLATE_ID] })
    } catch {
      /* 用户拒绝/不支持：忽略 */
    }
  }

  const submit = async () => {
    if (!corrText.trim() || submitting) return
    setSubmitting(true)
    try {
      await submitCorrection({
        doc_ref: docRef.trim() || undefined,
        original_text: origText.trim() || undefined,
        corrected_text: corrText.trim(),
        reason: reason.trim() || undefined,
      })
      setDocRef(''); setOrigText(''); setCorrText(''); setReason('')
      Taro.showToast({ title: '纠错已提交，采纳后通知你', icon: 'success' })
      await askSubscribe()
      await load()
    } catch (e) {
      Taro.showToast({ title: e instanceof Error ? e.message : '提交失败', icon: 'none' })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <View className="page">
      <View className="card">
        <View className="field-label">参考片段（可选，如「反手技术 · 第 2 段」）</View>
        <Input className="input" value={docRef} placeholder="填写有误的引用片段" onInput={(e) => setDocRef(e.detail.value)} />
        <View className="field-label">原文（可选）</View>
        <Textarea className="textarea" value={origText} placeholder="描述你认为有误的内容" onInput={(e) => setOrigText(e.detail.value)} />
        <View className="field-label">纠正文 *</View>
        <Textarea className="textarea" value={corrText} placeholder="正确的说法/内容" onInput={(e) => setCorrText(e.detail.value)} />
        <View className="field-label">理由（可选）</View>
        <Input className="input" value={reason} placeholder="补充依据" onInput={(e) => setReason(e.detail.value)} />
        <View className="submit" style={{ marginTop: 28 }} onClick={() => void submit()}>
          {submitting ? '提交中…' : '提交纠错'}
        </View>
      </View>

      <View className="card">
        <View className="field-label">我的提交 · {items.length}</View>
        {items.map((c) => {
          const st = STATUS_TEXT[c.status] || STATUS_TEXT.pending
          return (
            <View key={c.id} className="corr">
              <View className="q">
                <Text style={{ flex: 1 }}>{c.doc_ref || c.corrected_text.slice(0, 12)}</Text>
                <Text className={`st ${st.cls}`}>{st.label}</Text>
              </View>
              <Text className="a">{c.corrected_text}</Text>
              {c.admin_reply && <Text className="tm">管理员：{c.admin_reply}</Text>}
              <Text className="tm">{c.created_at}</Text>
            </View>
          )
        })}
        {!loading && items.length === 0 && <View className="state">还没有提交过纠错</View>}
      </View>

      {loading && <View className="state">加载中…</View>}
      {!loading && error && (
        <View className="state" onClick={() => void load()}>{error}（点击重试）</View>
      )}
    </View>
  )
}
