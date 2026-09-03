"""FAQ 缓存单元测试：命中/过期/LRU 淘汰/空 key 守卫（注入假时钟，不依赖真实时间）。"""

from app.observability.faq_cache import FaqCache


def _cache(capacity=8, ttl=3600):
    clock = [1000.0]
    return FaqCache(capacity=capacity, ttl=ttl, now=lambda: clock[0]), clock


def test_miss_then_hit():
    c, _ = _cache()
    assert c.get("q") is None
    c.set("q", {"answer": "a"})
    assert c.get("q") == {"answer": "a"}


def test_ttl_expiry():
    c, clock = _cache(ttl=3600)
    c.set("q", {"answer": "a"})
    clock[0] += 1800  # 半小时后仍有效
    assert c.get("q") == {"answer": "a"}
    clock[0] += 2000  # 超过 1 小时 → 过期
    assert c.get("q") is None


def test_lru_eviction():
    c, _ = _cache(capacity=2)
    c.set("a", 1)
    c.set("b", 2)
    assert c.get("a") == 1  # a 变成最新
    c.set("c", 3)  # 淘汰最旧的 b
    assert c.get("b") is None
    assert c.get("c") == 3
    assert c.size == 2


def test_empty_question_guards():
    c, _ = _cache()
    assert c.get("") is None
    c.set("", {"answer": "a"})  # 空 key 不写入
    assert c.size == 0


def test_clear():
    c, _ = _cache()
    c.set("q", 1)
    c.clear()
    assert c.size == 0
    assert c.get("q") is None
