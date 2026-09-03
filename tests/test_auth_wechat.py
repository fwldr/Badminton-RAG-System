"""微信小程序登录测试：code2session → openid 绑定/建号 → 签发 token（全部离线，monkeypatch 注入）。"""

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
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "wechat.db")
    # 只改真实 settings 字段（整体替换会导致 token 签发/解密的 secret 不一致而 401）
    monkeypatch.setattr(get_settings(), "wx_appid", "wx-test-appid")
    monkeypatch.setattr(get_settings(), "wx_secret", "wx-test-secret")
    reset_db()
    # 假 code2session：固定 openid，不触网
    monkeypatch.setattr(auth_mod, "wx_code2session", lambda code: {"openid": "openid-abc123"})
    yield
    reset_db()


def _client() -> TestClient:
    return TestClient(create_app())


def test_wechat_login_creates_and_reuses_user():
    client = _client()
    resp = client.post("/auth/wechat", json={"code": "wx-code-1", "nickname": "羽球小将"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["is_new"] is True
    assert data["token"]
    assert data["user"]["role"] == "user"
    assert data["user"]["nickname"] == "羽球小将"
    assert data["user"]["username"].startswith("wx_")

    # 同一 code（同 openid）再登录 → 复用账号，is_new=False
    resp2 = client.post("/auth/wechat", json={"code": "wx-code-1"})
    assert resp2.status_code == 200
    data2 = resp2.json()["data"]
    assert data2["is_new"] is False
    assert data2["user"]["id"] == data["user"]["id"]


def test_wechat_login_persists_and_lookup():
    client = _client()
    client.post("/auth/wechat", json={"code": "wx-code-2"})
    user = UserRepo.get_by_openid("openid-abc123")
    assert user is not None
    assert user["openid"] == "openid-abc123"


def test_wechat_login_bad_code_401(monkeypatch):
    monkeypatch.setattr(
        auth_mod, "wx_code2session", lambda code: {"errcode": 40029, "errmsg": "invalid code"}
    )
    client = _client()
    resp = client.post("/auth/wechat", json={"code": "wx-code-bad"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101
    assert "微信登录失败" in resp.json()["message"]


def test_wechat_login_unconfigured_500(monkeypatch):
    monkeypatch.setattr(get_settings(), "wx_appid", "")
    monkeypatch.setattr(get_settings(), "wx_secret", "")
    client = _client()
    resp = client.post("/auth/wechat", json={"code": "wx-code-1"})
    assert resp.status_code == 500
    assert "未配置" in resp.json()["message"]


def test_wechat_login_service_error_500(monkeypatch):
    def boom(_code):  # noqa: ANN001
        raise RuntimeError("network down")

    monkeypatch.setattr(auth_mod, "wx_code2session", boom)
    client = _client()
    resp = client.post("/auth/wechat", json={"code": "wx-code-1"})
    assert resp.status_code == 500
    assert "服务不可用" in resp.json()["message"]


def test_wx_user_cannot_password_login():
    client = _client()
    client.post("/auth/wechat", json={"code": "wx-code-1"})
    # 微信账号无可用密码：任意密码登录必须 401（随机哈希，不泄露）
    resp = client.post("/auth/login", json={"username": "wx_openid-abc123", "password": "guess-me-123"})
    assert resp.status_code == 401


def test_wechat_login_requires_code():
    client = _client()
    resp = client.post("/auth/wechat", json={"code": ""})
    assert resp.status_code == 422
