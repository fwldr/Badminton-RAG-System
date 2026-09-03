"""Wiki 知识模型单元测试：id/slug 合法性、条目 markdown 往返、索引文档与目录提示（全离线）。

条目 id 必须同时满足「合法 Chroma 名片段」与「不含冒号」（`Retriever._fetch_record` 用
`rpartition(":")` 拆 record id），中文标题一律降级为 ASCII slug + 短 hash。
"""

import pytest

from app.wiki.schema import (
    Entry,
    Section,
    SourceAnchor,
    entry_id,
    is_valid_entry_id,
    join_anchor,
    slugify,
    split_anchor,
    validate_entry_id,
)


def _anchor(rid: str = "racket_specs:42", row: int = 42) -> SourceAnchor:
    return SourceAnchor(table="球拍", id=rid, file="data/processed/球拍.csv", row=row)


def _entry(**overrides) -> Entry:
    data = dict(
        id=entry_id("ent_racket_specs", "尤尼克斯 天斧99", salt="racket_specs:42", ascii_hint="ASTROX 99"),
        title="尤尼克斯 天斧99",
        type="product",
        category="装备规格/球拍",
        aliases=("ASTROX 99", "AX99"),
        facets={"品牌": "尤尼克斯 YONEX", "拍身重量(U)": "4U", "平衡点类别": "头重"},
        sections=(
            Section("overview", "概况", "尤尼克斯 天斧99 是一支 4U 头重进攻拍。", (_anchor(),)),
            Section("specs", "规格参数", "| 项目 | 值 |\n|---|---|\n| 拍身重量(U) | 4U |", (_anchor(),)),
        ),
        links_out=(join_anchor("con_spec_knowledge_x_1", "row-2"),),
        fingerprint="sha256:abc",
    )
    data.update(overrides)
    return Entry(**data)


def test_slugify_keeps_ascii_only():
    assert slugify("威克多 VICTOR ARS-90KⅡ") == "victor-ars-90kii"
    assert slugify("尤尼克斯 天斧99") == "99"
    assert slugify("纯中文没有拉丁字符") == ""
    assert slugify("  Ax100  zz200  ") == "ax100-zz200"
    assert len(slugify("x" * 200)) <= 48
    assert slugify("") == ""


def test_entry_id_is_ascii_and_deterministic():
    eid = entry_id("ent_racket_specs", "尤尼克斯 天斧99", salt="racket_specs:42")
    assert eid == entry_id("ent_racket_specs", "尤尼克斯 天斧99", salt="racket_specs:42")
    assert is_valid_entry_id(eid)
    # 同名不同来源行 → 短 hash 不同，不会互相覆盖
    other = entry_id("ent_racket_specs", "尤尼克斯 天斧99", salt="racket_specs:99")
    assert other != eid
    # 纯中文标题降级为 `x` + hash（无注音依赖）
    assert entry_id("con_spec_knowledge", "平衡点类别", salt="规格常识|平衡点类别").startswith(
        "con_spec_knowledge_x_"
    )


def test_entry_id_rejects_colon_and_cjk():
    assert not is_valid_entry_id("ent_racket:1")
    assert not is_valid_entry_id("ent_球拍_1")
    assert not is_valid_entry_id("ab")  # 短于 3 位
    with pytest.raises(ValueError):
        validate_entry_id("bad id")


def test_anchor_round_trip():
    anchor = _anchor()
    assert SourceAnchor.from_dict(anchor.to_dict()) == anchor


def test_entry_markdown_round_trip_preserves_structure():
    entry = _entry()
    text = entry.to_markdown()
    assert text.startswith("---\nid: ")
    assert "## 概况" in text and "## 规格参数" in text
    assert "racket_specs:42" in text  # 来源锚点原样落盘

    restored = Entry.from_markdown(text)
    assert restored.id == entry.id
    assert restored.title == entry.title
    assert restored.type == entry.type
    assert restored.category == entry.category
    assert restored.aliases == entry.aliases
    assert restored.facets == entry.facets
    assert restored.links_out == entry.links_out
    assert restored.fingerprint == entry.fingerprint
    assert [s.key for s in restored.sections] == ["overview", "specs"]
    assert restored.sections[0].body == entry.sections[0].body
    assert restored.sources == entry.sources


def test_entry_rendering_is_stable():
    entry = _entry()
    assert entry.to_markdown() == entry.to_markdown()
    assert entry.digest() == _entry().digest()


def test_entry_digest_changes_when_content_changes():
    base = _entry()
    changed = _entry(facets={**base.facets, "平衡点类别": "头轻"})
    assert base.digest() != changed.digest()


def test_from_markdown_rejects_body_frontmatter_mismatch():
    text = _entry().to_markdown() + "\n## 多出来的章节\n内容\n"
    with pytest.raises(ValueError):
        Entry.from_markdown(text)


def test_from_markdown_rejects_missing_frontmatter():
    with pytest.raises(ValueError):
        Entry.from_markdown("# 没有 frontmatter")


def test_entry_rejects_invalid_id_and_type():
    with pytest.raises(ValueError):
        Entry(id="坏 id", title="t", type="product", category="c")
    with pytest.raises(ValueError):
        Entry(id="ent_ok_123", title="t", type="unknown", category="c")
    with pytest.raises(ValueError):
        Entry(id="ent_ok_123", title="   ", type="product", category="c")


def test_sources_dedupe_across_sections():
    shared = _anchor()
    entry = _entry(
        sections=(
            Section("a", "甲", "x", (shared, _anchor("racket_specs:7", 7))),
            Section("b", "乙", "y", (shared,)),
        )
    )
    assert entry.record_ids == ("racket_specs:42", "racket_specs:7")


def test_section_lookup_and_anchor_split():
    entry = _entry()
    assert entry.section("specs").title == "规格参数"
    assert entry.section("missing") is None
    assert split_anchor("ent_a#row-1") == ("ent_a", "row-1")
    assert split_anchor(join_anchor("ent_a")) == ("ent_a", "")


def test_page_document_and_toc_hint():
    entry = _entry()
    page = entry.page_document()
    assert "尤尼克斯 天斧99" in page
    assert "拍身重量(U)：4U" in page
    assert "概况" in page and "规格参数" in page

    hint = entry.toc_hint()
    assert "ASTROX 99" in hint and "4U" in hint
