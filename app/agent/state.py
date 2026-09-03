"""Agent 状态定义：LangGraph State + trace 记录。

trace 元素只存浅拷贝摘要（{"node","input","output"}），不塞 LiveData（Record/Service 等不可序列化对象）。
"""

from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """一次 Agent 对话的完整状态（LangGraph 节点间共享）。"""

    question: str                      # 原始问题
    session_id: str                    # 多轮会话标识
    scope: str | None                  # 范围限定：equipment / rules / technique / document（强制路由用）
    mode: str                          # 检索模式：classic（默认）| wiki（LLM 导航式检索）
    history: list[dict]                # 历史消息（压缩后的摘要列表）
    route: str                         # equipment / rules / technique / chitchat / multi
    sub_questions: list[str]           # 拆解后的子问题（multi）
    contexts: list[dict]               # 检索上下文 [{table, id, document, metadata, distance}]
    conditions: dict                   # 装备参数过滤条件
    wiki_trace: dict                   # wiki 模式 orient 轨迹 {categories, targets, llm_calls, degraded}
    clarification: str | None          # 需要澄清时的提示（非 None 时不再生成回答）
    answer: str                        # 最终回答
    sources: list[dict]                # 来源 [{table, brand, model}]
    images: list[dict]                 # 图片引用 [{url, title}]（图片文档的展示链接）
    verified: bool                     # 回答校验结果
    retry_count: int                   # 重检索次数（上限 1，防死循环）
    trace: list[dict]                  # 节点执行记录（面试展示）
    error: str | None                  # 异常信息（节点出错时记录）


def trace_entry(node: str, input_summary: object, output_summary: object) -> dict:
    """构造 trace 记录：输入/输出都做轻量摘要，保证可 JSON 序列化。"""
    return {
        "node": node,
        "input": _summarize(input_summary),
        "output": _summarize(output_summary),
    }


def _summarize(obj: object, max_len: int = 300) -> object:
    """把任意对象压缩成可序列化摘要（字符串截断 / dict/list 递归 / 其他转 str）。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _summarize(v, max_len) for k, v in list(obj.items())[:8]}
    if isinstance(obj, (list, tuple)):
        return [_summarize(x, max_len) for x in list(obj)[:5]]
    s = str(obj)
    return s if len(s) <= max_len else s[:max_len] + "..."
