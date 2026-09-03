/** 登录页：微信一键登录（对应原型 login.html） */

import { useState } from 'react'
import Taro from '@tarojs/taro'
import { Button, Text, View } from '@tarojs/components'
import { useUserStore } from '../../store/user'
import './index.scss'

export default function LoginPage() {
  const wxLogin = useUserStore((s) => s.wxLogin)
  const loggingIn = useUserStore((s) => s.loggingIn)
  const [agreed, setAgreed] = useState(true)

  const handleLogin = async () => {
    if (!agreed) {
      Taro.showToast({ title: '请先勾选同意用户协议与隐私政策', icon: 'none' })
      return
    }
    const ok = await wxLogin()
    if (ok) {
      Taro.switchTab({ url: '/pages/chat/index' })
    } else {
      Taro.showToast({ title: '登录失败，请重试（需在真机/微信环境）', icon: 'none' })
    }
  }

  return (
    <View>
      <View className="hero">
        <View className="rings"><i /><i /></View>
        <View className="feather">🏸</View>
        <View className="wordmark">
          <View className="cn">羽问</View>
          <View className="en">YUWEN · BADMINTON AI</View>
        </View>
        <View className="slogan">
          球拍、规则、技术……
          {'\n'}你问，<Text className="em">我答</Text>。只依据专业资料回答，不编造一个字。
        </View>
      </View>

      <View className="body">
        <Text className="title">微信一键登录 · 三秒开问</Text>
        <View className="sub">
          登录后可保存多轮对话、收藏问答并提交纠错，助手会记住你的水平与偏好。
        </View>
        <Button className={`wechat-btn${loggingIn ? ' loading' : ''}`} disabled={loggingIn} onClick={() => void handleLogin()}>
          {loggingIn ? '登录中…' : '💬 微信一键登录'}
        </Button>
        <View className="agree" onClick={() => setAgreed(!agreed)}>
          <View className={`tick${agreed ? '' : ' off'}`}>✓</View>
          <Text>已阅读并同意《用户协议》与《隐私政策》</Text>
        </View>
        <View className="disclaimer">羽问内容仅供羽毛球运动学习参考，不构成医疗、训练或消费建议。</View>
      </View>
    </View>
  )
}
