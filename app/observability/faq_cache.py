"""常见问题缓存：LRU + TTL。

约定（见 badminton-rag-phase4-plan.md Step 2）：
- key = 问题原文，**仅在会话无历史时**使用（多轮对话下同一句可能指代不同对象，绝不缓存）；
- 写入条件由调用方把关（verified 且无澄清且非闲聊）；
- 命中直接返回完整 payload（answer/sources/clarification/trace），跳过 agent 调用。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class FaqCache:
    """线程安全的 LRU + TTL 缓存。`now` 可注入（测试用假时钟）。"""

    def __init__(
        self,
        capacity: int = 128,
        ttl: int = 3600,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._capacity = max(1, capacity)
        self._ttl = ttl
        self._now = now or time.time
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}  # key -> (expire_ts, payload)
        self._order: list[str] = []  # 简易 LRU：尾部最新

    def get(self, question: str) -> Any | None:
        if not question:
            return None
        with self._lock:
            item = self._data.get(question)
            if item is None:
                return None
            expire, payload = item
            if self._now() > expire:
                self._data.pop(question, None)
                self._remove_order(question)
                return None
            # 命中：移到最新位
            self._remove_order(question)
            self._order.append(question)
            return payload

    def set(self, question: str, payload: Any) -> None:
        if not question:
            return
        with self._lock:
            if question not in self._data:
                self._order.append(question)
            self._data[question] = (self._now() + self._ttl, payload)
            while len(self._order) > self._capacity:
                evicted = self._order.pop(0)  # 淘汰最旧
                self._data.pop(evicted, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._order.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)

    def _remove_order(self, key: str) -> None:
        try:
            self._order.remove(key)
        except ValueError:
            pass
