"""BM25 混合检索测试：红色手胶在纯向量下召回不到、BM25 混合可召回（对比卖点）。

用真实规格表数据 + FakeEmbedder + 内存库，不触网。
"""

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.ingest.embedder import FakeEmbedder
from app.ingest.pipeline import _build_metadata, load_rows
from app.ingest.serializer import SPEC_TABLES
from app.ingest.store import VectorStore
from app.rag.retriever import Retriever


@pytest.fixture(scope="module")
def store_embedder():
    """真实 5 张规格表数据灌入内存库（避免单一表被 max_per_table 上限截断）。"""
    settings = get_settings()
    store = VectorStore()
    embedder = FakeEmbedder()
    for spec in SPEC_TABLES:
        rows = load_rows(settings.processed_data_dir / spec.csv_file)
        ids = [f"{spec.name}:{i}" for i in range(len(rows))]
        docs = [spec.serializer(row) for row in rows]
        metas = [
            _build_metadata(row, spec.metadata_fields, Path(spec.csv_file).stem)
            for row in rows
        ]
        store.add(spec.name, ids, docs, metas, embedder.embed(docs))
    return store, embedder


QUESTION = "推荐一款红色的手胶"


def test_vector_mode_cannot_recall_red_grip(store_embedder):
    store, embedder = store_embedder
    hits = Retriever(store, embedder).retrieve(QUESTION, top_k=10)
    assert hits
    assert not any("GP203" in h.text for h in hits), "纯向量应召回不到 GP203"


def test_bm25_mode_recalls_red_grip(store_embedder):
    store, embedder = store_embedder
    hits = Retriever(store, embedder, use_bm25=True).retrieve(QUESTION, top_k=10)
    assert hits
    assert any("GP203" in h.text for h in hits), "BM25 混合应能召回含红色的 GP203"


def test_bm25_mode_main_result_respects_diversity(store_embedder):
    store, embedder = store_embedder
    hits = Retriever(store, embedder, use_bm25=True).retrieve(QUESTION, top_k=10)
    # 主结果（前 top_k 条，即无过滤时给 LLM 的部分）同一表最多 4 条
    main = hits[:10]
    grip_hits = [h for h in main if h.table == "grip_specs"]
    assert len(grip_hits) <= 4, "主结果同一 collection 最多保留 4 条"
