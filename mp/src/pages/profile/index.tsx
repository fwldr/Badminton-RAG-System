/** 我的：点击用户卡弹出「编辑个人信息」（微信头像昵称填写能力 + 水平/性别/主力拍）+ 偏好 + 菜单。 */

import { useRef, useState } from 'react'
import Taro from '@tarojs/taro'
import { Button, Image, Input, Picker, Text, View } from '@tarojs/components'
import { updateProfile } from '../../api/auth'
import { API_BASE_URL } from '../../api/request'
import { uploadImage } from '../../api/user'
import { useUserStore } from '../../store/user'
import './index.scss'

const LEVELS = ['新手', '进阶', '专业']
const GENDERS = ['男', '女', '保密']

/** 头像渲染地址：/uploads/xxx → 完整 URL；http 直用；其余为 emoji（返回空表示 emoji 渲染） */
function avatarSrc(avatar?: string | null): string {
  if (!avatar) return ''
  if (avatar.startsWith('http')) return avatar
  if (avatar.startsWith('/')) return `${API_BASE_URL}${avatar}`
  return ''
}

export default function ProfilePage() {
  const user = useUserStore((s) => s.user)
  const logout = useUserStore((s) => s.logout)
  const setUser = useUserStore((s) => s.setUser)
  const [saving, setSaving] = useState(false)

  // 编辑面板状态
  const [editOpen, setEditOpen] = useState(false)
  const [edName, setEdName] = useState('')
  const [edAvatar, setEdAvatar] = useState('') // 服务端 /uploads/xxx 或 emoji
  const [edLevel, setEdLevel] = useState(0)
  const [edGender, setEdGender] = useState(0)
  const [edRacket, setEdRacket] = useState('')
  const [uploading, setUploading] = useState(false)

  if (!user) return <View className="page" />

  const prefStyle: 'simple' | 'detailed' = user.pref_style === 'simple' ? 'simple' : 'detailed'
  const showSources = (user.pref_show_sources ?? 1) === 1

  const patch = async (fields: { pref_style?: 'simple' | 'detailed'; pref_show_sources?: boolean }) => {
    if (saving) return
    setSaving(true)
    try {
      const updated = await updateProfile(fields)
      setUser(updated)
      Taro.showToast({ title: '已保存', icon: 'success' })
    } catch (e) {
      Taro.showToast({ title: e instanceof Error ? e.message : '保存失败', icon: 'none' })
    } finally {
      setSaving(false)
    }
  }

  const openEdit = () => {
    setEdName(user.nickname || user.username || '')
    setEdAvatar(user.avatar || '')
    setEdLevel(Math.max(0, LEVELS.indexOf(user.level || '新手')))
    setEdGender(Math.max(0, GENDERS.indexOf(user.gender || '保密')))
    setEdRacket(user.racket_model || '')
    setEditOpen(true)
  }

  /** chooseAvatar：微信头像授权选择 → 上传 → 得到 /uploads/xxx
   *  本环境 wx.chooseAvatar 未暴露，用 Button openType 原生能力（官方推荐）；
   *  点击立即锁定（防双触发 "another chooseAvatar is in progress"），取消择中 30s 后自动解锁。 */
  const [picking, setPicking] = useState(false)
  const pickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const guardPick = () => {
    if (picking || uploading) return
    setPicking(true)
    if (pickTimer.current) clearTimeout(pickTimer.current)
    pickTimer.current = setTimeout(() => setPicking(false), 30000)
  }
  const onAvatarChosen = async (tmp?: string) => {
    if (pickTimer.current) clearTimeout(pickTimer.current)
    if (!tmp) {
      setPicking(false) // 取消/空结果 → 解锁
      return
    }
    setUploading(true)
    try {
      const r = await uploadImage(tmp)
      setEdAvatar(r.path)
    } catch (e) {
      Taro.showToast({ title: `头像上传失败：${e instanceof Error ? e.message : '未知错误'}`, icon: 'none' })
    } finally {
      setPicking(false)
      setUploading(false)
    }
  }

  const saveEdit = async () => {
    if (saving) return
    setSaving(true)
    try {
      const updated = await updateProfile({
        nickname: edName.trim() || undefined,
        avatar: edAvatar || undefined,
        level: LEVELS[edLevel] as '新手' | '进阶' | '专业',
        gender: GENDERS[edGender] as '男' | '女' | '保密',
        racket_model: edRacket.trim() || undefined,
      })
      setUser(updated)
      setEditOpen(false)
      Taro.showToast({ title: '个人信息已更新', icon: 'success' })
    } catch (e) {
      Taro.showToast({ title: e instanceof Error ? e.message : '保存失败', icon: 'none' })
    } finally {
      setSaving(false)
    }
  }

  const menu = [
    { ic: '🔔', t: '消息通知', tip: '', url: '/pages/notifications/index' },
    { ic: '✏️', t: '我的纠错', tip: '', url: '/pages/corrections/index' },
    { ic: '⚙️', t: '设置', tip: '', url: '/pages/settings/index' },
  ]

  const userAvatar = avatarSrc(user.avatar)

  return (
    <View className="page">
      <View className="head">
        <View className="me" onClick={openEdit}>
          {userAvatar ? (
            <Image className="avatar-img" src={userAvatar} mode="aspectFill" />
          ) : (
            <View className="avatar">{user.avatar || '🧑'}</View>
          )}
          <View className="info">
            <View className="n">{user.nickname || user.username}<Text className="lv">{user.level || '新手'}</Text></View>
            <View className="sub">{user.racket_model ? `主力拍：${user.racket_model}` : '还没设置主力拍'} · 点击编辑 ›</View>
          </View>
        </View>
        <View className="stats">
          <View className="s"><Text className="b">—</Text><Text>收藏</Text></View>
          <View className="s"><Text className="b">—</Text><Text>纠错</Text></View>
        </View>
      </View>

      <View className="body">
        <View className="card">
          <View className="row">
            <View className="lbl">
              <Text className="t">回答语气</Text>
              <Text className="s">详细模式含更多背景与对比</Text>
            </View>
            <View className="seg">
              <View className={`o${prefStyle === 'simple' ? ' on' : ''}`} onClick={() => void patch({ pref_style: 'simple' })}>简洁</View>
              <View className={`o${prefStyle === 'detailed' ? ' on' : ''}`} onClick={() => void patch({ pref_style: 'detailed' })}>详细</View>
            </View>
          </View>
          <View className="row">
            <View className="lbl">
              <Text className="t">显示引用来源</Text>
              <Text className="s">气泡下方展开「引用来源」面板</Text>
            </View>
            <View className={`switch${showSources ? ' on' : ''}`} onClick={() => void patch({ pref_show_sources: !showSources })} />
          </View>
        </View>

        <View className="card">
          {menu.map((m) => (
            <View
              key={m.t}
              className="item"
              onClick={() => {
                if (m.url) Taro.navigateTo({ url: m.url })
                else Taro.showToast({ title: m.tip || '开发中', icon: 'none' })
              }}
            >
              <View className="ic">{m.ic}</View>
              <Text className="t">{m.t}</Text>
              <Text className="arrow">›</Text>
            </View>
          ))}
          <View className="item" onClick={() => logout()}>
            <View className="ic" style={{ background: '#fef2f2' }}>🚪</View>
            <Text className="t" style={{ color: '#dc2626' }}>退出登录</Text>
            <Text className="arrow">›</Text>
          </View>
        </View>

        <Text className="disclaimer">
          「羽问」内容基于公开资料整理，仅供学习参考，不构成医疗、训练或消费建议。{'\n'}v0.1.0 · 备案信息 · 平台协议
        </Text>
      </View>

      {/* —— 编辑个人信息弹层 —— */}
      {editOpen && (
        <>
          <View className="edit-mask" onClick={() => setEditOpen(false)} />
          <View className="edit-sheet">
            <View className="hd">
              <Text className="t">编辑个人信息</Text>
              <Text className="x" onClick={() => setEditOpen(false)}>✕</Text>
            </View>

            <Text className="edit-label">微信头像（点击授权选择）</Text>
            <View className="edit-avatar-row">
              <View className="edit-avatar">
                {avatarSrc(edAvatar) ? (
                  <Image src={avatarSrc(edAvatar)} mode="aspectFill" />
                ) : (
                  <Text>{edAvatar || '🧑'}</Text>
                )}
              </View>
              <Button
                className="avatar-pick"
                openType="chooseAvatar"
                disabled={picking || uploading}
                onClick={guardPick}
                onChooseAvatar={(e: any) => void onAvatarChosen(e?.detail?.avatarUrl)}
              >
                {uploading ? '上传中…' : picking ? '选择中…' : '从微信头像中选择'}
              </Button>
            </View>

            <Text className="edit-label">微信昵称（聚焦可一键填入）</Text>
            <Input
              className="edit-input"
              type="nickname"
              value={edName}
              placeholder="点击输入，微信昵称自动填入"
              onInput={(e) => setEdName(e.detail.value)}
            />

            <Text className="edit-label">打球水平</Text>
            <Picker mode="selector" range={LEVELS} onChange={(e) => setEdLevel(Number(e.detail.value))}>
              <View className="edit-input">{LEVELS[edLevel]} ▾</View>
            </Picker>

            <Text className="edit-label">性别</Text>
            <View className="gen-row">
              {GENDERS.map((g, i) => (
                <Text key={g} className={`gen${edGender === i ? ' on' : ''}`} onClick={() => setEdGender(i)}>{g}</Text>
              ))}
            </View>

            <Text className="edit-label">主力拍（选填）</Text>
            <Input
              className="edit-input"
              value={edRacket}
              placeholder="如：神速8000 / 4U"
              onInput={(e) => setEdRacket(e.detail.value)}
            />

            <View className="edit-save" onClick={() => void saveEdit()}>
              {saving ? '保存中…' : '保存'}
            </View>
            <Text className="edit-hint">头像与昵称经微信授权获取，仅用于展示与个性化推荐</Text>
          </View>
        </>
      )}
    </View>
  )
}
