"""多轮记忆：会话历史存储 + 压缩（内存 dict，可换 Redis）。"""

from __future__ import annotations

import logging
import threading

from app.rag.llm import LLMClient, parse_filter_json

logger = logging.getLogger(__name__)

DEFAULT_MAX_HISTORY = 8  # 超过该条数触发压缩


class MemoryStore:
    """按 session_id 存储对话历史（内存版；生产可换 Redis）。

    每条消息形态：{"role": "user"|"assistant", "content": str}
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def append(self, session_id: str, message: dict) -> None:
        with self._lock:
            self._store.setdefault(session_id, []).append(message)

    def get(self, session_id: str, limit: int | None = None) -> list[dict]:
        with self._lock:
            msgs = list(self._store.get(session_id, []))
        return msgs[-limit:] if limit else msgs

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)


def compress_history(
    history: list[dict], llm: LLMClient | None, max_items: int = DEFAULT_MAX_HISTORY
) -> list[dict]:
    """历史压缩：超过 max_items 条时，把旧消息压成一句摘要。

    返回压缩后的消息列表（保留最近若干条原文 + 前置摘要）。
    """
    if len(history) <= max_items:
        return history
    old = history[:-max_items]
    recent = history[-max_items:]

    if llm is None:
        # 无 LLM 时退化为丢弃最旧消息
        return recent

    lines = [f"{m.get('role')}: {m.get('content', '')}" for m in old]
    system = (
        "把以下对话历史压缩成一句中文摘要，保留关键信息（用户问过什么、系统答过什么）。"
        '只输出 JSON：{"summary": "..."}。不要输出其他文字。'
    )
    try:
        text = llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(lines)},
            ],
            json_mode=True,
        )
        data = parse_filter_json(text)
        summary = str(data.get("summary", "")).strip()
        if summary:
            return [{"role": "system", "content": f"历史摘要：{summary}"}] + recent
    except Exception:
        logger.exception("历史压缩失败，退化为丢弃旧消息")
    return recent
