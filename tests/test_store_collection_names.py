"""中文表名 ↔ Chroma collection 名映射测试（离线：内存版 VectorStore）。

Chroma（rust 内核）collection 仅允许 [A-Za-z0-9._-] 且 3-512 位、首尾字母数字；
SPEC_TABLES / KNOWLEDGE_TABLES 表名为中文（面向用户），由 `collection_name()` 映射为
英文 collection 名，与 data/chroma 既有数据（英文名）对齐：重入库幂等、不产生双份。
"""

import pytest

from app.ingest.store import VectorStore, collection_name, display_name


def test_collection_name_mapping():
    assert collection_name("球拍") == "racket_specs"
    assert collection_name("BWF官方规则") == "bwf_rules"
    assert collection_name("racket_specs") == "racket_specs"  # 合法英文名直通
    assert collection_name("pdf_a1b2c3d4") == "pdf_a1b2c3d4"  # 文档 collection 直通
    with pytest.raises(ValueError):
        collection_name("未知的表")  # 未注册中文名 → 明确报错（提示维护映射）
    assert display_name("racket_specs") == "球拍"
    assert display_name("bwf_rules") == "BWF官方规则"
    assert display_name("pdf_x") == "pdf_x"  # 未注册（文档类）原样返回


def test_store_add_query_chinese_table():
    store = VectorStore()
    store.add(
        "球拍",
        ["racket_specs:0", "racket_specs:1"],
        ["记录1", "记录2"],
        [{"品牌": "A"}, {"品牌": "B"}],
        [[1.0, 0.0], [0.0, 1.0]],
    )
    assert "racket_specs" in store.list_collections()
    assert store.count("球拍") == 2
    assert store.count("racket_specs") == 2  # 中文与英文名指向同一 collection
    hits = store.query("球拍", [1.0, 0.0], n_results=10)
    assert len(hits) == 2
    # 删除走映射
    store.delete_collection("球拍")
    assert store.count("racket_specs") == 0


def test_store_upsert_idempotent_with_legacy_ids():
    store = VectorStore()
    # 旧数据 id 前缀为英文（racket_specs:0）；pipeline 重入库用 collection_name 生成同前缀
    store.add("球拍", ["racket_specs:0"], ["旧文档"], [{"品牌": "A"}], [[1.0]])
    store.add("球拍", ["racket_specs:0"], ["新文档"], [{"品牌": "B"}], [[1.0]])
    assert store.count("球拍") == 1  # 同名同 id upsert：不产生双份
    assert store.query("球拍", [1.0], n_results=1)[0]["document"] == "新文档"
