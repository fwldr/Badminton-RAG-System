/** 轻量 Markdown 渲染（小程序端）：标题 / 加粗 / 行内代码 / 删除线 / 有序·无序列表 / 段落。
 *  用于渲染 LLM 回答，避免 **、--、- 等原始标记直接展示。
 *  小程序的 rich-text 不支持自定义样式且交互弱，这里用 Text/View 结构化渲染。 */

import type { ReactNode } from 'react'
import { Text, View } from '@tarojs/components'

type Block =
  | { t: 'h'; level: number; text: string }
  | { t: 'p'; text: string }
  | { t: 'ul'; items: string[] }
  | { t: 'ol'; items: string[] }

function parseBlocks(src: string): Block[] {
  const blocks: Block[] = []
  let list: { type: 'ul' | 'ol'; items: string[] } | null = null
  const flush = () => {
    if (list) {
      blocks.push(list.type === 'ul' ? { t: 'ul', items: list.items } : { t: 'ol', items: list.items })
      list = null
    }
  }
  for (const raw of src.split('\n')) {
    const line = raw.trim()
    if (!line) {
      flush()
      continue
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line)
    if (h) {
      flush()
      blocks.push({ t: 'h', level: h[1].length, text: h[2] })
      continue
    }
    const ul = /^[-*]\s+(.*)$/.exec(line)
    if (ul) {
      if (!list || list.type !== 'ul') {
        flush()
        list = { type: 'ul', items: [] }
      }
      list.items.push(ul[1])
      continue
    }
    const ol = /^(\d+)[.、]\s+(.*)$/.exec(line)
    if (ol) {
      if (!list || list.type !== 'ol') {
        flush()
        list = { type: 'ol', items: [] }
      }
      list.items.push(`${ol[1]}. ${ol[2]}`)
      continue
    }
    flush()
    blocks.push({ t: 'p', text: line })
  }
  flush()
  return blocks
}

/** 行内标记：**加粗** / `代码` / ~~删除线~~ */
function renderInline(text: string, keyBase: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|~~[^~]+~~)/g).map((p, i) => {
    const k = `${keyBase}-${i}`
    if (/^\*\*[^*]+\*\*$/.test(p)) return <Text key={k} className="b" userSelect>{p.slice(2, -2)}</Text>
    if (/^`[^`]+`$/.test(p)) return <Text key={k} className="code" userSelect>{p.slice(1, -1)}</Text>
    if (/^~~[^~]+~~$/.test(p)) return <Text key={k} className="del" userSelect>{p.slice(2, -2)}</Text>
    return <Text key={k} userSelect>{p}</Text>
  })
}

export default function Md({ text }: { text: string }) {
  const blocks = parseBlocks(text || '')
  if (blocks.length === 0) return null
  return (
    <View className="md">
      {blocks.map((b, i) => {
        if (b.t === 'h') {
          return (
            <Text key={i} className={`md-h md-h${b.level}`} userSelect>
              {renderInline(b.text, `h${i}`)}
            </Text>
          )
        }
        if (b.t === 'ul') {
          return (
            <View key={i} className="md-ul">
              {b.items.map((it, j) => (
                <View key={j} className="md-li">
                  <Text className="bullet" userSelect>•</Text>
                  <Text className="txt" userSelect>{renderInline(it, `ul${i}-${j}`)}</Text>
                </View>
              ))}
            </View>
          )
        }
        if (b.t === 'ol') {
          return (
            <View key={i} className="md-ul">
              {b.items.map((it, j) => (
                <Text key={j} className="md-li-ol" userSelect>
                  {renderInline(it, `ol${i}-${j}`)}
                </Text>
              ))}
            </View>
          )
        }
        return (
          <Text key={i} className="md-p" userSelect>
            {renderInline(b.text, `p${i}`)}
          </Text>
        )
      })}
    </View>
  )
}
