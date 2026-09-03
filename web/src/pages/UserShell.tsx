/* 用户端外壳：移动优先 4 Tab（首页 / 发现 / 工作台 / 我的） */

import { useState } from 'react'
import type { AuthUser } from '../api/auth'
import BottomNav, { type UserTab } from '../components/BottomNav'
import DraggableFab from '../components/DraggableFab'
import ChatPage, { type AskRequest, type ChatInit } from './ChatPage'
import DiscoverPage from './DiscoverPage'
import ProfilePage from './ProfilePage'
import WorkbenchPage, { type OpenPayload } from './WorkbenchPage'

interface Props {
  token: string
  user: AuthUser
  onUserChange: (user: AuthUser) => void
  onAdmin: () => void
  onLogout: () => void
}

export default function UserShell({ token, user, onUserChange, onAdmin, onLogout }: Props) {
  const [tab, setTab] = useState<UserTab>('home')
  const [chatInit, setChatInit] = useState<ChatInit>({ sessionId: null, title: '', messages: [] })
  const [chatKey, setChatKey] = useState(0)
  const [ask, setAsk] = useState<AskRequest | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const openConversation = (p: OpenPayload) => {
    setChatInit({ sessionId: p.sessionId, title: p.title, messages: p.messages })
    setChatKey((k) => k + 1)
    setAsk(null)
    setTab('home')
  }

  const newChat = () => {
    setChatInit({ sessionId: null, title: '', messages: [] })
    setChatKey((k) => k + 1)
    setAsk(null)
    setTab('home')
  }

  const askQuestion = (question: string, scope: string | null) => {
    setAsk({ key: Date.now(), question, scope })
    setChatInit({ sessionId: null, title: '', messages: [] })
    setChatKey((k) => k + 1)
    setTab('home')
  }

  return (
    <div className="app user-shell">
      <div className="shell-body">
        {tab === 'home' && (
          <ChatPage
            key={chatKey}
            token={token}
            user={user}
            initial={chatInit}
            ask={ask}
            onAskConsumed={() => setAsk(null)}
            onChanged={() => setRefreshKey((k) => k + 1)}
            onAdmin={onAdmin}
            onLogout={onLogout}
          />
        )}
        {tab === 'discover' && <DiscoverPage token={token} onAsk={askQuestion} />}
        {tab === 'work' && (
          <WorkbenchPage token={token} refreshKey={refreshKey} onOpen={openConversation} />
        )}
        {tab === 'me' && (
          <ProfilePage
            token={token}
            user={user}
            onUserChange={onUserChange}
            onAdmin={onAdmin}
            onLogout={onLogout}
          />
        )}
      </div>
      <BottomNav tab={tab} onChange={(t) => setTab(t)} />
      <DraggableFab onClick={newChat} title="新提问" />
    </div>
  )
}
