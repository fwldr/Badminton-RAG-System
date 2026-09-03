"""管理端 Dashboard 测试：总览统计 + 健康探活（离线：内存向量库 + stub 探针）。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.routes.admin_dashboard import get_probes
from app.api.routes.kb import get_kb_store
from app.core.config import get_settings
from app.core.security import create_token, hash_password
from app.db.database import init_db, reset_db
from app.db.repos import (
    ConversationRepo,
    CorrectionRepo,
    DocRepo,
    FeedbackRepo,
    MessageRepo,
    UserRepo,
)
from app.ingest.store import VectorStore
from main import create_app


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "admin.db")
    monkeypatch.setattr(get_settings(), "admin_api_key", "admin-key-1")
    reset_db()
    init_db()
    yield
    reset_db()


def _admin_headers(permissions=None) -> dict:
    existing = UserRepo.get_by_username("qa_admin")
    uid = existing["id"] if existing else UserRepo.create("qa_admin", hash_password("pw"), role="admin")
    if permissions is not None:
        UserRepo.set_permissions(uid, json.dumps(permissions, ensure_ascii=False))
    s = get_settings()
    token = create_token({"sub": str(uid), "role": "admin"}, s.auth_token_secret, 3600)
    return {"Authorization": f"Bearer {token}"}


def _new_client(probes: dict | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_kb_store] = lambda: VectorStore()  # 内存版
    app.dependency_overrides[get_probes] = lambda: probes or {
        "数据库": lambda: {"ok": True},
        "向量库": lambda: {"collections": 0},
        "百炼": lambda: {"url": "http://x", "status": 200},
    }
    return TestClient(app)


def test_dashboard_requires_admin_or_forbidden():
    client = _new_client()
    assert client.get("/admin/dashboard").status_code == 401
    uid = UserRepo.create("u1", hash_password("pw"), role="user")
    s = get_settings()
    token = create_token({"sub": str(uid), "role": "user"}, s.auth_token_secret, 3600)
    resp = client.get("/admin/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_dashboard_stats_ok():
    client = _new_client()
    DocRepo.create("规则手册.pdf", "pdf")
    DocRepo.update_status(1, "ready", chunk_count=10)
    DocRepo.create("说明.md", "md")
    FeedbackRepo.insert("s1", "发球规则", "答", 1, None, "t1", 0)
    FeedbackRepo.insert("s2", "发球规则", "答", -1, "答案有误", "t2", 0)
    uid = UserRepo.create("u2", hash_password("pw"), role="user")
    conv = ConversationRepo.upsert(uid, "sess-1", "测试会话")
    MessageRepo.add(conv, "user", "发球规则")
    MessageRepo.add(conv, "assistant", "答案")

    resp = client.get("/admin/dashboard", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["documents"]["total"] == 2
    assert data["documents"]["by_type"]["pdf"] == 1
    assert data["documents"]["failed"] == 0
    assert data["activity"]["users"] == 2  # 管理员 + u2
    assert data["activity"]["messages"] == 2
    assert data["activity"]["messages_today"] == 2
    assert data["feedback"]["dislikes"] == 1
    assert isinstance(data["routes"], list)
    assert data["vectors"]["collections"] == 0


def test_dashboard_pending_corrections_shown():
    client = _new_client()
    uid = UserRepo.create("u3", hash_password("pw"), role="user")
    CorrectionRepo.create(uid, "doc_ref", "原文", "改后", "补正")
    data = client.get("/admin/dashboard", headers=_admin_headers()).json()["data"]
    assert data["todo"]["pending_corrections"] == 1


def test_health_probes_ok():
    client = _new_client()
    resp = client.get("/admin/health", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["degraded"] is False
    names = {i["name"] for i in data["items"]}
    assert names == {"数据库", "向量库", "百炼"}


def test_health_probes_degraded_on_error():
    client = _new_client(probes={"数据库": lambda: (_ for _ in ()).throw(RuntimeError("down"))})
    resp = client.get("/admin/health", headers=_admin_headers())
    data = resp.json()["data"]
    assert data["degraded"] is True
    assert data["failed"] == ["数据库"]
