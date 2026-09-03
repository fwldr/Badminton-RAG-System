/* 管理端模块：数据源与知识库管理——上传/列表/删除/重索引 + 元数据打标 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteDocument,
  listDocuments,
  patchDocTags,
  reindexDocument,
  uploadDocument,
  type DocRecord,
} from '../../api/client'

interface Props {
  token: string
  onError: (msg: string) => void
  onToast: (msg: string) => void
}

export default function KbSection({ token, onError, onToast }: Props) {
  const [docs, setDocs] = useState<DocRecord[]>([])
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  // 打标编辑状态：{docId: 输入框草稿}
  const [tagDraft, setTagDraft] = useState<Record<number, string>>({})
  const [savingTags, setSavingTags] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await listDocuments(token)
      setDocs(data.documents)
    } catch (e) {
      onError(e instanceof Error ? e.message : '加载文档列表失败')
    }
  }, [token, onError])

  useEffect(() => {
    void load()
  }, [load])

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const res = await uploadDocument(file, token)
      onToast(`${res.filename}：${res.status}（${res.chunk_count} 块）`)
      if (fileRef.current) fileRef.current.value = ''
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (doc: DocRecord) => {
    if (!window.confirm(`确定删除「${doc.filename}」？（记录 + 向量 + 原文件）`)) return
    try {
      await deleteDocument(doc.id, token)
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : '删除失败')
    }
  }

  const handleReindex = async (doc: DocRecord) => {
    try {
      const res = await reindexDocument(doc.id, token)
      onToast(`「${doc.filename}」重索引完成：版本 ${res.version}，${res.chunk_count} 块`)
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : '重索引失败')
    }
  }

  const handleSaveTags = async (doc: DocRecord) => {
    setSavingTags(doc.id)
    try {
      const draft = (tagDraft[doc.id] ?? doc.tags ?? '') as string
      const tags = draft
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean)
      await patchDocTags(doc.id, tags, token)
      onToast(`「${doc.filename}」标签已更新（${tags.length} 个）`)
      await load()
    } catch (e) {
      onError(e instanceof Error ? e.message : '标签保存失败')
    } finally {
      setSavingTags(null)
    }
  }

  return (
    <div>
      <p className="admin-hint">
        支持 pdf / 图片（OCR + 多模态）/ txt / md / csv；保存原文件、删除时同步清理向量与副本。
      </p>
      <div className="admin-row">
        <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.txt,.md,.csv" disabled={uploading} />
        <button onClick={() => void handleUpload()} disabled={uploading}>
          {uploading ? '上传入库中…' : '上传入库'}
        </button>
      </div>

      <table className="stats-table">
        <thead>
          <tr>
            <th>id</th>
            <th>文件名</th>
            <th>类型</th>
            <th>状态</th>
            <th>块数</th>
            <th>版本</th>
            <th>标签</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <td>{d.id}</td>
              <td>{d.filename}</td>
              <td>{d.doc_type}</td>
              <td>
                {d.status}
                {d.status === 'failed' && d.error_msg ? `（${d.error_msg}）` : ''}
              </td>
              <td>{d.chunk_count}</td>
              <td>{d.version}</td>
              <td style={{ minWidth: 140 }}>
                <input
                  className="tag-input"
                  value={tagDraft[d.id] ?? d.tags ?? ''}
                  placeholder="规则类,2024赛事"
                  onChange={(e) => setTagDraft((p) => ({ ...p, [d.id]: e.target.value }))}
                />
                <button
                  className="icon-btn"
                  disabled={savingTags === d.id}
                  onClick={() => void handleSaveTags(d)}
                  title="保存标签"
                >
                  {savingTags === d.id ? '…' : '存'}
                </button>
              </td>
              <td>
                <button className="icon-btn" onClick={() => void handleReindex(d)} title="重索引（版本 +1）">
                  重索引
                </button>
                <button className="icon-btn" onClick={() => void handleDelete(d)} title="删除">
                  删除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {docs.length === 0 && (
        <p className="admin-hint">
          暂无上传文档（可用 CLI：python -m app.ingest.pipeline --dir data/raw_docs 批量导入）
        </p>
      )}
    </div>
  )
}
