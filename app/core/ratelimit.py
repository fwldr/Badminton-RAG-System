"""内存令牌桶限流器（进程内，单机够用；生产多实例换 Redis：INCR + EXPIRE）。"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """令牌桶：capacity 桶容量，refill_per_sec 每秒回填令牌数。

    acquire(key) 成功返回 True 并消耗 1 个令牌；桶空返回 False。
    各 key 独立计数（如按 IP / 按用户）。
    """

    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity 必须为正")
        self.capacity = float(capacity)
        self.refill_per_sec = refill_per_sec
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = threading.Lock()

    def acquire(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            # 回填：距上次消耗经过的时间 * 回填速率
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True
