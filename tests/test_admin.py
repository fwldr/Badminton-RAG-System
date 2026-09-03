"""管理后台路由测试：鉴权、上传解析、列表、删除、重索引。"""

import pytest
from fastapi.testclient import TestClient

import app.api.routes.admin as admin_mod
from app.api.routes.admin import get_store_embedder
from app.core.config import get_settings
from app.db.database import reset_db
from app.db.repos import DocRepo
from app.ingest.embedder import FakeEmbedder
from app.ingest.store import VectorStore
from main import create_app


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "admin.db")
    monkeypatch.setattr(get_settings(), "admin_api_key", "admin-key-1")
    monkeypatch.setattr(get_settings(), "chroma_dir", tmp_path / "chroma_test")
    # 文件落盘隔离：避免测试写真实 data/uploads（doc_*/docs 副本）
    monkeypatch.setattr(admin_mod, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(get_settings(), "doc_images_dir", tmp_path / "uploads" / "docs")
    reset_db()
    yield
    reset_db()


def _headers():
    return {"X-Admin-Key": "admin-key-1"}


def _client() -> TestClient:
    app = create_app()
    # 注入 FakeEmbedder + 内存向量库（不触网）
    store = VectorStore()  # 内存版
    embedder = FakeEmbedder()

    def fake_se():
        return (store, embedder)

    app.dependency_overrides[get_store_embedder] = fake_se
    return TestClient(app)


def test_admin_requires_key():
    client = _client()
    assert client.get("/admin/documents").status_code == 401
    assert client.get("/admin/documents", headers={"X-Admin-Key": "bad"}).status_code == 401


def test_upload_rejects_bad_ext():
    client = _client()
    resp = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("evil.exe", b"data", "application/octet-stream")},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 42201


def test_upload_md_ok():
    client = _client()
    content = "# 羽毛球知识\n\n4U球拍约80-84克。\n\n5U球拍约75-79克。".encode("utf-8")
    resp = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("知识.md", content, "text/markdown")},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "ready"
    assert data["chunk_count"] >= 3

    docs = DocRepo.list_all()
    assert len(docs) == 1
    assert docs[0]["status"] == "ready"
    assert docs[0]["version"] == 1


def test_upload_csv_ok():
    client = _client()
    content = "名称,说明\n球速76,适合中温\n球速77,适合低温\n".encode("utf-8")
    resp = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("球速.csv", content, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["chunk_count"] == 2


def test_reindex_bumps_version():
    client = _client()
    content = "第一段。\n\n第二段。".encode("utf-8")
    up = client.post(
        "/admin/documents", headers=_headers(), files={"file": ("a.md", content, "text/markdown")}
    )
    doc_id = up.json()["data"]["id"]

    resp = client.post(f"/admin/documents/{doc_id}/reindex", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == 2

    docs = DocRepo.list_all()
    assert docs[0]["version"] == 2


def test_delete_removes_doc_and_collection():
    client = _client()
    content = "一段内容\n\n两段内容".encode("utf-8")
    up = client.post(
        "/admin/documents", headers=_headers(), files={"file": ("b.md", content, "text/markdown")}
    )
    doc_id = up.json()["data"]["id"]

    resp = client.delete(f"/admin/documents/{doc_id}", headers=_headers())
    assert resp.status_code == 200
    assert DocRepo.get(doc_id) is None

    # 再删一次 → 404
    resp = client.delete(f"/admin/documents/{doc_id}", headers=_headers())
    assert resp.status_code == 404
    assert resp.json()["code"] == 40401


def test_reindex_missing_file_conflict():
    client = _client()
    content = "一段内容".encode("utf-8")
    up = client.post(
        "/admin/documents", headers=_headers(), files={"file": ("c.md", content, "text/markdown")}
    )
    doc_id = up.json()["data"]["id"]

    # 模拟原文件丢失
    import app.api.routes.admin as admin_mod

    for p in admin_mod.UPLOAD_DIR.glob(f"doc_{doc_id}.*"):
        p.unlink()

    resp = client.post(f"/admin/documents/{doc_id}/reindex", headers=_headers())
    assert resp.status_code == 409
    assert resp.json()["code"] == 40901
