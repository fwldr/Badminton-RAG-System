"""向量库（内存 Chroma）单元测试。"""

from app.ingest.store import VectorStore


def test_add_query_count():
    store = VectorStore()
    store.add(
        "racket_specs",
        ids=["r0", "r1"],
        documents=["尤尼克斯 YONEX 天斧99", "李宁 LINING 雷霆90"],
        metadatas=[
            {"品牌": "尤尼克斯 YONEX", "型号": "天斧99"},
            {"品牌": "李宁 LINING", "型号": "雷霆90"},
        ],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
    )
    assert store.count("racket_specs") == 2

    hits = store.query("racket_specs", [1.0, 0.0], n_results=2)
    assert len(hits) == 2
    # 余弦距离升序：最相似（[1,0]）在前
    assert hits[0]["document"] == "尤尼克斯 YONEX 天斧99"
    assert hits[0]["metadata"]["品牌"] == "尤尼克斯 YONEX"
    assert hits[0]["distance"] <= hits[1]["distance"]


def test_query_empty_collection_returns_empty():
    store = VectorStore()
    assert store.query("grip_specs", [0.1, 0.2], n_results=5) == []


def test_upsert_overwrites():
    store = VectorStore()
    store.add("string_specs", ["s0"], ["旧文档"], [{"品牌": "A"}], [[1.0]])
    store.add("string_specs", ["s0"], ["新文档"], [{"品牌": "B"}], [[1.0]])
    assert store.count("string_specs") == 1
    hits = store.query("string_specs", [1.0], n_results=1)
    assert hits[0]["document"] == "新文档"


def test_reset_clears_all():
    store = VectorStore()
    store.add("racket_specs", ["r0"], ["doc"], [{"x": "y"}], [[1.0]])
    store.reset()
    assert store.count("racket_specs") == 0
