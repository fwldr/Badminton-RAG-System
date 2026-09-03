export default defineAppConfig({
  // webview 渲染：Skyline 的 <wx-image> 不支持 HTTP 图片（本地开发 http://127.0.0.1），
  // 上线换 HTTPS 域名后如需 Skyline 再改
  renderer: 'webview',
  pages: [
    'pages/chat/index',          // 提问（首页）
    'pages/discover/index',      // 发现
    'pages/workbench/index',     // 工作台
    'pages/profile/index',       // 我的
    'pages/login/index',         // 登录
    'pages/conv-detail/index',   // 会话详情（历史回放 + 续聊）
    'pages/notifications/index', // 消息通知
    'pages/corrections/index',   // 我的纠错
    'pages/qa-detail/index',     // 问答分享详情（转发落地）
    'pages/settings/index'       // 设置（账号绑定/隐私/清缓存/关于）
  ],
  window: {
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#054b38',
    navigationBarTitleText: '羽问',
    navigationBarTextStyle: 'white'
  },
  tabBar: {
    color: '#7c8b82',
    selectedColor: '#047857',
    backgroundColor: '#ffffff',
    borderStyle: 'white',
    list: [
      { pagePath: 'pages/chat/index', text: '提问', iconPath: 'assets/tabbar/chat.png', selectedIconPath: 'assets/tabbar/chat-active.png' },
      { pagePath: 'pages/discover/index', text: '发现', iconPath: 'assets/tabbar/discover.png', selectedIconPath: 'assets/tabbar/discover-active.png' },
      { pagePath: 'pages/workbench/index', text: '工作台', iconPath: 'assets/tabbar/workbench.png', selectedIconPath: 'assets/tabbar/workbench-active.png' },
      { pagePath: 'pages/profile/index', text: '我的', iconPath: 'assets/tabbar/profile.png', selectedIconPath: 'assets/tabbar/profile-active.png' }
    ]
  }
})
