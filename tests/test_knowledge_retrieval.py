"""知识表检索集成测试：FakeEmbedder + 内存 Chroma（不触网）。

验证知识表已进入检索范围：毛片类问题命中 feather_type，场地/规则类问题命中 bwf_rules；
并验证来源解析：知识表 → 表名 + 主题名，规格表 → 品牌 + 型号。
"""

from pathlib import Path

from app.core.config import get_settings
from app.ingest.embedder import FakeEmbedder
from app.ingest.pipeline import _build_metadata, load_rows
from app.ingest.serializer import KNOWLEDGE_TABLES
from app.ingest.store import VectorStore
from app.rag.retriever import Retriever, resolve_source

_TABLES = {t.name: t for t in KNOWLEDGE_TABLES}


def _seed(store: VectorStore, embedder: FakeEmbedder, names: list[str]) -> None:
    settings = get_settings()
    for name in names:
        table = _TABLES[name]
        rows = load_rows(settings.processed_data_dir / table.csv_file)
        ids, docs, metas = [], [], []
        for i, row in enumerate(rows):
            ids.append(f"{table.name}:{i}")
            docs.append(table.serializer(row))
            metas.append(_build_metadata(row, table.metadata_fields, Path(table.csv_file).stem))
        store.add(table.name, ids, docs, metas, embedder.embed(docs))


def test_feather_query_hits_feather_type():
    store = VectorStore()
    embedder = FakeEmbedder()
    _seed(store, embedder, ["毛片类型", "BWF官方规则", "战术"])
    hits = Retriever(store, embedder).retrieve("鹅毛鸭毛耐打", top_k=5)
    assert hits, "应检索到知识表记录"
    assert hits[0].table == "毛片类型"


def test_court_query_hits_bwf_rules():
    store = VectorStore()
    embedder = FakeEmbedder()
    _seed(store, embedder, ["毛片类型", "BWF官方规则", "战术"])
    hits = Retriever(store, embedder).retrieve("单双打场地", top_k=5)
    assert hits, "应检索到知识表记录"
    assert any(h.table == "BWF官方规则" for h in hits[:3]), "场地类问题应命中 BWF官方规则"


def test_resolve_source_knowledge_and_spec():
    # 知识表：表名 + 主题名（metadata 首列）
    brand, model = resolve_source(
        {"table": "feather_type", "metadata": {"毛片名称": "鹅刀翎(鹅全圆)", "来源文件": "毛片类型"}}
    )
    assert brand == "毛片类型"
    assert model == "鹅刀翎(鹅全圆)"
    # 规格表：品牌 + 型号
    brand, model = resolve_source(
        {"table": "racket_specs", "metadata": {"品牌": "尤尼克斯 YONEX", "型号": "天斧99"}}
    )
    assert brand == "尤尼克斯 YONEX"
    assert model == "天斧99"


def test_resolve_source_independent_of_metadata_key_order():
    # Chroma 返回的 metadata dict 键顺序不保证为插入顺序，主题名须按注册表字段名取，
    # 而非 dict 首键（这里把 来源文件 放在前面模拟乱序）。
    brand, model = resolve_source(
        {"table": "feather_type", "metadata": {"来源文件": "毛片类型", "毛片名称": "鸭中方"}}
    )
    assert brand == "毛片类型"
    assert model == "鸭中方"
