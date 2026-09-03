"""输入守卫：敏感词（黑名单）过滤。

管理端 RAG 词典维护黑名单（rag_dictionary.type=blacklist）；`blacklist_enabled`
运行时参数开启后，/chat 与 /ask 入口命中敏感词直接拦截（后端友好拒绝）。
"""

from __future__ import annotations


def contains_blacklist(text: str, words: list[str]) -> str | None:
    """返回文本中命中的第一个敏感词；未命中返回 None。"""
    for word in words:
        w = str(word).strip()
        if w and w in text:
            return w
    return None


def blacklist_reply(word: str) -> dict:
    """拦截时返回的标准化响应（与 /chat 响应结构对齐，前端零改动渲染）。"""
    return {
        "answer": f"抱歉，您的问题包含敏感内容（{word}），无法回答。",
        "sources": [],
        "images": [],
        "clarification": None,
        "trace": [{"node": "guard", "input": {}, "output": {"blocked": word}}],
        "trace_id": "blocked",
        "cached": False,
        "langfuse_url": None,
    }
