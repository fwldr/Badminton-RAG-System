"""Token 用量统计：按 route 聚合 LLM token，输出成本报表（进程级单例使用）。"""

from __future__ import annotations

import threading

_TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


class TokenCounter:
    """按 route 聚合 token 与调用次数（线程安全）。

    用法：每请求结束后 `counter.add(route, {"total_tokens": ...})`；
    报表用 `report()` 输出，供 /chat/stats（管理鉴权）查看。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}

    def add(self, route: str, usage: dict | None) -> None:
        """累加一次 usage 到指定 route（空/坏值忽略对应字段，不抛异常）。"""
        if not isinstance(usage, dict):
            return
        with self._lock:
            row = self._data.setdefault(
                route or "unknown",
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0},
            )
            row["calls"] += 1
            for key in _TOKEN_KEYS:
                try:
                    row[key] += int(usage.get(key, 0) or 0)
                except (TypeError, ValueError):
                    pass

    def report(self) -> list[dict]:
        """按 route 排序的成本报表行。"""
        with self._lock:
            return [{"route": route, **row} for route, row in sorted(self._data.items())]

    def reset(self) -> None:
        with self._lock:
            self._data.clear()
