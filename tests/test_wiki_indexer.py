"""Wiki 索引层测试（全离线：FakeEmbedder + 内存 VectorStore，不触百炼 API）。

重点是「按 digest 增量重嵌」与「陈旧 id 清理」——几千段全量重嵌按量计费不便宜，
重编译后只应重嵌真正变了的页。
"""

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from app.ingest.embedder import FakeEmbedder
from app.ingest.serializer import KNOWLEDGE_TABLES, SPEC_TABLES
from app.ingest.store import VectorStore
from app.models.spec import SpecTable
from app.wiki.compile import compile_entries, load_records
from app.wiki.indexer import (
    WIKI_PAGE_COLLECTION,
    WIKI_SECTION_COLLECTION,
    index_is_current,
    index_wiki,
    save_index_state,
    section_doc_id,
    split_section_doc_id,
)
from app.wiki.manifest import Manifest, build_manifest, source_fingerprint

RACKET: SpecTable = next(t for t in SPEC_TABLES if t.name == "球拍")
SPEC_KNOWLEDGE: SpecTable = next(t for t in KNOWLEDGE_TABLES if t.name == "规格常识")
TABLES = (RACKET, SPEC_KNOWLEDGE)

RACKET_ROWS = [
    {"品牌": "尤尼克斯 YONEX", "型号": "天斧99", "别名": "ASTROX 99", "拍身重量(U)": "4U",
     "中管韧度": "硬", "平衡点类别": "头重", "最高磅数": "35", "参考价": "1350元", "来源": "淘宝"},
    {"品牌": "李宁 LINING", "型号": "战戟12", "别名": "", "拍身重量(U)": "3U",
     "中管韧度": "适中", "平衡点类别": "均衡", "最高磅数": "30", "参考价": "799元", "来源": "淘宝"},
]
KNOWLEDGE_ROWS = [
    {"规格项": "拍身重量U数", "规格值": "4U", "含义说明": "重量约80-84克", "适用建议": "较轻，挥拍快"},
    {"规格项": "拍身重量U数", "规格值": "3U", "含义说明": "重量约85-89克", "适用建议": "攻守兼备"},
    {"规格项": "平衡点类别", "规格值": "头重", "含义说明": "平衡点高于295mm", "适用建议": "适合进攻型打法"},
]


def _write(root: Path, table: SpecTable, rows: list[dict]) -> None:
    path = root / table.csv_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "processed"
    _write(root, RACKET, RACKET_ROWS)
    _write(root, SPEC_KNOWLEDGE, KNOWLEDGE_ROWS)
    return root


def _entries(root: Path):
    records = load_records(root, TABLES)
    return compile_entries(data_dir=root, tables=TABLES, records=records)


def test_doc_id_round_trip():
    doc_id = section_doc_id("ent_racket_specs_x_1", "overview")
    assert doc_id == "ent_racket_specs_x_1#overview"
    assert split_section_doc_id(doc_id) == ("ent_racket_specs_x_1", "overview")
    assert ":" not in doc_id


def test_index_wiki_skips_unchanged_content_despite_frontmatter_change(data_dir: Path):
    """重嵌判据只看将被写入的文本+元数据：frontmatter（条目级指纹）变化不该触发全库重嵌。"""
    store, embedder = VectorStore(), FakeEmbedder()
    entries = _entries(data_dir)
    index_wiki(store, embedder, entries)

    bumped = [replace(e, fingerprint="sha256:renderer-v2") for e in entries]
    report = index_wiki(store, embedder, bumped)
    assert report.embedded == 0
    assert report.skipped == len(entries) + sum(len(e.sections) for e in entries)


def test_index_wiki_pages_and_sections(data_dir: Path):
    store, embedder = VectorStore(), FakeEmbedder()
    entries = _entries(data_dir)

    report = index_wiki(store, embedder, entries)

    assert store.count(WIKI_PAGE_COLLECTION) == len(entries)
    sections = sum(len(e.sections) for e in entries)
    assert store.count(WIKI_SECTION_COLLECTION) == sections
    assert report.skipped == 0
    assert len(report.pages_written) == len(entries)
    assert len(report.sections_written) == sections

    hits = store.get(WIKI_SECTION_COLLECTION, [section_doc_id(entries[0].id, "specs")])
    meta = hits[0]["metadata"]
    assert meta["entry_title"] == "尤尼克斯 YONEX 天斧99"
    assert "拍身重量(U)" in hits[0]["document"]
    # metadata 只能是标量 → facets/records 以 JSON 字符串落库
    assert meta["wiki"] == 1 and meta["entry_id"] == entries[0].id


def test_index_wiki_is_incremental(data_dir: Path):
    store, embedder = VectorStore(), FakeEmbedder()
    entries = _entries(data_dir)
    index_wiki(store, embedder, entries)

    again = index_wiki(store, embedder, _entries(data_dir))
    assert again.embedded == 0 and again.deleted == []
    assert again.skipped == len(entries) + sum(len(e.sections) for e in entries)

    changed_rows = [dict(RACKET_ROWS[0]), dict(RACKET_ROWS[1], 参考价="699元")]
    _write(data_dir, RACKET, changed_rows)
    changed = index_wiki(store, embedder, _entries(data_dir))
    # 只有战戟12 的页与它的 3 个章节需要重嵌
    assert len(changed.pages_written) == 1
    assert len(changed.sections_written) == 3


def test_index_wiki_removes_stale_documents(data_dir: Path):
    store, embedder = VectorStore(), FakeEmbedder()
    entries = _entries(data_dir)
    index_wiki(store, embedder, entries)
    gone = next(e for e in entries if e.title == "李宁 LINING 战戟12")

    _write(data_dir, RACKET, RACKET_ROWS[:1])
    report = index_wiki(store, embedder, _entries(data_dir))

    assert gone.id in report.deleted
    assert store.get(WIKI_PAGE_COLLECTION, [gone.id]) == []
    assert store.count(WIKI_PAGE_COLLECTION) == 3


def test_index_state_current(data_dir: Path, tmp_path: Path):
    store, embedder = VectorStore(), FakeEmbedder()
    entries = _entries(data_dir)
    fingerprint = source_fingerprint(data_dir, TABLES)
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    report = index_wiki(store, embedder, entries)
    manifest = build_manifest(entries, fingerprint)
    manifest.save(wiki_dir)
    save_index_state(wiki_dir, manifest.source_fingerprint, report)

    assert index_is_current(wiki_dir, store)
    save_index_state(wiki_dir, "sha256:stale", report)
    assert not index_is_current(wiki_dir, store)
    assert not index_is_current(tmp_path / "missing", store)


def test_page_document_is_indexed_not_raw_rows(data_dir: Path):
    """wiki_page 存的是条目概况（标题+facets+章节名），不是章节全文。"""
    store, embedder = VectorStore(), FakeEmbedder()
    entries = _entries(data_dir)
    index_wiki(store, embedder, entries)
    product = next(e for e in entries if e.type == "product")

    page = store.get(WIKI_PAGE_COLLECTION, [product.id])[0]["document"]
    assert product.title in page and "章节：" in page
    assert "| 品牌 |" not in page  # 规格全文在 wiki_section，不在页级文档
