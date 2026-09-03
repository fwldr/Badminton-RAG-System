"""Wiki 编译器测试（全离线：只读 data/processed CSV 与临时目录，不调 LLM/向量）。

覆盖 W1 验收线：
- 829 条记录 → 条目页，每条记录被且只被一个条目锚定，且每个非空单元格都能在条目里找到；
- facets 原样取自 CSV 列；
- 条目 id 全部合法（ASCII、无冒号）且唯一；
- 概念条目三种粒度（row/field/table）与「值索引」推导的 links 均按模板生成。
"""

import csv
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.ingest.pipeline import row_ids
from app.ingest.serializer import KNOWLEDGE_TABLES, SPEC_TABLES
from app.models.spec import SpecTable
from app.wiki.compile import (
    CONCEPT_POLICIES,
    MAX_IN_LINKS,
    WikiCompileError,
    compile_entries,
    load_records,
    split_values,
    validate_entries,
)

RACKET: SpecTable = next(t for t in SPEC_TABLES if t.name == "球拍")
SPEC_KNOWLEDGE: SpecTable = next(t for t in KNOWLEDGE_TABLES if t.name == "规格常识")

RACKET_ROWS = [
    {
        "品牌": "尤尼克斯 YONEX", "型号": "天斧99", "别名": "ASTROX 99",
        "拍框材质": "碳纤维", "拍身重量(U)": "4U", "拍柄粗细": "G5",
        "中管韧度": "硬", "拉线磅数": "21-29LBS", "最高磅数": "35",
        "平衡点": "305mm", "平衡点类别": "头重", "打法类型": "进攻型",
        "适合水平": "进阶级", "适合人群": "适合力量好的进攻型选手",
        "参考价": "1350元", "来源": "淘宝",
    },
    {
        "品牌": "李宁 LINING", "型号": "战戟12", "别名": "",
        "拍框材质": "碳纤维", "拍身重量(U)": "3U,4U", "拍柄粗细": "G6",
        "中管韧度": "适中", "拉线磅数": "24-28LBS", "最高磅数": "30",
        "平衡点": "", "平衡点类别": "均衡", "打法类型": "均衡型",
        "适合水平": "通用级", "适合人群": "", "参考价": "799元", "来源": "淘宝",
    },
]

SPEC_KNOWLEDGE_ROWS = [
    {"规格项": "拍身重量U数", "规格值": "4U", "含义说明": "重量约80-84克", "适用建议": "较轻，挥拍快"},
    {"规格项": "拍身重量U数", "规格值": "3U", "含义说明": "重量约85-89克", "适用建议": "攻守兼备"},
    {"规格项": "平衡点类别", "规格值": "头重", "含义说明": "平衡点高于295mm", "适用建议": "适合进攻型打法"},
]


def _write_fixture(root: Path, table: SpecTable, rows: list[dict]) -> None:
    path = root / table.csv_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def wiki_data(tmp_path: Path) -> Path:
    """两张表的迷你事实源（1 张规格表 + 1 张知识表）。"""
    _write_fixture(tmp_path, RACKET, RACKET_ROWS)
    _write_fixture(tmp_path, SPEC_KNOWLEDGE, SPEC_KNOWLEDGE_ROWS)
    return tmp_path


def _compile(data_dir: Path) -> tuple[list, list]:
    tables = (RACKET, SPEC_KNOWLEDGE)
    records = load_records(data_dir, tables)
    return compile_entries(data_dir=data_dir, tables=tables, records=records), records


def test_split_values_handles_multi_value_cells():
    assert split_values("4U,5U") == ["4U", "5U"]
    assert split_values("硬、适中") == ["硬", "适中"]
    assert split_values(" 76，77 ") == ["76", "77"]
    assert split_values("") == []


def test_product_entry_sections(wiki_data: Path):
    entries, records = _compile(wiki_data)
    products = [e for e in entries if e.type == "product"]
    assert len(products) == 2
    entry = next(e for e in products if e.title == "尤尼克斯 YONEX 天斧99")

    assert entry.id.startswith("ent_racket_specs_")
    assert "ASTROX 99" in entry.aliases
    assert [s.key for s in entry.sections] == ["overview", "specs", "fit"]
    # 概况复用既有行级序列化（零 LLM）
    assert entry.sections[0].body == RACKET.serializer(RACKET_ROWS[0])
    specs = entry.section("specs").body
    assert "| 品牌 | 尤尼克斯 YONEX |" in specs and "| 中管韧度 | 硬 |" in specs
    assert "适合力量好的进攻型选手" in entry.section("fit").body
    # 每节都自带来源锚点（record id = 主键哈希行 id，与入库 pipeline 一致）
    assert {a.id for a in entry.sections[1].sources} == {row_ids(RACKET, RACKET_ROWS)[0]}


def test_product_facets_come_from_metadata_fields(wiki_data: Path):
    entries, _ = _compile(wiki_data)
    entry = next(e for e in entries if e.title == "李宁 LINING 战戟12")
    assert entry.facets == {
        "品牌": "李宁 LINING",
        "型号": "战戟12",
        "拍身重量(U)": "3U,4U",
        "最高磅数": "30",
        "平衡点类别": "均衡",
        "打法类型": "均衡型",
        "适合水平": "通用级",
        "参考价": "799元",
    }
    assert "来源" not in entry.facets and "平衡点" not in entry.facets


def test_concept_field_granularity_groups_rows_into_sections(wiki_data: Path):
    entries, _ = _compile(wiki_data)
    weight = next(e for e in entries if e.title == "拍身重量U数")
    assert weight.type == "concept"
    assert weight.category == "器材常识/规格常识/拍身重量U数"
    assert weight.facets == {"规格项": "拍身重量U数"}
    assert [s.title for s in weight.sections] == ["4U", "3U"]
    assert "重量约80-84克" in weight.sections[0].body
    assert weight.record_ids == tuple(row_ids(SPEC_KNOWLEDGE, SPEC_KNOWLEDGE_ROWS)[:2])


def test_concept_row_granularity_and_other_granularities():
    policies = {p.table: p.granularity for p in CONCEPT_POLICIES.values()}
    assert policies["手法技术"] == "row" and policies["战术"] == "row"
    assert policies["BWF官方规则"] == "field" and policies["规格常识"] == "field"
    assert policies["毛片等级"] == "table" and policies["速度等级"] == "table"


def test_links_derived_from_value_index(wiki_data: Path):
    entries, _ = _compile(wiki_data)
    yonex = next(e for e in entries if e.title == "尤尼克斯 YONEX 天斧99")
    lining = next(e for e in entries if e.title == "李宁 LINING 战戟12")
    weight = next(e for e in entries if e.title == "拍身重量U数")
    balance = next(e for e in entries if e.title == "平衡点类别")

    assert yonex.links_out == (f"{weight.id}#row-0", f"{balance.id}#row-2")
    # 多值单元格（3U,4U）拆开后各指一节
    weight_targets = [t for t in lining.links_out if t.split("#")[0] == weight.id]
    assert sorted(t.split("#")[1] for t in weight_targets) == ["row-0", "row-1"]
    # 反向链接回填到被指向的概念页
    assert yonex.id in weight.links_in


def test_in_links_are_capped(tmp_path: Path):
    rows = [dict(RACKET_ROWS[0], 品牌=f"牌{i}", 型号=f"M{i}", 平衡点类别="头重") for i in range(MAX_IN_LINKS + 5)]
    _write_fixture(tmp_path, RACKET, rows)
    _write_fixture(tmp_path, SPEC_KNOWLEDGE, SPEC_KNOWLEDGE_ROWS)
    entries, _ = _compile(tmp_path)
    balance = next(e for e in entries if e.title == "平衡点类别")
    assert len(balance.links_in) == MAX_IN_LINKS


def test_validate_detects_uncovered_record(wiki_data: Path):
    entries, records = _compile(wiki_data)
    dropped = [e for e in entries if e.title != "拍身重量U数"]
    with pytest.raises(WikiCompileError, match="未被覆盖"):
        validate_entries(dropped, records)


def test_validate_detects_unknown_anchor(wiki_data: Path):
    entries, records = _compile(wiki_data)
    # 源表少了一行，但条目仍锚定它 → 锚点指向不存在记录
    with pytest.raises(WikiCompileError, match="指向不存在记录"):
        validate_entries(entries, records[1:])


def test_validate_detects_facet_not_in_source(wiki_data: Path):
    entries, records = _compile(wiki_data)
    product = next(e for e in entries if e.type == "product")
    product.facets["品牌"] = "不存在的品牌"
    with pytest.raises(WikiCompileError, match="facets 必须原样来自 CSV"):
        validate_entries(entries, records)


def test_validate_detects_missing_cell(wiki_data: Path):
    entries, records = _compile(wiki_data)
    product = next(e for e in entries if e.type == "product")
    product.sections = tuple(s for s in product.sections if s.key != "specs")
    with pytest.raises(WikiCompileError, match="未包含来源"):
        validate_entries(entries, records)


def test_unknown_table_has_no_policy(tmp_path: Path):
    _write_fixture(tmp_path, RACKET, RACKET_ROWS)
    records = [
        replace(r, table="没这张表") for r in load_records(tmp_path, (RACKET,))
    ]
    with pytest.raises(WikiCompileError, match="没有编译策略"):
        compile_entries(data_dir=tmp_path, tables=(RACKET,), records=records)


# ---------- 全量语料验收（plan §7 W1 验收线） ----------


def test_full_corpus_compiles_and_validates():
    data_dir = get_settings().processed_data_dir
    records = load_records(data_dir)
    entries = compile_entries(data_dir=data_dir, records=records)

    assert len(records) == 829
    assert Counter(e.type for e in entries) == {"product": 714, "concept": 71, "category": 68}
    assert len({e.id for e in entries}) == len(entries)  # id 唯一
    assert all(":" not in e.id for e in entries)

    primary = [e for e in entries if e.type in ("product", "concept")]
    anchored = Counter(rid for e in primary for rid in e.record_ids)
    assert set(anchored) == {r.id for r in records}      # 100% 可回溯
    assert set(anchored.values()) == {1}                 # 主条目下无一被重复锚定
    assert {e.category.split("/")[0] for e in entries} == {
        "装备规格", "规则判罚", "技术教学", "器材常识", "聚合视图",
    }
    validate_entries(entries, records)                   # 忠实性（含逐单元格回溯）


def test_category_entries_aggregate_products_by_facet(tmp_path: Path):
    rows = [
        {**RACKET_ROWS[0], "品牌": f"牌{i}", "型号": f"M{i}", "拍身重量(U)": "4U", "平衡点类别": "头重"}
        for i in range(6)
    ]
    _write_fixture(tmp_path, RACKET, rows)
    _write_fixture(tmp_path, SPEC_KNOWLEDGE, SPEC_KNOWLEDGE_ROWS)
    entries, records = _compile(tmp_path)

    category = next(e for e in entries if e.type == "category")
    assert category.title == "4U 球拍"
    assert category.facets == {"拍身重量(U)": "4U"}
    assert category.category == "聚合视图/球拍/拍身重量(U)"
    assert [s.title for s in category.sections] == ["聚合概况", *(f"牌{i}" for i in range(6))]
    assert "共 6 款" in category.sections[0].body
    assert "- 牌0 M0｜" in category.section("brand-0-0").body  # 成员行摘要可回溯
    assert len(category.record_ids) == 6

    product = next(e for e in entries if e.title == "牌0 M0")
    assert category.id in product.links_out              # 产品页指向所属聚合页


def test_category_pages_skip_small_groups(tmp_path: Path):
    _write_fixture(tmp_path, RACKET, RACKET_ROWS)
    _write_fixture(tmp_path, SPEC_KNOWLEDGE, SPEC_KNOWLEDGE_ROWS)
    entries, _ = _compile(tmp_path)
    # 默认门槛 min_members=5：两张表的夹具都不该产出聚合页
    assert [e for e in entries if e.type == "category"] == []
