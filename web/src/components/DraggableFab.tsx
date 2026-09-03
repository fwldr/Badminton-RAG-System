/* 可拖拽悬浮「新提问」按钮（UserShell 右下角 ＋）。
 * 约定（与 styles/index.css 的 .draggable-fab 注释对齐）：
 * - position: fixed，坐标由本组件写入 style.left/top；
 * - 拖拽中加 draggable-fab--dragging 类（CSS 关闭过渡并放大）；
 * - 位移超过阈值(4px)判定为拖拽并抑制随后的 click，否则视为点击触发 onClick；
 * - 位置持久化到 localStorage（key: br_fab_pos），窗口缩放时重新夹取到可视区内。 */

import { useEffect, useRef, useState } from 'react'

const SIZE = 54            // 与 CSS 宽高一致，用于视口夹取
const EDGE = 12            // 距视口边缘最小间距
const CLICK_THRESHOLD = 4  // px：小于该位移视为点击而非拖拽
const POS_KEY = 'br_fab_pos'

interface Props {
  onClick: () => void
  title?: string
}

interface Pos {
  left: number
  top: number
}

function clampPos(pos: Pos): Pos {
  return {
    left: Math.min(Math.max(EDGE, pos.left), window.innerWidth - SIZE - EDGE),
    top: Math.min(Math.max(EDGE, pos.top), window.innerHeight - SIZE - EDGE),
  }
}

function defaultPos(): Pos {
  // 默认右下角：避开底部导航（约 64px 高 + 留白）
  return clampPos({
    left: window.innerWidth - SIZE - EDGE * 2,
    top: window.innerHeight - SIZE - 88,
  })
}

function loadPos(): Pos {
  try {
    const raw = localStorage.getItem(POS_KEY)
    if (raw) {
      const p = JSON.parse(raw)
      if (p && typeof p.left === 'number' && typeof p.top === 'number') return clampPos(p)
    }
  } catch {
    // 解析失败回退默认位置
  }
  return defaultPos()
}

export default function DraggableFab({ onClick, title = '新提问' }: Props) {
  const [pos, setPos] = useState<Pos>(loadPos)
  const [dragging, setDragging] = useState(false)
  // 拖拽会话状态：按下时记录指针与元素的相对偏移、起点坐标；移动超阈值置 moved
  const offset = useRef<{ dx: number; dy: number } | null>(null)
  const start = useRef<{ x: number; y: number } | null>(null)
  const moved = useRef(false)

  // 视口变化时把按钮夹回可视区
  useEffect(() => {
    const onResize = () => setPos((p) => clampPos(p))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const onPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    const rect = e.currentTarget.getBoundingClientRect()
    offset.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top }
    start.current = { x: e.clientX, y: e.clientY }
    moved.current = false
    setDragging(true)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!offset.current || !start.current) return
    if (
      !moved.current &&
      Math.hypot(e.clientX - start.current.x, e.clientY - start.current.y) < CLICK_THRESHOLD
    ) {
      return // 未超阈值：还在「可能是一次点击」的容差内，不移动
    }
    moved.current = true
    setPos(clampPos({ left: e.clientX - offset.current!.dx, top: e.clientY - offset.current!.dy }))
  }

  const onPointerEnd = () => {
    offset.current = null
    start.current = null
    setDragging(false)
    if (moved.current) {
      try {
        localStorage.setItem(POS_KEY, JSON.stringify(pos))
      } catch {
        // localStorage 不可用：仅本次会话内记忆
      }
    }
  }

  const handleClick = () => {
    if (moved.current) return // 拖拽收尾的 click：抑制误触
    onClick()
  }

  return (
    <button
      type="button"
      className={`draggable-fab${dragging ? ' draggable-fab--dragging' : ''}`}
      data-dragging={dragging || undefined}
      style={{ position: 'fixed', left: pos.left, top: pos.top }}
      title={title}
      aria-label={title}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onClick={handleClick}
    >
      ＋
    </button>
  )
}
