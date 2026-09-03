"""Wiki 清单/目录与幂等落盘测试（全离线：临时目录，不触网、不碰 data/wiki）。

覆盖 W1 验收线：重跑指纹未变 → 零写入；源变更 → 只重写受影响页并清理失效页；
`record_id → entry_id` 反查可用（在线 orient 的 hybrid 补齐与 `_strict` 口径都依赖它）。
"""

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.ingest.pipeline import row_ids
from app.ingest.serializer import KNOWLEDGE_TABLES, SPEC_TABLES
from app.models.spec import SpecTable
from app.wiki.compile import compile_entries, load_records
from app.wiki.manifest import (
    ENTRIES_SUBDIR,
    MANIFEST_NAME,
    TOC_NAME,
    Manifest,
    build_manifest,
    build_toc,
    entry_path,
    load_entry,
    source_fingerprint,
    write_wiki,
)
from app.wiki.schema import COMPILED_VERSION

RACKET: SpecTable = next(t for t in SPEC_TABLES if t.name == "球拍")
SPEC_KNOWLEDGE: SpecTable = next(t for t in KNOWLEDGE_TABLES if t.name == "规格常识")

RACKET_ROWS = [
    {"品牌": "尤尼克斯 YONEX", "型号": "天斧99", "别名": "ASTROX 99", "拍身重量(U)": "4U",
     "中管韧度": "硬", "平衡点类别": "头重", "最高磅数": "35", "参考价": "1350元", "来源": "淘宝"},
    {"品牌": "李宁 LINING", "型号": "战戟12", "别名": "", "拍身重量(U)": "3U",
     "中管韧度": "适中", "平衡点类别": "均衡", "最高磅数": "30", "参考价": "799元", "来源": "淘宝"},
]
KNOWLEDGE_ROWS = [
    {"规格项": "拍身重量U数", "规格值": "4U", "含义说明": "重量约80-84克", "适用建议": "较轻，挥拍快"},
    {"规格项": "平衡点类别", "规格值": "头重", "含义说明": "平衡点高于295mm", "适用建议": "适合进攻型打法"},
]
TABLES = (RACKET, SPEC_KNOWLEDGE)


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


def _build(root: Path):
    records = load_records(root, TABLES)
    return compile_entries(data_dir=root, tables=TABLES, records=records), source_fingerprint(root, TABLES)


def test_source_fingerprint_tracks_content_and_schema_version(data_dir: Path):
    first = source_fingerprint(data_dir, TABLES)
    assert first == source_fingerprint(data_dir, TABLES)
    assert first.startswith("sha256:")

    _write(data_dir, RACKET, RACKET_ROWS[:1])
    assert source_fingerprint(data_dir, TABLES) != first

    # 模板结构升级（未落盘的旧页格式会变）也必须判定为「需要重编译」
    _write(data_dir, RACKET, RACKET_ROWS)
    import app.wiki.manifest as manifest_module

    original = manifest_module.COMPILED_VERSION
    try:
        manifest_module.COMPILED_VERSION = original + 1
        assert source_fingerprint(data_dir, TABLES) != first
    finally:
        manifest_module.COMPILED_VERSION = original
    assert COMPILED_VERSION >= 1


def test_write_wiki_creates_pages_manifest_and_toc(tmp_path: Path, data_dir: Path):
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)

    report = write_wiki(wiki_dir, entries, fingerprint)

    assert len(report.written) == len(entries) and not report.skipped
    assert (wiki_dir / MANIFEST_NAME).exists() and (wiki_dir / TOC_NAME).exists()
    assert len(list((wiki_dir / ENTRIES_SUBDIR).glob("*.md"))) == len(entries)

    # 落盘页能原样读回（frontmatter 与正文一致）
    for entry in entries:
        loaded = load_entry(wiki_dir, entry.id)
        assert loaded is not None and loaded.digest() == entry.digest()
        assert loaded.facets == entry.facets
        assert [s.key for s in loaded.sections] == [s.key for s in entry.sections]
    assert load_entry(wiki_dir, "不存在的条目_abc") is None


def test_write_wiki_is_idempotent(tmp_path: Path, data_dir: Path):
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)
    write_wiki(wiki_dir, entries, fingerprint)
    before = {p.name: p.stat().st_mtime_ns for p in (wiki_dir / ENTRIES_SUBDIR).glob("*.md")}

    report = write_wiki(wiki_dir, entries, fingerprint)

    assert report.written == [] and report.deleted == []
    assert len(report.skipped) == len(entries)
    assert not report.changed
    after = {p.name: p.stat().st_mtime_ns for p in (wiki_dir / ENTRIES_SUBDIR).glob("*.md")}
    assert after == before  # 真的一个字节都没写


def test_write_wiki_rewrites_when_new_entries_appear(tmp_path: Path, data_dir: Path):
    """源未变但产物多了条目（如 W4 新增聚合页）→ 不能被零写入快路径吞掉。"""
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)
    write_wiki(wiki_dir, entries, fingerprint)

    grown = entries + [replace(entries[0], id="ent_extra_new_1", title="新增聚合页")]
    report = write_wiki(wiki_dir, grown, fingerprint)

    assert report.written == ["ent_extra_new_1"]
    assert (wiki_dir / "entries" / "ent_extra_new_1.md").exists()
    # 完全一致时仍然零写入
    again = write_wiki(wiki_dir, grown, fingerprint)
    assert again.written == [] and len(again.skipped) == len(grown)


def test_write_wiki_rewrites_only_changed_page(tmp_path: Path, data_dir: Path):
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)
    write_wiki(wiki_dir, entries, fingerprint)

    changed_rows = [dict(RACKET_ROWS[0]), dict(RACKET_ROWS[1], 参考价="699元")]
    _write(data_dir, RACKET, changed_rows)
    entries2, fingerprint2 = _build(data_dir)
    report = write_wiki(wiki_dir, entries2, fingerprint2)

    assert len(report.written) == 1  # 只有战戟12 那一页
    page = next(e for e in entries2 if e.title == "李宁 LINING 战戟12")
    assert report.written == [page.id]
    assert "699元" in entry_path(wiki_dir, page.id).read_text(encoding="utf-8")


def test_write_wiki_deletes_stale_pages(tmp_path: Path, data_dir: Path):
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)
    write_wiki(wiki_dir, entries, fingerprint)
    gone = next(e for e in entries if e.title == "李宁 LINING 战戟12")

    _write(data_dir, RACKET, RACKET_ROWS[:1])
    entries2, fingerprint2 = _build(data_dir)
    report = write_wiki(wiki_dir, entries2, fingerprint2)

    assert report.deleted == [gone.id]
    assert not entry_path(wiki_dir, gone.id).exists()
    assert Manifest.load(wiki_dir).entries.get(gone.id) is None


def test_write_wiki_dry_run_writes_nothing(tmp_path: Path, data_dir: Path):
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)

    report = write_wiki(wiki_dir, entries, fingerprint, dry_run=True)

    assert len(report.written) == len(entries)
    assert list((wiki_dir / ENTRIES_SUBDIR).glob("*.md")) == []
    assert not (wiki_dir / MANIFEST_NAME).exists()


def test_write_wiki_force_rewrites_everything(tmp_path: Path, data_dir: Path):
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)
    write_wiki(wiki_dir, entries, fingerprint)

    report = write_wiki(wiki_dir, entries, fingerprint, force=True)

    assert len(report.written) == len(entries) and not report.skipped


def test_manifest_reverse_lookup_and_titles(tmp_path: Path, data_dir: Path):
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)
    write_wiki(wiki_dir, entries, fingerprint)
    manifest = Manifest.load(wiki_dir)

    page = next(e for e in entries if e.title == "尤尼克斯 YONEX 天斧99")
    rid0 = row_ids(RACKET, RACKET_ROWS)[0]
    ghost = row_ids(RACKET, [{"品牌": "查无", "型号": "此拍"}])[0]
    assert manifest.entry_ids_for_record(rid0) == [page.id]
    assert manifest.entry_ids_for_record(ghost) == []
    assert manifest.title(page.id) == "尤尼克斯 YONEX 天斧99"
    assert manifest.title(f"{page.id}#overview") == "尤尼克斯 YONEX 天斧99"
    assert manifest.stats["records"] == 4 and manifest.stats["entries"] == len(entries)

    # 章节级 record 锚点：context_precision_strict 的回算依据
    summary = manifest.entries[page.id]
    assert summary["sections"][0]["records"] == [rid0]
    assert summary["fingerprint"].startswith("sha256:")


def test_manifest_round_trip_through_dict(tmp_path: Path, data_dir: Path):
    entries, _ = _build(data_dir)
    manifest = build_manifest(entries, "sha256:seed")
    restored = Manifest.from_dict(json.loads(json.dumps(manifest.to_dict(), ensure_ascii=False)))
    assert restored.source_fingerprint == "sha256:seed"
    assert restored.entries == manifest.entries
    assert restored.record_to_entries == manifest.record_to_entries
    assert Manifest.load(tmp_path / "no-such-dir") is None


def test_manifest_rejects_duplicate_entry_id(data_dir: Path):
    entries, _ = _build(data_dir)
    with pytest.raises(ValueError, match="条目 id 重复"):
        build_manifest([entries[0], entries[0]], "fp")


def test_toc_structure_is_two_level(tmp_path: Path, data_dir: Path):
    wiki_dir = tmp_path / "wiki"
    entries, fingerprint = _build(data_dir)
    write_wiki(wiki_dir, entries, fingerprint)
    toc = json.loads((wiki_dir / TOC_NAME).read_text(encoding="utf-8"))

    assert toc["total_entries"] == len(entries)
    assert [c["name"] for c in toc["categories"]] == ["器材常识", "装备规格"]
    gear = next(c for c in toc["categories"] if c["name"] == "装备规格")
    assert gear["count"] == 2
    table = gear["tables"][0]
    assert table["path"] == "装备规格/球拍"
    line = table["entries"][0]
    assert line["id"].startswith("ent_racket_specs_")
    assert "ASTROX 99" in line["hint"] and "4U" in line["hint"]
    assert line["sections"] == ["概况", "规格参数", "适用人群与打法"]
