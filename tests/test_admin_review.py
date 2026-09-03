"""管理端内容审核测试：纠错工单（列表/审核/采纳通知）+ 低质量聚合（离线）。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_token, hash_password
from app.db.database import init_db, reset_db
from app.db.repos import CorrectionRepo, FeedbackRepo, NotificationRepo, UserRepo
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


def _client() -> TestClient:
    return TestClient(create_app())


def _make_user(username: str = "u1") -> int:
    return UserRepo.create(username, hash_password("pw"), role="user")


def test_corrections_list_and_filter():
    client = _client()
    h = _admin_headers()
    uid = _make_user("user-a")
    cid = CorrectionRepo.create(uid, "规则手册.pdf", "原文A", "改后A", "规则已更新")
    CorrectionRepo.create(uid, "战术.md", "原文B", "改后B", "战术过时")
    CorrectionRepo.update(cid, "accepted", "感谢补充")

    data = client.get("/admin/corrections", headers=h).json()["data"]
    assert data["total"] == 2
    pending = client.get("/admin/corrections", headers=h, params={"status": "pending"}).json()["data"]
    assert pending["total"] == 1
    assert pending["items"][0]["username"] == "user-a"
    # 非法 status → 422
    assert client.get("/admin/corrections", headers=h, params={"status": "x"}).status_code == 422


def test_correction_accept_notifies_submitter():
    client = _client()
    h = _admin_headers()
    uid = _make_user("user-b")
    cid = CorrectionRepo.create(uid, "doc_ref", "原文", "改后", "原因")
    resp = client.patch(f"/admin/corrections/{cid}", headers=h,
                        json={"status": "accepted", "admin_reply": "已核实采纳"})
    assert resp.status_code == 200
    assert resp.json()["data"]["notified"] is True
    assert CorrectionRepo.get_any(cid)["status"] == "accepted"
    notifs = NotificationRepo.list_user(uid)
    assert any("采纳" in n["title"] for n in notifs)


def test_correction_reject_and_discussion():
    client = _client()
    h = _admin_headers()
    uid = _make_user("user-c")
    cid = CorrectionRepo.create(uid, None, "原文", "改后", "原因")
    client.patch(f"/admin/corrections/{cid}", headers=h,
                 json={"status": "rejected", "admin_reply": "原文无误"})
    assert CorrectionRepo.get_any(cid)["status"] == "rejected"
    client.patch(f"/admin/corrections/{cid}", headers=h, json={"status": "discussion"})
    assert CorrectionRepo.get_any(cid)["status"] == "discussion"
    assert client.patch("/admin/corrections/99999", headers=h,
                        json={"status": "accepted"}).status_code == 404


def test_qc_bad_questions_aggregates():
    client = _client()
    h = _admin_headers()
    FeedbackRepo.insert("s1", "杀球要领", "答A", -1, "答案有误", "t1", 0)
    FeedbackRepo.insert("s2", "杀球要领", "答B", -1, "答非所问", "t2", 0)
    FeedbackRepo.insert("s3", "杀球要领", "答C", -1, None, "t3", 0)
    FeedbackRepo.insert("s4", "发球规则", "答D", 1, None, "t4", 0)
    data = client.get("/admin/qc/bad", headers=h).json()["data"]["items"]
    assert len(data) == 1
    assert data[0]["question"] == "杀球要领"
    assert data[0]["dislike_count"] == 3
    assert data[0]["last_comment"] is None  # 最近一次点踩（t3）无评论
    assert data[0]["last_trace_id"] == "t3"
