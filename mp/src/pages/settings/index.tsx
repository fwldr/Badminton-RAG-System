/** 设置页：微信/手机号绑定与解绑、清除缓存、隐私与关于、版本与备案占位。 */

import { useState } from 'react'
import Taro from '@tarojs/taro'
import { Button, Text, View } from '@tarojs/components'
import { bindWechatPhone, unbind } from '../../api/auth'
import { useUserStore } from '../../store/user'
import './index.scss'

export default function SettingsPage() {
  const user = useUserStore((s) => s.user)
  const setUser = useUserStore((s) => s.setUser)
  const [busy, setBusy] = useState(false)

  const doUnbind = (type: 'wechat' | 'phone', label: string) => {
    Taro.showModal({
      title: `解绑${label}`,
      content: type === 'wechat' ? '解绑后该账号仅能再次通过微信登录（如需换绑请先解绑）' : '解绑后手机号关联将移除',
      confirmColor: '#dc2626',
      success: async (res) => {
        if (!res.confirm) return
        try {
          const updated = await unbind(type)
          setUser(updated)
          Taro.showToast({ title: `已解绑${label}`, icon: 'success' })
        } catch (e) {
          Taro.showToast({ title: e instanceof Error ? e.message : '解绑失败', icon: 'none' })
        }
      },
    })
  }

  const onGetPhone = async (detail: { code?: string }) => {
    const code = detail?.code
    if (!code) {
      Taro.showToast({ title: '未授权手机号', icon: 'none' })
      return
    }
    if (busy) return
    setBusy(true)
    try {
      const updated = await bindWechatPhone(code)
      setUser(updated)
      Taro.showToast({ title: '手机号绑定成功', icon: 'success' })
    } catch (e) {
      Taro.showToast({ title: e instanceof Error ? e.message : '绑定失败', icon: 'none' })
    } finally {
      setBusy(false)
    }
  }

  const clearCache = () => {
    Taro.showModal({
      title: '清除缓存',
      content: '将清除本地会话缓存与预填问题（登录态与收藏数据不受影响）',
      success: (res) => {
        if (!res.confirm) return
        try { Taro.removeStorageSync('mp_session_id') } catch { /* 忽略 */ }
        try { Taro.removeStorageSync('mp_prefill') } catch { /* 忽略 */ }
        try { Taro.removeStorageSync('mp_active_conv') } catch { /* 忽略 */ }
        Taro.showToast({ title: '缓存已清除', icon: 'success' })
      },
    })
  }

  const wxBound = !!user?.wx_bound
  const phoneBound = !!user?.phone_bound

  return (
    <View className="page">
      <View className="card">
        <View className="row">
          <View className="ic">💬</View>
          <View className="lbl">
            <Text className="t">微信登录</Text>
            <Text className="s">{wxBound ? `已绑定 ${user?.username}` : '未绑定（当前为账号密码登录）'}</Text>
          </View>
          {wxBound ? (
            <Text className="btn danger" onClick={() => doUnbind('wechat', '微信')}>解绑</Text>
          ) : (
            <Text className="state off">未绑定</Text>
          )}
        </View>
        <View className="row">
          <View className="ic">📱</View>
          <View className="lbl">
            <Text className="t">手机号</Text>
            <Text className="s">用于账号安全与通知（微信手机号快速验证）</Text>
          </View>
          {phoneBound ? (
            <Text className="btn danger" onClick={() => doUnbind('phone', '手机号')}>解绑</Text>
          ) : (
            <Button
              className="btn primary"
              openType="getPhoneNumber"
              onGetPhoneNumber={(e) => void onGetPhone(e.detail)}
              style={{ margin: 0, width: 'auto' }}
            >
              {busy ? '绑定中…' : '绑定'}
            </Button>
          )}
        </View>
        <View className="row">
          <View className="ic">🧹</View>
          <View className="lbl">
            <Text className="t">清除缓存</Text>
            <Text className="s">会话临时数据与预填问题</Text>
          </View>
          <Text className="btn subtle" onClick={clearCache}>清除</Text>
        </View>
      </View>

      <View className="card">
        <View className="row">
          <View className="ic">🛡️</View>
          <View className="lbl">
            <Text className="t">隐私与授权</Text>
            <Text className="s">《用户信息保护指引》· 微信授权管理</Text>
          </View>
          <Text className="state">›</Text>
        </View>
        <View className="row">
          <View className="ic">🧾</View>
          <View className="lbl">
            <Text className="t">用户协议与隐私政策</Text>
            <Text className="s">登录页勾选即视为同意</Text>
          </View>
          <Text className="state">›</Text>
        </View>
      </View>

      <View className="card about">
        <View className="logo">🏸</View>
        <View className="name">羽问</View>
        <Text className="ver">v0.1.0 · 羽毛球知识问答小程序</Text>
        <Text className="desc">
          知识库来源公开资料整理，仅供学习参考，不构成医疗、训练或消费建议。
          {'\n'}备案号：___（上线前填写）· 平台：微信小程序
        </Text>
      </View>
    </View>
  )
}
