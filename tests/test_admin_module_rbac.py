"""模块级权限（require_admin_module）测试：新管理模块按 users.permissions 放行/拒绝 + 旧 key 不适用。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_token, hash_password
from app.db.database import init_db, reset_db
from app.db.repos import UserRepo
from main import create_app

ALL_MODULES = ["dashboard", "kb", "rag", "review", "system"]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "admin.db")
    monkeypatch.setattr(get_settings(), "admin_api_key", "admin-key-1")
    reset_db()
    init_db()
    yield
    reset_db()


def _admin_headers(permissions=None) -> dict:
    existing = UserRepo.get_by_username("rbac_admin")
    uid = existing["id"] if existing else UserRepo.create("rbac_admin", hash_password("pw"), role="admin")
    if permissions is not None:
        UserRepo.set_permissions(uid, json.dumps(permissions, ensure_ascii=False))
    s = get_settings()
    token = create_token({"sub": str(uid), "role": "admin"}, s.auth_token_secret, 3600)
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    return TestClient(create_app())


def test_null_permissions_grants_all():
    client = _client()
    h = _admin_headers()  # permissions=NULL → 全部模块
    assert client.get("/admin/dashboard", headers=h).status_code == 200
    assert client.get("/admin/rag/settings", headers=h).status_code == 200
    assert client.get("/admin/corrections", headers=h).status_code == 200
    assert client.get("/admin/system", headers=h).status_code == 200


def test_empty_permissions_denies_all():
    client = _client()
    h = _admin_headers([])
    for path in ("/admin/dashboard", "/admin/rag/settings", "/admin/corrections", "/admin/system"):
        assert client.get(path, headers=h).status_code == 403, path


def test_partial_permissions_grant_only_module():
    client = _client()
    h = _admin_headers(["rag"])
    assert client.get("/admin/rag/settings", headers=h).status_code == 200
    assert client.get("/admin/dashboard", headers=h).status_code == 403
    assert client.get("/admin/corrections", headers=h).status_code == 403
    assert client.get("/admin/system", headers=h).status_code == 403


def test_new_modules_require_admin_jwt_not_shared_key():
    """新模块端点走严格管理员 JWT：旧 X-Admin-Key 不能访问（与 users 管理同语义）。"""
    client = _client()
    resp = client.get("/admin/rag/settings", headers={"X-Admin-Key": "admin-key-1"})
    assert resp.status_code == 401
    # 非管理员账户也 403
    uid = UserRepo.create("normal", hash_password("pw"), role="user")
    s = get_settings()
    token = create_token({"sub": str(uid), "role": "user"}, s.auth_token_secret, 3600)
    resp2 = client.get("/admin/rag/settings", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 403
