"""CLI 批量入库测试：目录收集、批量入库、文件 hash 幂等（离线）。"""

import fitz

from app.ingest.embedder import FakeEmbedder
from app.ingest.pipeline import collect_doc_files, ingest_documents
from app.ingest.store import VectorStore
from app.ingest.vision_embed import FakeVisionEmbedder


def _make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12, fontname="china-s")
    data = doc.tobytes()
    doc.close()
    return data


def test_collect_doc_files_recursive(tmp_path):
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    (pdf_dir / "a.pdf").write_bytes(_make_pdf("发球规则。"))
    (tmp_path / "b.txt").write_text("反手发球技术。", encoding="utf-8")
    (tmp_path / "notes.csv").write_text("名称,说明\n球速76,适合中温\n", encoding="utf-8")
    (tmp_path / "skip.log").write_text("x")
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)  # 图片扩展名应被收集

    files = collect_doc_files(tmp_path)
    names = {p.name for p in files}
    assert names == {"a.pdf", "b.txt", "notes.csv", "img.png"}


def test_ingest_documents_batch_and_idempotent(tmp_path):
    store = VectorStore()
    embedder = FakeEmbedder()
    p1 = tmp_path / "规则.pdf"
    p1.write_bytes(_make_pdf("发球规则：击球点不得高于腰部。"))
    p2 = tmp_path / "技术.md"
    p2.write_text("反手发球技术要点：握拍放松，手腕发力。", encoding="utf-8")

    summary = ingest_documents(store, embedder, [p1, p2])
    assert summary[str(p1)] >= 1
    assert summary[str(p2)] >= 1

    colls = store.list_collections()
    assert any(c.startswith("pdf_") for c in colls)
    assert any(c.startswith("doc_") for c in colls)
    total_before = sum(store.count(c) for c in colls)

    # 重复执行同文件 → 幂等跳过（chunk 数不翻倍）
    summary2 = ingest_documents(store, embedder, [p1, p2])
    assert str(p1) not in summary2 and str(p2) not in summary2
    total_after = sum(store.count(c) for c in store.list_collections())
    assert total_before == total_after


def test_ingest_documents_missing_dir_collects_empty(tmp_path):
    assert collect_doc_files(tmp_path / "nope") == []


def test_ingest_documents_refreshes_legacy_image_without_url(tmp_path):
    """旧图片数据（metadata 无「图片URL」）→ 带 image_dir 重跑时迁移重建而非跳过。"""
    store = VectorStore()
    embedder = FakeEmbedder()
    img = tmp_path / "动作分解图.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    # 旧格式入库：不传 image_dir（无展示链接）
    ingest_documents(store, embedder, [img], vision_embed=FakeVisionEmbedder(dim=4096))
    img_colls = [c for c in store.list_collections() if c.startswith("img_")]
    assert img_colls
    assert "图片URL" not in store.get_all(img_colls[0])[0]["metadata"]

    # 带 image_dir 重跑 → 检测到旧数据缺图片URL → 删除重建
    out = tmp_path / "docs"
    summary = ingest_documents(
        store, embedder, [img],
        vision_embed=FakeVisionEmbedder(dim=4096), image_dir=out,
    )
    assert str(img) in summary
    meta = store.get_all(img_colls[0])[0]["metadata"]
    assert meta["图片URL"].startswith("/uploads/docs/")
    assert (out / meta["图片URL"].rsplit("/", 1)[-1]).exists()

    # 已是最新格式 → 幂等跳过
    summary2 = ingest_documents(
        store, embedder, [img],
        vision_embed=FakeVisionEmbedder(dim=4096), image_dir=out,
    )
    assert str(img) not in summary2


def test_ingest_pdf_failed_for_scanned(tmp_path):
    """扫描版 PDF 批量入库 → 不产生 collection（failed 计 0 chunk）。"""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(72, 72, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    data = doc.tobytes()
    doc.close()
    store = VectorStore()
    p = tmp_path / "scan.pdf"
    p.write_bytes(data)
    summary = ingest_documents(store, FakeEmbedder(), [p])
    assert summary[str(p)] == 0
    assert not any(c.startswith("pdf_") for c in store.list_collections())
