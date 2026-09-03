"""文档元数据打标测试：PATCH tags → documents 表 + Chroma metadata 同步（离线）。"""

import pytest
from fastapi.testclient import TestClient

from app.api.routes.admin import get_ocr_engine, get_store_embedder, get_vision_embedder
from app.core.config import get_settings
from app.core.security import create_token, hash_password
from app.db.database import init_db, reset_db
from app.db.repos import DocRepo, UserRepo
from app.ingest.embedder import FakeEmbedder
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


def _admin_headers() -> dict:
    existing = UserRepo.get_by_username("admin_t")
    uid = existing["id"] if existing else UserRepo.create("admin_t", hash_password("pw"), role="admin")
    s = get_settings()
    token = create_token({"sub": str(uid), "role": "admin"}, s.auth_token_secret, 3600)
    return {"Authorization": f"Bearer {token}"}


def _client() -> tuple[TestClient, VectorStore]:
    app = create_app()
    store = VectorStore()  # 内存版
    embedder = FakeEmbedder()
    app.dependency_overrides[get_store_embedder] = lambda: (store, embedder)
    app.dependency_overrides[get_ocr_engine] = lambda: None
    app.dependency_overrides[get_vision_embedder] = lambda: None
    return TestClient(app), store


def test_patch_doc_tags_syncs_db_and_chroma():
    client, store = _client()
    doc_id = DocRepo.create("规则手册.pdf", "pdf")
    # 模拟已入库：doc_{id} collection 有记录
    store.add(
        f"doc_{doc_id}",
        [f"doc_{doc_id}:0", f"doc_{doc_id}:1"],
        ["块一：发球规则", "块二：双打规则"],
        [{"文件名": "规则手册.pdf"}, {"文件名": "规则手册.pdf"}],
        FakeEmbedder().embed(["块一：发球规则", "块二：双打规则"]),
    )

    resp = client.patch(
        f"/admin/documents/{doc_id}/tags",
        headers=_admin_headers(),
        json={"tags": ["规则类", "2024赛事"]},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["tags"] == ["规则类", "2024赛事"]
    # DB
    assert DocRepo.get(doc_id)["tags"] == "规则类,2024赛事"
    # Chroma metadata 全部记录同步
    hits = store.get_all(f"doc_{doc_id}")
    for h in hits:
        assert h["metadata"]["tags"] == "规则类,2024赛事"
    assert h["metadata"]["文件名"] == "规则手册.pdf"  # 原有字段保留（合并更新）


def test_patch_doc_tags_clear_and_404():
    client, store = _client()
    doc_id = DocRepo.create("a.csv", "csv")
    store.add(f"doc_{doc_id}", [f"doc_{doc_id}:0"], ["表头:值"],
              [{"文件名": "a.csv"}], FakeEmbedder().embed(["表头:值"]))
    client.patch(f"/admin/documents/{doc_id}/tags", headers=_admin_headers(), json={"tags": ["x"]})
    resp = client.patch(f"/admin/documents/{doc_id}/tags", headers=_admin_headers(), json={"tags": []})
    assert resp.json()["data"]["tags"] == []
    assert DocRepo.get(doc_id)["tags"] == ""
    assert store.get_all(f"doc_{doc_id}")[0]["metadata"]["tags"] == ""
    assert client.patch("/admin/documents/99999/tags",
                        headers=_admin_headers(), json={"tags": ["x"]}).status_code == 404
