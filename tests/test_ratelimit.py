"""限流器测试：令牌桶逻辑 + API 限流依赖。"""

import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import rate_limit
from app.api.errors import register_exception_handlers


def test_token_bucket_initial_capacity():
    from app.core.ratelimit import RateLimiter

    limiter = RateLimiter(capacity=3, refill_per_sec=0)
    assert limiter.acquire("k") is True
    assert limiter.acquire("k") is True
    assert limiter.acquire("k") is True
    assert limiter.acquire("k") is False  # 桶空


def test_token_bucket_refill():
    from app.core.ratelimit import RateLimiter

    limiter = RateLimiter(capacity=1, refill_per_sec=10)  # 每秒回 10 个
    assert limiter.acquire("k") is True
    assert limiter.acquire("k") is False  # 空了
    time.sleep(0.15)  # 回填 ~1.5 个
    assert limiter.acquire("k") is True


def test_token_bucket_independent_keys():
    from app.core.ratelimit import RateLimiter

    limiter = RateLimiter(capacity=1, refill_per_sec=0)
    assert limiter.acquire("a") is True
    assert limiter.acquire("b") is True  # 各 key 独立
    assert limiter.acquire("a") is False


def test_rate_limit_dependency_429():
    """路由级：小容量限流依赖，超限返回 42901。"""
    app = FastAPI()

    @app.get("/ping", dependencies=[Depends(rate_limit(1, 0))])
    async def ping():
        return {"ok": True}

    register_exception_handlers(app)
    client = TestClient(app)
    assert client.get("/ping").status_code == 200
    resp = client.get("/ping")
    assert resp.status_code == 429
    assert resp.json()["code"] == 42901
