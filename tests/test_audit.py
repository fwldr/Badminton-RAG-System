"""审计路由测试：鉴权 + 列表 + 导出 + ask 埋点。"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import require_admin_key
from app.api.routes.ask import get_ask_service
from app.api.errors import register_exception_handlers
from app.core.config import get_settings
from app.db.database import reset_db
from app.db.repos import AuditRepo
from app.models.schema import AskSource
from app.rag.service import AskResult
from main import create_app


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "audit.db")
    monkeypatch.setattr(get_settings(), "admin_api_key", "test-admin-key")
    reset_db()
    yield
    reset_db()


class StubService:
    def ask(self, question: str) -> AskResult:
        return AskResult(
            answer="推荐李宁 GP203",
            sources=[AskSource(table="grip_specs", brand="李宁 LINING", model="GP203")],
        )


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_ask_service] = lambda: StubService()
    return TestClient(app)


def test_audit_requires_admin_key():
    client = _client()
    assert client.get("/audit/logs").status_code == 401
    assert client.get("/audit/logs", headers={"X-Admin-Key": "wrong"}).status_code == 401
    resp = client.get("/audit/logs", headers={"X-Admin-Key": "test-admin-key"})
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 0


def test_ask_writes_audit_log():
    client = _client()
    resp = client.post("/ask", json={"question": "推荐红色手胶"})
    assert resp.status_code == 200
    logs = AuditRepo.query()
    assert len(logs) == 1
    assert logs[0]["question"] == "推荐红色手胶"
    assert "GP203" in logs[0]["answer"]
    assert logs[0]["sources_json"] and "grip_specs" in logs[0]["sources_json"]
    assert logs[0]["latency_ms"] is not None


def test_audit_export_csv():
    client = _client()
    client.post("/ask", json={"question": "问题A"})
    resp = client.get("/audit/logs/export", headers={"X-Admin-Key": "test-admin-key"})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    text = resp.content.decode("utf-8")
    assert text.startswith("\ufeff")  # BOM
    assert "问题A" in text
