"""Wiki 编译器（W1：纯模板、零 LLM）。

`data/processed/` 的 829 条 CSV 记录 → 条目页：

- **product**（5 张规格表逐行）：`概况`（复用现有行级序列化）+ `规格参数`（全字段表）+
  `适用人群与打法`，facets 严格取自 `SpecTable.metadata_fields`；
- **concept**（12 张知识表，按表选粒度）：小体量对比表（毛片等级/球头材质/速度等级/两类影响因素）
  合成一页多节，`规格常识` 按 `规格项` 分组，其余（规则/判罚/战术/手法/步法/毛片类型）逐行成页；
- **links**：由「值索引」模板推导（如 `4U` → 概念《拍身重量U数》§4U），不调用 LLM；
  category 条目与 LLM 综述段落属 W4，本模块不产出。

忠实性铁律：条目正文只允许出现来源行的原文，编译后由 `validate_entries` 校验
「每条记录的每个非空单元格都出现在其条目文本中」且「facets 原样等于 CSV 单元格值」，不满足即失败。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.ingest.pipeline import load_rows, row_ids
from app.ingest.serializer import ALL_TABLES, SPEC_TABLES
from app.ingest.store import collection_name
from app.models.spec import SpecTable
from app.wiki.schema import (
    Entry,
    Section,
    SourceAnchor,
    entry_id,
    join_anchor,
    slugify,
)

logger = logging.getLogger(__name__)

# 知识表 → 目录第一级分类（对齐 plan §3.3）
TABLE_GROUPS: dict[str, str] = {
    **{t.name: "装备规格" for t in SPEC_TABLES},
    "BWF官方规则": "规则判罚",
    "常见判罚": "规则判罚",
    "战术": "技术教学",
    "手法技术": "技术教学",
    "步法技术": "技术教学",
    "毛片等级": "器材常识",
    "毛片类型": "器材常识",
    "球头材质": "器材常识",
    "耐打度影响因素": "器材常识",
    "速度等级": "器材常识",
    "飞行稳定性影响因素": "器材常识",
    "规格常识": "器材常识",
}

# 多值单元格拆分（如 `4U,5U`、`76、77`、`硬、适中`）
_VALUE_SPLIT_RE = re.compile(r"[,，、/;；\s]+")

# 不进入 facets 的列：来源类列（出处已由 sources 锚点承载）
_NON_FACET_COLUMNS = frozenset({"来源", "来源文件"})

# 单个条目 frontmatter 保留的反向链接上限
MAX_IN_LINKS = 10

_SPEC_TABLE_NAMES = frozenset(t.name for t in SPEC_TABLES)


class WikiCompileError(RuntimeError):
    """编译校验失败（覆盖率 / 忠实性不达标）。"""


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


@dataclass(frozen=True)
class SourceRecord:
    """一条待编译的原始记录（CSV 行）。"""

    table: str        # 中文表名（球拍）
    collection: str   # 英文 collection 名（racket_specs）
    id: str           # record id（`{collection}:{sha1(主键)[:12]}`，与入库行 id 一致）
    file: str         # data/processed/球拍.csv
    row: int          # 数据行号（0 基，仅出处标注，不参与 id）
    cells: dict[str, str] = field(default_factory=dict)
    text: str = ""    # 现有行级序列化文本

    def anchor(self, field_name: str = "") -> SourceAnchor:
        return SourceAnchor(
            table=self.table, id=self.id, file=self.file, row=self.row, field=field_name
        )

    def value(self, column: str) -> str:
        return _clean(self.cells.get(column, ""))


def load_records(
    data_dir: Path | None = None,
    tables: tuple[SpecTable, ...] = ALL_TABLES,
) -> list[SourceRecord]:
    """读 CSV 为 SourceRecord 列表。

    record id 必须与入库 pipeline 的 `row_ids` 严格一致（hybrid 反查按该 id 从
    Chroma 规格 collection 取回原始行）；`row` 行号仅作人类可读出处，不参与 id。
    """
    root = data_dir or get_settings().processed_data_dir
    records: list[SourceRecord] = []
    for table in tables:
        collection = collection_name(table.name)
        rows = load_rows(root / table.csv_file)
        for idx, (rid, row) in enumerate(zip(row_ids(table, rows), rows)):
            cells = {
                _clean(key): _clean(value)
                for key, value in row.items()
                if key is not None
            }
            records.append(
                SourceRecord(
                    table=table.name,
                    collection=collection,
                    id=rid,
                    file=f"data/processed/{table.csv_file}",
                    row=idx,
                    cells=cells,
                    text=table.serializer(row),
                )
            )
    return records


# ---------- 通用渲染 ----------


def split_values(value: str) -> list[str]:
    """拆分多值单元格为 token 列表（去空、保序去重）。"""
    return [t for t in dict.fromkeys(_VALUE_SPLIT_RE.split(_clean(value))) if t]


def _field_lines(cells: dict[str, str], columns: tuple[str, ...]) -> list[str]:
    """把指定列渲染为 `- **列名**：值`（跳过空值）。"""
    return [f"- **{c}**：{cells[c]}" for c in columns if _clean(cells.get(c, ""))]


def _spec_table(cells: dict[str, str]) -> str:
    """全字段规格表（markdown 两列表），保证每行内容都可在条目内回溯。"""
    rows = [(c, v) for c, v in cells.items() if _clean(v)]
    if not rows:
        return ""
    lines = ["| 项目 | 值 |", "|---|---|"]
    lines += [f"| {c} | {v} |" for c, v in rows]
    return "\n".join(lines)


def _facets_of(record: SourceRecord, columns: tuple[str, ...]) -> dict[str, str]:
    """facets 取自指定列（规格表即 `SpecTable.metadata_fields`，值原样保留不做归一化）。"""
    return {
        c: record.value(c)
        for c in columns
        if c not in _NON_FACET_COLUMNS and record.value(c)
    }


def _match_keys(value: str) -> set[str]:
    """一个「可匹配值」的等价键集合：原值 + 括号前主干 + 去类型后缀。

    例：`75(慢速)` → {`75(慢速)`, `75`}；`三拼球头` → {`三拼球头`, `三拼`}。
    """
    raw = _clean(value)
    if not raw:
        return set()
    keys = {raw}
    base = re.split(r"[(（]", raw)[0].strip()
    if base:
        keys.add(base)
    for suffix in ("球头", "毛片", "材质"):
        if base.endswith(suffix) and len(base) > len(suffix) + 1:
            keys.add(base[: -len(suffix)])
    return keys


def record_source_fingerprint(records: list[SourceRecord]) -> str:
    """一组来源记录的**内容**指纹（条目级增量判据）。

    不含渲染版本：版本已编码在全局 `source_fingerprint` 里 —— 模板升级只需作废快路径，
    再由逐条 digest 比对定位真正变了的页，避免为一次模板改动重嵌全部章节。
    """
    payload = "\x1f".join(
        f"{r.id}|" + "\x1e".join(f"{k}={v}" for k, v in sorted(r.cells.items()))
        for r in sorted(records, key=lambda r: r.id)
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


# ---------- product 条目（5 张规格表） ----------

# 规格表 → 「适用人群与打法」章节取用的列（全为空则该章节不输出）
FIT_COLUMNS: dict[str, tuple[str, ...]] = {
    "球拍": ("打法类型", "适合水平", "适合人群", "拍柄粗细", "中管韧度"),
    "羽毛球": ("羽毛类别", "毛片级别", "适用温度"),
    "球线": ("材质", "竖横线直径"),
    "手胶": ("材质类别", "材质", "颜色"),
    "球鞋": ("鞋面", "中底", "大底"),
}

# 规格表 → 参与「值索引」跳转的列（只对能精确匹配到概念值的列开放，避免噪声链接）
PRODUCT_LINK_COLUMNS: dict[str, tuple[str, ...]] = {
    "球拍": ("拍身重量(U)", "平衡点类别", "中管韧度"),
    "羽毛球": ("毛片级别", "球头类别", "球速"),
}


def _product_title(record: SourceRecord) -> tuple[str, tuple[str, ...]]:
    """产品条目标题（品牌 + 型号/名称）与别名。"""
    brand = record.value("品牌")
    model = record.value("型号") or record.value("名称")
    title = f"{brand} {model}".strip() or record.text[:24]
    aliases = tuple(a for a in (record.value("别名"),) if a and a not in title)
    return title, aliases


def compile_product_entries(record: SourceRecord, spec: SpecTable) -> Entry:
    """一条规格表记录 → 一个 product 条目（纯模板，不经 LLM）。"""
    title, aliases = _product_title(record)
    anchor = record.anchor()
    sections = [
        Section(key="overview", title="概况", body=record.text, sources=(anchor,)),
        Section(key="specs", title="规格参数", body=_spec_table(record.cells), sources=(anchor,)),
    ]
    fit_lines = _field_lines(record.cells, FIT_COLUMNS.get(spec.name, ()))
    if fit_lines:
        sections.append(
            Section(key="fit", title="适用人群与打法", body="\n".join(fit_lines), sources=(anchor,))
        )
    return Entry(
        id=entry_id(f"ent_{record.collection}", title, salt=record.id, ascii_hint=title),
        title=title,
        type="product",
        category=f"{TABLE_GROUPS[spec.name]}/{spec.name}",
        aliases=aliases,
        facets=_facets_of(record, spec.metadata_fields),
        sections=tuple(sections),
        fingerprint=record_source_fingerprint([record]),
    )


# ---------- concept 条目（12 张知识表） ----------


@dataclass(frozen=True)
class SectionRule:
    """行 → 章节的渲染规则：章节键、标题、取用列。"""

    key: str
    title: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ConceptPolicy:
    """一张知识表的编译策略。

    - `granularity="row"`：一行一个条目，标题取 `subject_field`；
    - `granularity="field"`：按 `group_field` 取值分组，一行一节，节标题取 `subject_field`；
    - `granularity="table"`：整表一个条目，一行一节。
    """

    table: str
    granularity: str
    subject_field: str = ""
    group_field: str = ""
    section_rules: tuple[SectionRule, ...] = ()
    facet_fields: tuple[str, ...] = ()
    match_field: str = ""  # 供 product 条目按值反查跳转的列（row 粒度即标题列）


CONCEPT_POLICIES: dict[str, ConceptPolicy] = {
    p.table: p
    for p in (
        ConceptPolicy(
            table="BWF官方规则",
            granularity="field",
            group_field="规则类别",
            subject_field="规则内容",
            facet_fields=("规则类别",),
        ),
        ConceptPolicy(
            table="常见判罚",
            granularity="field",
            group_field="判罚类型",
            subject_field="违例描述",
            facet_fields=("判罚类型",),
        ),
        ConceptPolicy(
            table="战术",
            granularity="row",
            subject_field="战术名称",
            section_rules=(
                SectionRule("definition", "战术解析", ("战术描述",)),
                SectionRule("scene", "适用场景", ("适用场景",)),
                SectionRule("points", "战术要点", ("战术要点",)),
            ),
            facet_fields=("类型",),
            match_field="战术名称",
        ),
        ConceptPolicy(
            table="手法技术",
            granularity="row",
            subject_field="技术名称",
            section_rules=(
                SectionRule("definition", "技术解析", ("技术描述",)),
                SectionRule("key-points", "动作要领", ("动作要领",)),
                SectionRule("mistakes", "常见错误", ("常见错误",)),
                SectionRule("training", "训练方法", ("训练方法",)),
            ),
            facet_fields=("分类",),
            match_field="技术名称",
        ),
        ConceptPolicy(
            table="步法技术",
            granularity="row",
            subject_field="技术名称",
            section_rules=(
                SectionRule("definition", "技术解析", ("技术描述",)),
                SectionRule("key-points", "动作要领", ("动作要领",)),
                SectionRule("mistakes", "常见错误", ("常见错误",)),
                SectionRule("training", "训练方法", ("训练方法",)),
            ),
            facet_fields=("分类",),
            match_field="技术名称",
        ),
        ConceptPolicy(
            table="毛片等级", granularity="table", subject_field="等级", match_field="等级"
        ),
        ConceptPolicy(
            table="毛片类型",
            granularity="row",
            subject_field="毛片名称",
            section_rules=(
                SectionRule("definition", "特性", ("特性描述",)),
                SectionRule("source", "来源", ("来源",)),
                SectionRule("flight", "飞行稳定性", ("飞行稳定性",)),
                SectionRule("durability", "耐打性", ("耐打性",)),
                SectionRule("scene", "适用场景", ("适用场景",)),
            ),
        ),
        ConceptPolicy(
            table="球头材质", granularity="table", subject_field="球头类型", match_field="球头类型"
        ),
        ConceptPolicy(
            table="耐打度影响因素",
            granularity="table",
            subject_field="影响因素",
            section_rules=(
                SectionRule("detail", "影响说明", ("说明",)),
                SectionRule("standard", "评估标准", ("评估标准",)),
            ),
        ),
        ConceptPolicy(
            table="速度等级", granularity="table", subject_field="速度等级", match_field="速度等级"
        ),
        ConceptPolicy(
            table="飞行稳定性影响因素",
            granularity="table",
            subject_field="影响因素",
            section_rules=(
                SectionRule("detail", "影响说明", ("说明",)),
                SectionRule("standard", "评估标准", ("评估标准",)),
            ),
        ),
        ConceptPolicy(
            table="规格常识",
            granularity="field",
            group_field="规格项",
            subject_field="规格值",
            facet_fields=("规格项",),
            match_field="规格值",
        ),
    )
}


def _concept_category(policy: ConceptPolicy, group_value: str = "") -> str:
    group = TABLE_GROUPS[policy.table]
    return f"{group}/{policy.table}/{group_value}" if group_value else f"{group}/{policy.table}"


def _row_sections(record: SourceRecord, policy: ConceptPolicy) -> tuple[Section, ...]:
    """row 粒度：按 `section_rules` 分节；规则未覆盖的非空列落到末尾 `其他信息` 保证全覆盖。"""
    anchor = record.anchor()
    used: set[str] = {policy.subject_field}
    sections: list[Section] = []
    for rule in policy.section_rules:
        lines = _field_lines(record.cells, rule.columns)
        if lines:
            used.update(rule.columns)
            sections.append(
                Section(key=rule.key, title=rule.title, body="\n".join(lines), sources=(anchor,))
            )
    rest = tuple(c for c in record.cells if c not in used and _clean(record.cells[c]))
    if rest:
        sections.append(
            Section(
                key="other",
                title="其他信息",
                body="\n".join(_field_lines(record.cells, rest)),
                sources=(anchor,),
            )
        )
    return tuple(sections)


def compile_concept_entries(records: list[SourceRecord], policy: ConceptPolicy) -> list[Entry]:
    """一张知识表 → 若干 concept 条目（纯模板，不经 LLM）。"""
    if policy.granularity == "row":
        return [_row_entry(r, policy) for r in records]

    grouped: dict[str, list[SourceRecord]] = {}
    for record in records:
        key = record.value(policy.group_field) or (policy.table if not policy.group_field else "(未分类)")
        grouped.setdefault(key, []).append(record)
    as_group = bool(policy.group_field)
    return [_grouped_entry(list(rs), policy, key, as_group) for key, rs in grouped.items()]


def _row_entry(record: SourceRecord, policy: ConceptPolicy) -> Entry:
    title = record.value(policy.subject_field) or record.text[:24]
    return Entry(
        id=entry_id(f"con_{record.collection}", title, salt=record.id, ascii_hint=title),
        title=title,
        type="concept",
        category=_concept_category(policy),
        facets=_facets_of(record, policy.facet_fields),
        sections=_row_sections(record, policy),
        fingerprint=record_source_fingerprint([record]),
    )


def _grouped_entry(
    records: list[SourceRecord], policy: ConceptPolicy, key: str, as_group: bool
) -> Entry:
    """field / table 粒度条目：一节一行；field 粒度以分组值为标题并写入 facets。"""
    sections = tuple(
        Section(
            key=f"row-{record.row}",
            title=record.value(policy.subject_field) or f"第{record.row + 1}行",
            body="\n".join(
                _field_lines(
                    record.cells,
                    tuple(c for c in record.cells if c != policy.subject_field),
                )
            ),
            sources=(record.anchor(),),
        )
        for record in records
    )
    facets = _facets_of(records[0], policy.facet_fields)
    return Entry(
        id=entry_id(
            f"con_{records[0].collection}", key, salt=f"{policy.table}|{key}", ascii_hint=key
        ),
        title=key,
        type="concept",
        category=_concept_category(policy, key if as_group else ""),
        facets=facets,
        sections=sections,
        fingerprint=record_source_fingerprint(records),
    )


# ---------- 链接推导 ----------


def _link_index(entries: list[Entry], records_by_id: dict[str, SourceRecord]) -> dict[str, list[str]]:
    """概念条目的「可匹配值」索引：值 → [`entry_id#section_key`]。"""
    index: dict[str, list[str]] = {}

    def register(value: str, target: str) -> None:
        for key in sorted(_match_keys(value)):
            targets = index.setdefault(key, [])
            if target not in targets:
                targets.append(target)

    for entry in entries:
        if entry.type != "concept":
            continue
        record = records_by_id.get(entry.record_ids[0]) if entry.record_ids else None
        if record is None:
            continue
        policy = CONCEPT_POLICIES.get(record.table)
        if policy is None or not policy.match_field:
            continue
        if policy.granularity == "row":
            register(entry.title, entry.id)
            continue
        for section in entry.sections:
            source = section.sources[0] if section.sources else None
            row_record = records_by_id.get(source.id) if source else None
            if row_record is not None:
                register(row_record.value(policy.match_field), join_anchor(entry.id, section.key))
    return index


def _attach_links(
    entries: list[Entry],
    records_by_id: dict[str, SourceRecord],
    index: dict[str, list[str]],
) -> None:
    """按值索引为 product 条目挂 `links.out`，并回填被指向条目的 `links.in`。"""
    by_id = {e.id: e for e in entries}
    for entry in entries:
        if entry.type != "product" or not entry.record_ids:
            continue
        record = records_by_id.get(entry.record_ids[0])
        if record is None:
            continue
        targets: list[str] = []
        for column in PRODUCT_LINK_COLUMNS.get(record.table, ()):
            for token in split_values(record.value(column)):
                targets.extend(index.get(token, ()))
        entry.links_out = tuple(dict.fromkeys(t for t in targets if t.split("#")[0] != entry.id))
        for target in entry.links_out:
            linked = by_id.get(target.split("#")[0])
            back = join_anchor(entry.id)
            # 产品→概念的反向链接可达上百条，展开概念页时全是噪声；完整反向关系由 manifest 扫描得到
            if linked is not None and len(linked.links_in) < MAX_IN_LINKS and back not in linked.links_in:
                linked.links_in = (*linked.links_in, back)


# ---------- category 聚合条目（W4：把属性过滤的结果固化成页面） ----------


@dataclass(frozen=True)
class CategoryFacet:
    """一个可聚合的 facet 列。"""

    column: str
    split: bool = True  # 多值单元格（`4U,5U`）是否拆成多个聚合页
    min_members: int = 5  # 少于该成员数不建页（避免长尾目录噪声）


CATEGORY_FACETS: dict[str, tuple[CategoryFacet, ...]] = {
    "球拍": (
        CategoryFacet("拍身重量(U)"),
        CategoryFacet("平衡点类别"),
        CategoryFacet("打法类型"),
        CategoryFacet("适合水平"),
        CategoryFacet("品牌", split=False),
    ),
    "羽毛球": (
        CategoryFacet("羽毛类别"),
        CategoryFacet("球头类别"),
        CategoryFacet("品牌", split=False),
    ),
    "球线": (CategoryFacet("材质"), CategoryFacet("品牌", split=False)),
    "手胶": (CategoryFacet("材质类别"), CategoryFacet("颜色"), CategoryFacet("品牌", split=False)),
    "球鞋": (CategoryFacet("品牌", split=False),),
}

# 聚合页每节最多列出的成员数（超出注明总数，导航时再按需展开下一节）
MAX_CATEGORY_MEMBERS = 12
CATEGORY_PRICE_COLUMN = "参考价"


def _category_key(value: str) -> str:
    """聚合键：`头重(进攻)` → `头重`（取括号前主干），避免同一取值分裂成两页。"""
    return _clean(re.split(r"[(（]", _clean(value))[0])


def _member_line(entry: Entry) -> str:
    """成员摘要行：型号 + 该表几个关键 facet（供 LLM 在聚合页里直接比较）。"""
    parts = [entry.title]
    parts += [f"{k}:{v}" for k, v in list(entry.facets.items())[1:6] if v]
    return "- " + "｜".join(p for p in parts if p)


def _price_range(records: list[SourceRecord]) -> str:
    prices: list[float] = []
    for record in records:
        digits = re.search(r"\d+(?:\.\d+)?", record.value(CATEGORY_PRICE_COLUMN))
        if digits:
            prices.append(float(digits.group()))
    if not prices:
        return ""
    return f"，价格区间 {min(prices):g}~{max(prices):g} 元（{len(prices)} 条有报价）"


def compile_category_entries(
    products: list[Entry],
    records_by_id: dict[str, SourceRecord],
) -> tuple[list[Entry], dict[str, list[str]]]:
    """按 facet 值把产品页聚合成 category 条目。

    返回 (聚合页列表, record_id → 所属聚合页 id)：映射在编译期顺手产出，
    供产品页反向挂链（避免事后 O(n²) 反查成员）。
    """
    by_table: dict[str, list[tuple[Entry, SourceRecord]]] = {}
    for entry in products:
        record = records_by_id.get(entry.record_ids[0]) if entry.record_ids else None
        if record is not None:
            by_table.setdefault(record.table, []).append((entry, record))

    categories: list[Entry] = []
    members_of_record: dict[str, list[str]] = {}
    for table, pairs in by_table.items():
        collection = pairs[0][1].collection
        for facet in CATEGORY_FACETS.get(table, ()):
            groups: dict[str, list[tuple[Entry, SourceRecord]]] = {}
            for pair in pairs:
                tokens = split_values(pair[1].value(facet.column))
                keys = [_category_key(t) for t in tokens] if facet.split else [_category_key(pair[1].value(facet.column))]
                for key in dict.fromkeys(k for k in keys if k):
                    groups.setdefault(key, []).append(pair)
            for value, members in sorted(groups.items()):
                if len(members) < facet.min_members:
                    continue
                entry = _category_entry(table, collection, facet, value, members)
                categories.append(entry)
                for _, record in members:
                    members_of_record.setdefault(record.id, []).append(entry.id)
    return categories, members_of_record


def _category_entry(
    table: str,
    collection: str,
    facet: CategoryFacet,
    value: str,
    members: list[tuple[Entry, SourceRecord]],
) -> Entry:
    title = f"{value} {table}"
    by_brand: dict[str, list[tuple[Entry, SourceRecord]]] = {}
    for pair in members:
        by_brand.setdefault(pair[1].value("品牌") or "其他", []).append(pair)
    ordered_brands = sorted(by_brand.items(), key=lambda item: (-len(item[1]), item[0]))

    brand_stats = "、".join(f"{brand} {len(pairs)}" for brand, pairs in ordered_brands)
    overview = [
        f"共 {len(members)} 款「{facet.column}={value}」的{table}。",
        f"按品牌分布：{brand_stats}{_price_range([r for _, r in members])}。",
        f"成员按品牌分节列出，每节最多 {MAX_CATEGORY_MEMBERS} 款。",
    ]
    # 概况节只是本页成员数的派生统计，正文未引用任何成员行 → 不锚定（锚点必须与渲染范围一致）
    sections = [Section(key="overview", title="聚合概况", body="\n".join(overview))]
    for index, (brand, pairs) in enumerate(ordered_brands):
        listed = pairs[:MAX_CATEGORY_MEMBERS]
        body = "\n".join(_member_line(entry) for entry, _ in listed)
        if len(pairs) > len(listed):
            body += f"\n（本节共 {len(pairs)} 款，仅列出前 {len(listed)} 款）"
        sections.append(
            Section(
                key=f"brand-{index}-{slugify(brand) or 'x'}",
                title=brand,
                body=body,
                sources=tuple(record.anchor() for _, record in listed),
            )
        )
    return Entry(
        id=entry_id(
            f"cat_{collection}", title, salt=f"{table}|{facet.column}|{value}", ascii_hint=f"{value} {table}"
        ),
        title=title,
        type="category",
        category=f"聚合视图/{table}/{facet.column}",
        facets={facet.column: value},
        sections=tuple(sections),
        fingerprint=record_source_fingerprint([r for _, r in members]),
    )


def link_categories(entries: list[Entry], members_of_record: dict[str, list[str]], value_index: dict[str, list[str]]) -> None:
    """挂链：产品页 ↔ 所属聚合页，聚合页 → 该取值的概念页章节（复用值索引）。"""
    by_id = {e.id: e for e in entries}
    for product in (e for e in entries if e.type == "product" and e.record_ids):
        targets = members_of_record.get(product.record_ids[0], [])
        product.links_out = tuple(dict.fromkeys((*product.links_out, *targets)))
        for target in targets:
            linked = by_id.get(target)
            back = join_anchor(product.id)
            if linked is not None and len(linked.links_in) < MAX_IN_LINKS and back not in linked.links_in:
                linked.links_in = (*linked.links_in, back)

    for category in (e for e in entries if e.type == "category"):
        value = next(iter(category.facets.values()), "")
        category.links_out = tuple(
            dict.fromkeys(
                t for t in value_index.get(value, ()) if t.split("#")[0] != category.id
            )
        )
        for target in category.links_out:
            linked = by_id.get(target.split("#")[0])
            back = join_anchor(category.id)
            if linked is not None and len(linked.links_in) < MAX_IN_LINKS and back not in linked.links_in:
                linked.links_in = (*linked.links_in, back)


# ---------- 全量编译与校验 ----------


def compile_entries(
    data_dir: Path | None = None,
    tables: tuple[SpecTable, ...] = ALL_TABLES,
    records: list[SourceRecord] | None = None,
) -> list[Entry]:
    """全量编译：CSV → 条目列表（不写盘），并挂好链接与增量指纹。"""
    source_records = records if records is not None else load_records(data_dir, tables)
    records_by_id = {r.id: r for r in source_records}
    specs = {t.name: t for t in ALL_TABLES}

    by_table: dict[str, list[SourceRecord]] = {}
    for record in source_records:
        by_table.setdefault(record.table, []).append(record)

    entries: list[Entry] = []
    for table_name, table_records in by_table.items():
        if table_name in _SPEC_TABLE_NAMES:
            entries.extend(compile_product_entries(r, specs[table_name]) for r in table_records)
        elif table_name in CONCEPT_POLICIES:
            entries.extend(compile_concept_entries(table_records, CONCEPT_POLICIES[table_name]))
        else:
            raise WikiCompileError(f"表 {table_name!r} 没有编译策略（规格表或 CONCEPT_POLICIES）")

    value_index = _link_index(entries, records_by_id)
    _attach_links(entries, records_by_id, value_index)
    categories, members_of_record = compile_category_entries(
        [e for e in entries if e.type == "product"], records_by_id
    )
    entries.extend(categories)
    link_categories(entries, members_of_record, value_index)
    logger.info(
        "Wiki 编译完成：%d 条记录 → %d 个条目（含 %d 个聚合页）",
        len(source_records), len(entries), len(categories),
    )
    return entries


def validate_entries(entries: list[Entry], records: list[SourceRecord]) -> None:
    """忠实性与覆盖率闸门。

    主条目（product/concept）承担三条铁律：每条记录被**恰好一个主条目**锚定、
    记录的每个非空单元格出现在其主条目文本中、facets 原样等于单元格值。
    category 页是**派生视图**（会重复锚定成员行），只校验成员可回溯到主条目、
    且聚合取值确实来自某成员行该列的主干 —— 不让聚合页变成第二个事实源。
    """
    records_by_id = {r.id: r for r in records}
    primary = [e for e in entries if e.type in ("product", "concept")]
    for entry in entries:
        if not entry.sections:
            raise WikiCompileError(f"条目 {entry.id} 没有任何章节")

    anchored: dict[str, list[Entry]] = {}
    for entry in primary:
        for rid in entry.record_ids:
            anchored.setdefault(rid, []).append(entry)

    missing = sorted(set(records_by_id) - set(anchored))
    unknown = sorted(set(anchored) - set(records_by_id))
    orphan = sorted(rid for rid, es in anchored.items() if len(es) > 1)
    if missing or unknown:
        raise WikiCompileError(
            f"锚定不完整：{len(missing)} 条记录未被覆盖（示例 {missing[:3]}）；"
            f"{len(unknown)} 个锚点指向不存在记录（示例 {unknown[:3]}）"
        )
    if orphan:
        raise WikiCompileError(f"记录被多个主条目锚定（应为 1:1）：{orphan[:5]}")

    for entry in primary:
        text = entry.to_markdown()
        for rid in entry.record_ids:
            record = records_by_id[rid]
            for column, value in record.cells.items():
                if value and value not in text:
                    raise WikiCompileError(
                        f"忠实性校验失败：条目 {entry.id} 未包含来源 {rid} 的字段「{column}」值 {value!r}"
                    )
        for column, facet in entry.facets.items():
            values = {records_by_id[rid].value(column) for rid in entry.record_ids}
            if facet not in values:
                raise WikiCompileError(
                    f"facets 必须原样来自 CSV：条目 {entry.id} 的「{column}」={facet!r} 不在来源行中"
                )

    for entry in (e for e in entries if e.type == "category"):
        for column, value in entry.facets.items():
            keys: set[str] = set()
            for rid in entry.record_ids:
                cell = records_by_id[rid].value(column)
                keys.add(_category_key(cell))
                keys.update(t for t in (_category_key(x) for x in split_values(cell)) if t)
            if value not in keys:
                raise WikiCompileError(
                    f"聚合页取值必须来自成员行：条目 {entry.id} 的「{column}」={value!r} 不在成员取值 {sorted(keys)[:5]} 中"
                )
        untraceable = [rid for rid in entry.record_ids if not anchored.get(rid)]
        if untraceable:
            raise WikiCompileError(f"聚合页 {entry.id} 的成员无法回溯到主条目：{untraceable[:3]}")
