import { PropsWithChildren } from 'react'
import { useLaunch } from '@tarojs/taro'

import './app.scss'

function App({ children }: PropsWithChildren<any>) {
  useLaunch(() => {
    console.log('羽问小程序已启动')
  })

  // children 是将要会渲染的页面
  return children
}

export default App
