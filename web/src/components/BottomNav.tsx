/* 底部导航（移动优先 4 Tab：首页 / 发现 / 工作台 / 我的） */

export type UserTab = 'home' | 'discover' | 'work' | 'me'

const TABS: { key: UserTab; label: string; icon: string }[] = [
  { key: 'home', label: '首页', icon: '💬' },
  { key: 'discover', label: '发现', icon: '🧭' },
  { key: 'work', label: '工作台', icon: '🗂️' },
  { key: 'me', label: '我的', icon: '👤' },
]

interface Props {
  tab: UserTab
  onChange: (tab: UserTab) => void
}

export default function BottomNav({ tab, onChange }: Props) {
  return (
    <nav className="bottom-nav">
      {TABS.map((t) => (
        <button
          key={t.key}
          className={tab === t.key ? 'active' : ''}
          onClick={() => onChange(t.key)}
        >
          <span className="nav-icon">{t.icon}</span>
          <span>{t.label}</span>
        </button>
      ))}
    </nav>
  )
}
