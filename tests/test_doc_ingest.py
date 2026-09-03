"""文档入库测试：txt/md/csv 分块入库、重索引、删除（FakeEmbedder + 内存库）。"""

import pytest

from app.ingest.doc_ingest import ingest_document, _split_text, _parse_csv
from app.ingest.embedder import FakeEmbedder
from app.ingest.store import VectorStore


def _store():
    return VectorStore()  # 内存版，每个实例隔离


def test_split_text_paragraphs():
    text = "第一段。\n\n第二段内容较长" + "字" * 250 + "。\n\n第三段"
    blocks = _split_text(text)
    assert len(blocks) >= 3
    assert all(len(b) <= 200 for b in blocks)


def test_parse_csv_generic():
    data = "品牌,型号,重量\n李宁,GP203,4U\n威克多,某型号,5U\n".encode("utf-8")
    rows = _parse_csv(data)
    assert len(rows) == 2
    assert "品牌:李宁" in rows[0]
    assert "重量:4U" in rows[0]


def test_ingest_md_ready():
    store = _store()
    emb = FakeEmbedder()
    md = "# 测试文档\n\n羽毛球拍重量4U约80-84克。\n\n5U约75-79克。".encode("utf-8")
    status, count, err = ingest_document(md, "test.md", 1, store, emb)
    assert status == "ready"
    assert count == 3  # 标题 + 2 段
    assert store.count("doc_1") == count


def test_ingest_csv_ready():
    store = _store()
    emb = FakeEmbedder()
    csv_bytes = "名称,说明\n球速76,适合中温\n球速77,适合低温\n".encode("utf-8")
    status, count, err = ingest_document(csv_bytes, "data.csv", 2, store, emb)
    assert status == "ready"
    assert count == 2


def test_ingest_empty_failed():
    store = _store()
    emb = FakeEmbedder()
    status, count, err = ingest_document(b"", "empty.txt", 3, store, emb)
    assert status == "failed"
    assert count == 0


def test_reindex_replaces_collection():
    store = _store()
    emb = FakeEmbedder()
    v1 = "旧内容A\n\n旧内容B".encode("utf-8")
    ingest_document(v1, "doc.md", 5, store, emb)
    assert store.count("doc_5") == 2

    v2 = "新内容X\n\n新内容Y\n\n新内容Z".encode("utf-8")
    status, count, _ = ingest_document(v2, "doc.md", 5, store, emb)  # 重入（覆盖）
    assert status == "ready"
    assert count == 3
    assert store.count("doc_5") == 3
    # 旧内容不再存在
    docs = store.get_all("doc_5")
    assert not any("旧内容" in d["document"] for d in docs)


def test_delete_collection():
    store = _store()
    emb = FakeEmbedder()
    ingest_document("内容\n\n更多".encode("utf-8"), "doc.md", 7, store, emb)
    assert store.count("doc_7") == 2
    store.delete_collection("doc_7")
    assert "doc_7" not in store.list_collections()
