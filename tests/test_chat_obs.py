"""/chat 可观测性测试：FAQ 缓存命中跳过 agent + 成本统计端点鉴权（stub agent，不触网）。"""

from fastapi.testclient import TestClient

from app.api.routes.chat import _get_faq_cache, get_agent
from app.core.config import get_settings
from main import create_app


class CacheableStubAgent:
    """返回 verified=True 的桩 agent：用于验证缓存写入/命中。"""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, state: dict) -> dict:
        self.calls += 1
        return {
            "question": state["question"],
            "answer": "推荐李宁 GP203 红色手胶。",
            "sources": [],
            "clarification": None,
            "trace": [{"node": "route", "input": {}, "output": {"route": "equipment"}}],
            "route": "equipment",
            "verified": True,
        }


def _client(stub) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_agent] = lambda: stub
    return TestClient(app)


def test_faq_cache_hit_skips_agent():
    _get_faq_cache().clear()
    stub = CacheableStubAgent()
    client = _client(stub)
    # 用独立 session id，避免与其他测试共享模块级 _memory
    r1 = client.post("/chat", json={"session_id": "obs-s1", "question": "推荐红色手胶"})
    assert r1.status_code == 200
    assert r1.json()["data"]["cached"] is False
    assert r1.json()["data"]["trace_id"] != ""
    # 新会话同问题：无历史 → 命中缓存，跳过 agent
    r2 = client.post("/chat", json={"session_id": "obs-s2", "question": "推荐红色手胶"})
    assert r2.json()["data"]["cached"] is True
    assert r2.json()["data"]["answer"] == "推荐李宁 GP203 红色手胶。"
    assert stub.calls == 1


def test_faq_cache_not_used_in_multi_turn():
    """同会话第二轮（有历史）不查缓存，即使问题相同。"""
    _get_faq_cache().clear()
    stub = CacheableStubAgent()
    client = _client(stub)
    client.post("/chat", json={"session_id": "obs-s3", "question": "推荐红色手胶"})
    client.post("/chat", json={"session_id": "obs-s3", "question": "推荐红色手胶"})
    assert stub.calls == 2  # 第二轮有历史 → 走 agent


def test_chat_stats_requires_admin_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_api_key", "test-key")
    stub = CacheableStubAgent()
    client = _client(stub)
    # 无 key → 401
    assert client.get("/chat/stats").status_code == 401
    # 有 key → 200，返回 rows 列表
    resp = client.get("/chat/stats", headers={"X-Admin-Key": "test-key"})
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"]["rows"], list)


def test_chat_response_langfuse_url_none_with_null_tracer():
    """未启用 Langfuse（NullTracer，默认）时 langfuse_url 必须为 null，前端据此隐藏 trace 链接。"""
    _get_faq_cache().clear()
    stub = CacheableStubAgent()
    client = _client(stub)
    r = client.post("/chat", json={"session_id": "obs-s4", "question": "推荐红色手胶"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert "langfuse_url" in body
    assert body["langfuse_url"] is None
    assert body["trace_id"] != ""
