"""管理端 PDF/图片上传测试：白名单扩展、OCR 注入、上限配置（离线）。

沿用 test_admin.py 套路：monkeypatch settings 到 tmp_path + dependency_overrides
（内存向量库 + FakeEmbedder + FakeOcrEngine）。
"""

import fitz
import pytest
from fastapi.testclient import TestClient

import app.api.routes.admin as admin_mod
from app.api.routes.admin import get_ocr_engine, get_store_embedder, get_vision_embedder
from app.core.config import get_settings
from app.db.database import reset_db
from app.db.repos import DocRepo
from app.ingest.embedder import FakeEmbedder
from app.ingest.ocr import FakeOcrEngine
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


def _client(ocr: FakeOcrEngine | None = None) -> TestClient:
    app = create_app()
    store = VectorStore()  # 内存版
    embedder = FakeEmbedder()
    app.dependency_overrides[get_store_embedder] = lambda: (store, embedder)
    app.dependency_overrides[get_ocr_engine] = lambda: ocr
    # 覆盖多模态依赖：避免读到 .env 的 VISION_EMBED_ENABLED=true 触发真实 SiliconFlow API
    app.dependency_overrides[get_vision_embedder] = lambda: None
    return TestClient(app)


def _make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12, fontname="china-s")
    data = doc.tobytes()
    doc.close()
    return data


def test_upload_pdf_ok():
    client = _client()
    content = _make_pdf("发球规则：击球点不得高于腰部。")
    resp = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("规则手册.pdf", content, "application/pdf")},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "ready"
    assert data["chunk_count"] >= 1

    doc = DocRepo.list_all()[0]
    assert doc["doc_type"] == "pdf"
    assert doc["status"] == "ready"


def test_upload_scanned_pdf_failed():
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(72, 72, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    data = doc.tobytes()
    doc.close()
    client = _client()
    resp = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("扫描版.pdf", data, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert "无文字层" in (DocRepo.get(body["id"])["error_msg"] or "")


def test_upload_image_with_fake_ocr_ok():
    client = _client(ocr=FakeOcrEngine("球拍握把：G4 中等手型，G5 偏小手型"))
    content = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    resp = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("握把.png", content, "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "ready"
    assert data["chunk_count"] >= 1
    doc = DocRepo.list_all()[0]
    assert doc["doc_type"] == "png"


def test_upload_image_without_ocr_failed():
    client = _client(ocr=None)
    content = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    resp = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("a.png", content, "image/png")},
    )
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert "OCR" in (DocRepo.get(body["id"])["error_msg"] or "")


def test_upload_over_limit_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_max_size", 100)  # 100 字节上限
    client = _client()
    resp = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("big.md", b"x" * 200, "text/markdown")},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 42201


def test_upload_pdf_reindex_bumps_version():
    client = _client()
    content = _make_pdf("发球规则：击球点不得高于腰部。")
    up = client.post(
        "/admin/documents",
        headers=_headers(),
        files={"file": ("规则.pdf", content, "application/pdf")},
    )
    doc_id = up.json()["data"]["id"]
    resp = client.post(f"/admin/documents/{doc_id}/reindex", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == 2
