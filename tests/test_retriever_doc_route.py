"""文档类 collection 检索测试：doc_/pdf_ 纳入向量检索与 BM25 指纹重建（离线）。"""

from app.ingest.doc_ingest import ingest_document
from app.ingest.embedder import FakeEmbedder
from app.ingest.ocr import FakeOcrEngine
from app.ingest.store import VectorStore
from app.rag.retriever import Retriever, document_text_collections


def _seed_doc_store() -> tuple[VectorStore, FakeEmbedder]:
    store = VectorStore()  # 内存版
    embedder = FakeEmbedder()
    ingest_document(
        "反手发球规则：击球点不得高于腰部。".encode("utf-8"),
        "发球规则.md", 1, store, embedder,
    )
    return store, embedder


def test_document_text_collections_filters_prefixes():
    store, _ = _seed_doc_store()
    colls = document_text_collections(store)
    assert "doc_1" in colls
    assert all(not c.startswith("img_") for c in colls)


def test_retrieve_includes_doc_collection():
    store, embedder = _seed_doc_store()
    retriever = Retriever(store, embedder, use_bm25=True)
    records = retriever.retrieve("反手发球击球点", top_k=10, per_table_k=4)
    doc_hits = [r for r in records if r.table == "doc_1"]
    assert doc_hits
    assert "发球" in doc_hits[0].text


def test_bm25_includes_doc_collection_and_rebuilds():
    store, embedder = _seed_doc_store()
    retriever = Retriever(store, embedder, use_bm25=True)
    # 首次构建：doc_1 进 BM25（精确词「腰部」应命中）
    hits = retriever.retrieve("腰部", top_k=10, per_table_k=4)
    assert any(r.table == "doc_1" for r in hits)

    # 新增文档 → 指纹变化 → BM25 重建（新 collection 内容可被词法召回）
    ingest_document(
        "球线直径：0.68mm 属于细线。".encode("utf-8"),
        "球线.md", 2, store, embedder,
    )
    hits2 = retriever.retrieve("0.68mm", top_k=10, per_table_k=4)
    assert any(r.table == "doc_2" for r in hits2)


def test_rag_search_document_expands_doc_collections():
    store, embedder = _seed_doc_store()
    from app.agent.tools import rag_search

    retriever = Retriever(store, embedder)
    results = rag_search("反手发球规则", retriever, "document", top_k=5)
    assert results
    assert all(r["table"].startswith("doc_") for r in results)


def test_rag_search_document_includes_img_collections():
    """多模态 img_ collection（SiliconFlow 独立空间）也进 document 路由检索。"""
    from app.ingest.doc_ingest import ingest_document
    from app.ingest.vision_embed import FakeVisionEmbedder
    from app.agent.tools import rag_search

    store = VectorStore()
    embedder = FakeEmbedder()
    # 视觉向量经 SiliconFlow（与文本不同空间）：img_* 存储用 64 维假向量，
    # 查询时用同一个 vision_embed.embed_text（64 维）检索，故与文本查询向量分开
    vision = FakeVisionEmbedder(dim=64)
    ingest_document(
        b"\x89PNG\r\n\x1a\n" + b"9" * 64, "反手发球动作图.png", 1, store, embedder,
        ocr=FakeOcrEngine(""), vision_embed=vision, ocr_min_chars=20,
    )
    retriever = Retriever(store, embedder)
    results = rag_search("反手发球动作图", retriever, "document", top_k=5, vision_embed=vision)
    assert results
    assert any(r["table"].startswith("img_") for r in results)
