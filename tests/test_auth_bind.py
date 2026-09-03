"""账号绑定/解绑测试：微信手机号绑定（code 注入不触网）、解绑微信/手机号、绑定状态暴露。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_mod
from app.core.config import get_settings
from app.db.database import reset_db
from app.db.repos import UserRepo
from main import create_app


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "bind.db")
    # 只改真实 settings 的字段（不要整体替换 get_settings：否则 token 签发用假 secret、
    # deps.get_current_user 用真 secret 解密必然 401）
    monkeypatch.setattr(get_settings(), "wx_appid", "wx-test-appid")
    monkeypatch.setattr(get_settings(), "wx_secret", "wx-test-secret")
    reset_db()
    monkeypatch.setattr(auth_mod, "wx_code2session", lambda code: {"openid": "openid-abc123"})
    yield
    reset_db()


def _client() -> TestClient:
    return TestClient(create_app())


def _register(client, username="user1", password="secret123"):
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["data"]["token"]


def test_bind_phone_ok(monkeypatch):
    monkeypatch.setattr(auth_mod, "wx_get_phone_number", lambda code: "13800000001")
    client = _client()
    token = _register(client)
    h = {"Authorization": f"Bearer {token}"}

    resp = client.post("/auth/wechat/phone", headers=h, json={"code": "phone-code-1"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["phone_bound"] is True
    assert data["user"]["phone_bound"] is True

    assert UserRepo.get_by_phone("13800000001") is not None


def test_bind_phone_conflict_409(monkeypatch):
    monkeypatch.setattr(auth_mod, "wx_get_phone_number", lambda code: "13900000001")
    client = _client()
    t1 = _register(client, "user1")
    client.post("/auth/wechat/phone", headers={"Authorization": f"Bearer {t1}"}, json={"code": "c1"})

    t2 = _register(client, "user2")
    resp = client.post("/auth/wechat/phone", headers={"Authorization": f"Bearer {t2}"}, json={"code": "c2"})
    assert resp.status_code == 409
    assert "已绑定其他账号" in resp.json()["message"]


def test_unbind_phone(monkeypatch):
    monkeypatch.setattr(auth_mod, "wx_get_phone_number", lambda code: "13700000001")
    client = _client()
    token = _register(client)
    h = {"Authorization": f"Bearer {token}"}
    client.post("/auth/wechat/phone", headers=h, json={"code": "c1"})

    resp = client.post("/auth/unbind", headers=h, json={"type": "phone"})
    assert resp.status_code == 200
    assert resp.json()["data"]["phone_bound"] is False
    assert UserRepo.get_by_phone("13700000001") is None


def test_unbind_wechat():
    client = _client()
    # 微信登录建号
    login = client.post("/auth/wechat", json={"code": "wx-code"})
    assert login.status_code == 200
    token = login.json()["data"]["token"]
    h = {"Authorization": f"Bearer {token}"}

    me = client.get("/auth/me", headers=h).json()["data"]
    assert me["wx_bound"] is True

    resp = client.post("/auth/unbind", headers=h, json={"type": "wechat"})
    assert resp.status_code == 200
    assert resp.json()["data"]["wx_bound"] is False
    assert UserRepo.get_by_openid("openid-abc123") is None


def test_me_exposes_bind_flags():
    client = _client()
    token = _register(client)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["data"]
    assert me["wx_bound"] is False
    assert me["phone_bound"] is False
