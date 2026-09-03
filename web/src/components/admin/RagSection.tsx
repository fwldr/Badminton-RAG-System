/* 管理端模块：检索与问答调优中心（RAG Tuning Studio）
   沙箱链路回放 / 运行时参数 / Prompt 模板 / 同义词与敏感词词典 */

import { useCallback, useEffect, useState } from 'react'
import {
  activatePrompt,
  addDict,
  createPrompt,
  deleteDict,
  deletePrompt,
  getRagSettings,
  listDict,
  listPrompts,
  putRagSettings,
  ragDebug,
  updatePrompt,
  type DictItem,
  type PromptTemplate,
  type RagDebugResult,
  type RagSettings,
} from '../../api/client'

interface Props {
  token: string
  onError: (msg: string) => void
  onToast: (msg: string) => void
}

const MODULES = (['dashboard', 'kb', 'rag', 'review', 'system'] as const).map((m) => ({ key: m, label: m }))

function sub(label: string, el: React.ReactNode) {
  return (
    <div key={label} className="rag-block">
      <h4>{label}</h4>
      {el}
    </div>
  )
}

export default function RagSection({ token, onError, onToast }: Props) {
  const [tab, setTab] = useState<'sandbox' | 'params' | 'prompts' | 'dict'>('sandbox')

  // 沙箱
  const [q, setQ] = useState('')
  const [topK, setTopK] = useState(8)
  const [withAnswer, setWithAnswer] = useState(true)
  const [debugging, setDebugging] = useState(false)
  const [debug, setDebug] = useState<RagDebugResult | null>(null)

  // 参数
  const [settings, setSettings] = useState<RagSettings | null>(null)
  const [vecK, setVecK] = useState('')
  const [filterK, setFilterK] = useState('')
  const [blacklistOn, setBlacklistOn] = useState(false)
  const [savingParams, setSavingParams] = useState(false)

  // 模板
  const [prompts, setPrompts] = useState<PromptTemplate[]>([])
  const [editing, setEditing] = useState<PromptTemplate | 'new' | null>(null)
  const [tplName, setTplName] = useState('')
  const [tplDesc, setTplDesc] = useState('')
  const [tplBody, setTplBody] = useState('')

  // 词典
  const [synonyms, setSynonyms] = useState<DictItem[]>([])
  const [blacklist, setBlacklist] = useState<DictItem[]>([])
  const [synWord, setSynWord] = useState('')
  const [synValues, setSynValues] = useState('')
  const [blWord, setBlWord] = useState('')

  const loadSettings = useCallback(async () => {
    try {
      const s = await getRagSettings(token)
      setSettings(s)
      setVecK(s.settings.vector_top_k ?? '')
      setFilterK(s.settings.filter_top_k ?? '')
      setBlacklistOn(s.settings.blacklist_enabled === 'true')
    } catch (e) {
      onError(e instanceof Error ? e.message : '读取参数失败')
    }
  }, [token, onError])

  const loadPrompts = useCallback(async () => {
    try {
      const d = await listPrompts(token)
      setPrompts(d.templates)
    } catch (e) {
      onError(e instanceof Error ? e.message : '读取模板失败')
    }
  }, [token, onError])

  const loadDict = useCallback(async () => {
    try {
      const [s, b] = await Promise.all([listDict(token, 'synonym'), listDict(token, 'blacklist')])
      setSynonyms(s.items)
      setBlacklist(b.items)
    } catch (e) {
      onError(e instanceof Error ? e.message : '读取词典失败')
    }
  }, [token, onError])

  useEffect(() => {
    void loadSettings()
    void loadPrompts()
    void loadDict()
  }, [loadSettings, loadPrompts, loadDict])

  const runDebug = async () => {
    if (!q.trim()) return
    setDebugging(true)
    try {
      const r = await ragDebug(token, q.trim(), { top_k: topK, with_answer: withAnswer })
      setDebug(r)
    } catch (e) {
      onError(e instanceof Error ? e.message : '沙箱执行失败')
    } finally {
      setDebugging(false)
    }
  }

  const saveParams = async () => {
    setSavingParams(true)
    try {
      await putRagSettings(token, {
        vector_top_k: Number(vecK) || undefined,
        filter_top_k: Number(filterK) || undefined,
        blacklist_enabled: blacklistOn,
      })
      onToast('参数已保存，agent 已重建（下次请求生效）')
      await loadSettings()
    } catch (e) {
      onError(e instanceof Error ? e.message : '参数保存失败')
    } finally {
      setSavingParams(false)
    }
  }

  const startEdit = (t: PromptTemplate | 'new') => {
    setEditing(t)
    if (t === 'new') {
      setTplName('')
      setTplDesc('')
      setTplBody('')
    } else {
      setTplName(t.name)
      setTplDesc(t.description || '')
      setTplBody(t.system_prompt)
    }
  }

  const savePrompt = async () => {
    if (!tplName.trim() || !tplBody.trim()) return
    try {
      if (editing === 'new') {
        await createPrompt(token, { name: tplName.trim(), system_prompt: tplBody, description: tplDesc || null })
        onToast(`模板「${tplName.trim()}」已创建`)
      } else if (editing) {
        await updatePrompt(token, editing.id, { name: tplName.trim(), system_prompt: tplBody, description: tplDesc || null })
        onToast(`模板「${tplName.trim()}」已更新`)
      }
      setEditing(null)
      await loadPrompts()
    } catch (e) {
      onError(e instanceof Error ? e.message : '模板保存失败')
    }
  }

  const toggleActive = async (t: PromptTemplate) => {
    try {
      await activatePrompt(token, t.id)
      onToast(`已激活模板「${t.name}」`)
      await loadPrompts()
    } catch (e) {
      onError(e instanceof Error ? e.message : '激活失败')
    }
  }

  const removePrompt = async (t: PromptTemplate) => {
    if (!window.confirm(`删除模板「${t.name}」？`)) return
    try {
      await deletePrompt(token, t.id)
      await loadPrompts()
    } catch (e) {
      onError(e instanceof Error ? e.message : '删除失败')
    }
  }

  const addSyn = async () => {
    if (!synWord.trim()) return
    try {
      await addDict(token, 'synonym', synWord.trim(), synValues.split(/[,，]/).map((s) => s.trim()).filter(Boolean))
      setSynWord('')
      setSynValues('')
      await loadDict()
    } catch (e) {
      onError(e instanceof Error ? e.message : '新增同义词失败')
    }
  }

  const addBl = async () => {
    if (!blWord.trim()) return
    try {
      await addDict(token, 'blacklist', blWord.trim(), [])
      setBlWord('')
      await loadDict()
    } catch (e) {
      onError(e instanceof Error ? e.message : '新增敏感词失败')
    }
  }

  const removeDict = async (type: 'synonym' | 'blacklist', id: number) => {
    try {
      await deleteDict(token, type, id)
      await loadDict()
    } catch (e) {
      onError(e instanceof Error ? e.message : '删除失败')
    }
  }

  return (
    <div>
      <div className="admin-tabs">
        {(
          [
            ['sandbox', '🧪 在线测试沙箱'],
            ['params', '🎛️ 检索参数'],
            ['prompts', '📝 Prompt 模板'],
            ['dict', '📖 词典管理'],
          ] as const
        ).map(([k, label]) => (
          <button key={k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'sandbox' && sub(
        '输入问题，回放完整 RAG 链路（路由 → 查询扩展 → 候选块 → 上下文 → 回答）',
        <div>
          <div className="admin-row">
            <input
              style={{ flex: 1 }}
              placeholder="如：推荐 4U 的进攻型球拍"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <label className="admin-inline">
              Top-K
              <select value={topK} onChange={(e) => setTopK(Number(e.target.value))}>
                {[4, 6, 8, 10, 15].map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </label>
            <label className="admin-inline">
              <input type="checkbox" checked={withAnswer} onChange={(e) => setWithAnswer(e.target.checked)} />
              生成回答
            </label>
            <button onClick={() => void runDebug()} disabled={debugging || !q.trim()}>
              {debugging ? '链路执行中…' : '▶ 运行'}
            </button>
          </div>
          {debug && (
            <div className="rag-debug">
              <p><b>路由：</b>{debug.route}　<b>扩展查询：</b>{debug.expanded_queries.join(' / ')}</p>
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>表</th>
                    <th>得分</th>
                    <th>来源</th>
                    <th>预览</th>
                  </tr>
                </thead>
                <tbody>
                  {debug.candidates.map((c, i) => (
                    <tr key={c.id}>
                      <td>{i + 1}</td>
                      <td>{c.table}</td>
                      <td>{c.score ?? '—'}</td>
                      <td>{c.source}</td>
                      <td className="rag-preview">{c.preview}</td>
                    </tr>
                  ))}
                  {debug.candidates.length === 0 && (
                    <tr>
                      <td colSpan={5} className="admin-hint">无候选（库为空或路由未命中）</td>
                    </tr>
                  )}
                </tbody>
              </table>
              <p><b>过滤条件：</b>{JSON.stringify(debug.conditions)}</p>
              <p className="rag-preview"><b>上下文：</b></p>
              <pre className="rag-pre">{debug.context_block || '（空）'}</pre>
              {debug.answer != null && (
                <>
                  <p><b>LLM 生成：</b></p>
                  <pre className="rag-pre">{debug.answer}</pre>
                </>
              )}
            </div>
          )}
        </div>,
      )}

      {tab === 'params' && sub(
        '运行时参数（保存后 agent 单例重建，下次 /chat 生效）',
        <div>
          <div className="admin-row">
            <label className="admin-inline">
              Vector Top-K
              <input type="number" min={1} max={50} value={vecK} onChange={(e) => setVecK(e.target.value)} />
            </label>
            <label className="admin-inline">
              Filter Top-K
              <input type="number" min={1} max={20} value={filterK} onChange={(e) => setFilterK(e.target.value)} />
            </label>
            <label className="admin-inline">
              <input type="checkbox" checked={blacklistOn} onChange={(e) => setBlacklistOn(e.target.checked)} />
              敏感词守卫
            </label>
            <button onClick={() => void saveParams()} disabled={savingParams}>
              {savingParams ? '保存中…' : '保存参数'}
            </button>
          </div>
          {settings && (
            <p className="admin-hint">
              默认值：vector_top_k={settings.defaults.vector_top_k}，filter_top_k={settings.defaults.filter_top_k}；
              敏感词守卫默认关闭（开启后命中词典黑名单的问题直接拦截）。
            </p>
          )}
        </div>,
      )}

      {tab === 'prompts' && sub(
        'Prompt 模板：激活的模板覆盖生成节点 system 提示（至多一个 active）',
        <div>
          <div className="admin-row">
            {editing === null && (
              <button onClick={() => startEdit('new')}>＋ 新建模板</button>
            )}
            {editing !== null && (
              <>
                <input placeholder="模板名称" value={tplName} onChange={(e) => setTplName(e.target.value)} />
                <input placeholder="说明（可选）" value={tplDesc} onChange={(e) => setTplDesc(e.target.value)} />
              </>
            )}
          </div>
          {editing !== null && (
            <div className="rag-block">
              <textarea
                className="rag-textarea"
                rows={8}
                placeholder={'system 提示词全文（要求模型只输出 JSON：{"answer": ..., "used": [...]}）'}
                value={tplBody}
                onChange={(e) => setTplBody(e.target.value)}
              />
              <div className="admin-row">
                <button onClick={() => void savePrompt()} disabled={!tplName.trim() || !tplBody.trim()}>
                  保存模板
                </button>
                <button onClick={() => setEditing(null)}>取消</button>
              </div>
            </div>
          )}
          <table className="stats-table">
            <thead>
              <tr>
                <th>id</th>
                <th>名称</th>
                <th>说明</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {prompts.map((t) => (
                <tr key={t.id}>
                  <td>{t.id}</td>
                  <td>{t.name}</td>
                  <td className="rag-preview">{t.description || '—'}</td>
                  <td>{t.is_active === 1 ? '✅ 已激活' : '—'}</td>
                  <td>
                    <button className="icon-btn" onClick={() => startEdit(t)}>编辑</button>
                    <button
                      className="icon-btn"
                      disabled={t.is_active === 1}
                      onClick={() => void toggleActive(t)}
                      title={t.is_active === 1 ? '已激活' : '设为激活'}
                    >
                      激活
                    </button>
                    <button className="icon-btn" onClick={() => void removePrompt(t)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )}

      {tab === 'dict' && sub(
        '同义词与敏感词词典（同义词参与查询扩展；敏感词在开启守卫后用于 /chat 与 /ask 输入拦截）',
        <div>
          <div className="rag-block">
            <h5>同义词组（word = 锚点词，values = 其余同义词）</h5>
            <div className="admin-row">
              <input placeholder="锚点词，如 高远球" value={synWord} onChange={(e) => setSynWord(e.target.value)} />
              <input
                placeholder="其余同义词，逗号分隔：如 高球,后场球"
                value={synValues}
                onChange={(e) => setSynValues(e.target.value)}
              />
              <button onClick={() => void addSyn()}>＋ 添加</button>
            </div>
            <div className="dict-list">
              {synonyms.map((s) => (
                <span key={s.id} className="dict-chip">
                  {s.word} → {s.values.join(' / ')}
                  <button className="icon-btn" onClick={() => void removeDict('synonym', s.id)}>✕</button>
                </span>
              ))}
              {synonyms.length === 0 && <span className="admin-hint">暂无同义词组</span>}
            </div>
          </div>
          <div className="rag-block">
            <h5>敏感词（blacklist_enabled=true 时生效）</h5>
            <div className="admin-row">
              <input placeholder="敏感词，如 赌博" value={blWord} onChange={(e) => setBlWord(e.target.value)} />
              <button onClick={() => void addBl()}>＋ 添加</button>
            </div>
            <div className="dict-list">
              {blacklist.map((b) => (
                <span key={b.id} className="dict-chip">
                  {b.word}
                  <button className="icon-btn" onClick={() => void removeDict('blacklist', b.id)}>✕</button>
                </span>
              ))}
              {blacklist.length === 0 && <span className="admin-hint">暂无敏感词</span>}
            </div>
          </div>
        </div>,
      )}

      <p className="admin-hint" style={{ marginTop: 12 }}>
        （模块清单与权限项：{MODULES.map((m) => m.key).join(' / ')}）
      </p>
    </div>
  )
}
