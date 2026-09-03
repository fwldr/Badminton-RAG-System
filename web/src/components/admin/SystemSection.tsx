/* 管理端模块：系统运维与设置（只读展示；密钥掩码，由后端返回） */

import { useEffect, useState } from 'react'
import { systemConfig, type SystemConfig } from '../../api/client'

interface Props {
  token: string
  onError: (msg: string) => void
}

function kvRows(obj: Record<string, unknown>): [string, string][] {
  return Object.entries(obj).map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)])
}

const GROUPS: { key: string; title: string }[] = [
  { key: 'models', title: '模型服务（密钥已掩码）' },
  { key: 'database', title: '业务数据库' },
  { key: 'vector_store', title: '向量库' },
  { key: 'limits', title: '限流与上传限制' },
  { key: 'ingest', title: '文档入库预处理' },
  { key: 'auth', title: '账户与令牌' },
]

export default function SystemSection({ token, onError }: Props) {
  const [cfg, setCfg] = useState<SystemConfig | null>(null)

  useEffect(() => {
    systemConfig(token)
      .then(setCfg)
      .catch((e) => onError(e instanceof Error ? e.message : '配置读取失败'))
  }, [token, onError])

  if (!cfg) return <p className="admin-hint">加载中…</p>

  return (
    <div>
      <p className="admin-hint">
        只读展示（config 来源）。运行时修改密钥/限流涉及 .env 热生效语义，暂不支持在线更新；
        修改后重启服务生效。RAG 检索参数可在「检索调优 → 检索参数」运行时可调。
      </p>
      {GROUPS.map((g) => (
        <div key={g.key} className="rag-block">
          <h4>{g.title}</h4>
          <table className="stats-table">
            <tbody>
              {kvRows((cfg as unknown as Record<string, unknown>)[g.key] as Record<string, unknown>).map(([k, v]) => (
                <tr key={k}>
                  <td style={{ width: 220 }}>{k}</td>
                  <td className="rag-preview">{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}
