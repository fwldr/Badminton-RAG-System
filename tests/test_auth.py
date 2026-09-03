"""双角色账户体系与 RBAC 测试：注册 / 登录 / 当前用户 / 种子管理员 / 用户与权限管理。

全部离线：临时 SQLite 库 + 不触网（依赖注入/内存）。
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.database import reset_db
from main import _seed_bootstrap_admin, create_app


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "auth.db")
    monkeypatch.setattr(get_settings(), "admin_api_key", "admin-key-1")
    reset_db()
    yield
    reset_db()


def _client() -> TestClient:
    return TestClient(create_app())


def _register(client, username="player1", password="secret123", nickname=None):
    body = {"username": username, "password": password}
    if nickname is not None:
        body["nickname"] = nickname
    return client.post("/auth/register", json=body)


def _login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_register_creates_user():
    client = _client()
    resp = _register(client, "player1", "secret123", "小明")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["user"]["username"] == "player1"
    assert data["user"]["role"] == "user"
    assert data["user"]["nickname"] == "小明"
    assert "password" not in data["user"] and "password_hash" not in data["user"]
    assert data["token"]  # 注册即返回 token


def test_register_duplicate_409():
    client = _client()
    _register(client, "player1", "secret123")
    resp = _register(client, "player1", "other456")
    assert resp.status_code == 409
    assert resp.json()["code"] == 40901


def test_login_ok_and_me():
    client = _client()
    _register(client, "player1", "secret123")
    resp = _login(client, "player1", "secret123")
    assert resp.status_code == 200
    token = resp.json()["data"]["token"]
    assert resp.json()["data"]["user"]["role"] == "user"

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["username"] == "player1"


def test_login_wrong_password_401():
    client = _client()
    _register(client, "player1", "secret123")
    resp = _login(client, "player1", "wrongpass")
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101


def test_login_disabled_403(monkeypatch):
    client = _client()
    uid = _register(client, "player1", "secret123").json()["data"]["user"]["id"]
    # 直接禁用账号后登录应拒绝
    from app.db.repos import UserRepo

    UserRepo.set_active(uid, False)
    resp = _login(client, "player1", "secret123")
    assert resp.status_code == 403
    assert resp.json()["code"] == 40301


def test_me_requires_token_401():
    client = _client()
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer bad.token"}).status_code == 401


def _seed_admin():
    monkey_settings = get_settings()
    monkey_settings.bootstrap_admin_username = "rootadmin"
    monkey_settings.bootstrap_admin_password = "admin-secret-1"
    _seed_bootstrap_admin()


def test_bootstrap_admin_seeded_and_login():
    client = _client()
    _seed_admin()
    resp = _login(client, "rootadmin", "admin-secret-1")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["user"]["role"] == "admin"
    assert data["user"]["username"] == "rootadmin"

    # 种子幂等：再次 seed 不重复创建
    _seed_admin()
    from app.db.repos import UserRepo

    assert UserRepo.count() == 1


def test_admin_users_requires_admin_jwt():
    client = _client()
    _seed_admin()
    user_token = _register(client, "player1", "secret123").json()["data"]["token"]
    admin_token = _login(client, "rootadmin", "admin-secret-1").json()["data"]["token"]

    # 无 token → 401
    assert client.get("/admin/users").status_code == 401
    # 普通用户 token → 403（角色不足）
    resp = client.get("/admin/users", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 403
    assert resp.json()["code"] == 40301
    # 管理员 token → 200，列出用户
    resp = client.get("/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 2


def test_admin_users_patch_role():
    client = _client()
    _seed_admin()
    admin_token = _login(client, "rootadmin", "admin-secret-1").json()["data"]["token"]
    uid = _register(client, "player1", "secret123").json()["data"]["user"]["id"]
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 升级为管理员
    r = client.patch(f"/admin/users/{uid}", json={"role": "admin"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "admin"
    # 授予模块权限
    r = client.patch(f"/admin/users/{uid}", json={"permissions": ["content_review"]}, headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["permissions"] == '["content_review"]'
    # 禁用
    r = client.patch(f"/admin/users/{uid}", json={"is_active": False}, headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["is_active"] == 0


def test_admin_users_rejects_legacy_key():
    """用户与权限管理是严格管理员账户才能进；仅旧 X-Admin-Key 无法访问。"""
    client = _client()
    assert client.get("/admin/users", headers={"X-Admin-Key": "admin-key-1"}).status_code == 401


def test_legacy_key_still_works_for_documents():
    """既有管理端点向后兼容：旧 X-Admin-Key 仍可通过文档管理。"""
    client = _client()
    resp = client.get("/admin/documents", headers={"X-Admin-Key": "admin-key-1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["documents"] == []
