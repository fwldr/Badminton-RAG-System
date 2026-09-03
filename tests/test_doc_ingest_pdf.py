"""PDF 文档入库单元测试（离线：fitz 生成最小 PDF + FakeEmbedder + 内存向量库）。"""

import fitz
import pytest

from app.ingest.doc_ingest import _chunk_text, _parse_pdf, ingest_document
from app.ingest.embedder import FakeEmbedder
from app.ingest.store import VectorStore


def _make_pdf(*pages_text: str) -> bytes:
    """用 fitz 生成最小 PDF（china-s 内嵌 CJK 字体，支持中文）。"""
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12, fontname="china-s")
    data = doc.tobytes()
    doc.close()
    return data


def _make_long_pdf(lines: int = 40, line_len: int = 40) -> bytes:
    """多行文本页（insert_text 单行会按页宽截断，超长文档用逐行插入构造）。"""
    doc = fitz.open()
    page = doc.new_page()
    for i in range(lines):
        page.insert_text((72, 72 + i * 16), "A" * line_len, fontsize=12, fontname="china-s")
    data = doc.tobytes()
    doc.close()
    return data


def test_parse_pdf_extracts_text_with_page_no():
    pdf = _make_pdf("发球时，击球点不得高于腰部。", "接发球时，双脚不得提前移动。")
    blocks = _parse_pdf(pdf)
    assert blocks
    pages = {p for _, p in blocks}
    assert pages == {1, 2}
    texts = "".join(t for t, _ in blocks)
    assert "发球" in texts
    assert "接发球" in texts


def test_parse_pdf_returns_empty_for_no_text_layer():
    """扫描版（无文字层）→ 空列表，调用方判 failed。"""
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(72, 72, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    data = doc.tobytes()
    doc.close()
    assert _parse_pdf(data) == []


def test_ingest_pdf_document_ok():
    store = VectorStore()  # 内存版
    embedder = FakeEmbedder()
    pdf = _make_pdf("规则：发球时击球点不得高于腰部。")
    status, count, err = ingest_document(pdf, "规则手册.pdf", 1, store, embedder)
    assert status == "ready"
    assert count >= 1
    hits = store.get_all("doc_1")
    assert hits
    meta = hits[0]["metadata"]
    assert meta["source_type"] == "pdf"
    assert meta["page_no"] == 1
    assert meta["file_hash"]


def test_ingest_pdf_failed_no_text_layer():
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(72, 72, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    data = doc.tobytes()
    doc.close()
    store = VectorStore()
    status, count, err = ingest_document(data, "扫描版.pdf", 1, store, FakeEmbedder())
    assert status == "failed"
    assert "无文字层" in (err or "")


def test_ingest_pdf_respects_chunk_size():
    pdf = _make_long_pdf(lines=40, line_len=40)  # 单页 ~1600 字符
    store = VectorStore()
    status, count, err = ingest_document(
        pdf, "long.pdf", 1, store, FakeEmbedder(), chunk_size=100, chunk_overlap=20
    )
    assert status == "ready"
    assert count >= 10
    metas = [h["metadata"] for h in store.get_all("doc_1")]
    assert all(m["page_no"] == 1 for m in metas)


def test_chunk_text_window_overlap():
    text = "X" * 300
    chunks = _chunk_text(text, chunk_size=100, chunk_overlap=20)
    assert len(chunks) == 4
    # 相邻块应有 20 字符重叠
    assert chunks[0][-20:] == chunks[1][:20]


def test_chunk_text_paragraph_kept_short():
    assert _chunk_text("短段落。", 100, 10) == ["短段落。"]
