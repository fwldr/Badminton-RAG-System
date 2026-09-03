"""Wiki 知识模型：条目（Entry）/ 章节（Section）/ 来源锚点（SourceAnchor）与 id、slug 规则。

设计约束（见 `badminton-rag-llm-wiki-plan.md` §3.2）：

- 条目 id 必须同时是合法的 Chroma id/collection 名片段：仅 `[A-Za-z0-9._-]`，且**绝不含冒号**
  （`app/rag/retriever.py::_fetch_record` 用 `rpartition(":")` 拆 record id）；中文标题一律先转
  ASCII slug，再追加短 hash 防重名，标题→slug 的映射由 manifest.json 承载。
- frontmatter 承载结构化事实（`facets` 只能来自 CSV 列，永不经 LLM），正文承载叙述与章节；
  每个章节自带 `sources` 锚点（表名 + record id + 文件行号），是「答案可信」与
  「`context_precision_strict` 可回算」的同一个机制。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

import yaml

# 条目类型：product（规格表行）/ concept（知识表主题）/ category（facet 聚合，W4）/ source（文档，W4）
ENTRY_TYPES: tuple[str, ...] = ("product", "concept", "category", "source")

# 条目正文结构版本：渲染契约变更时 +1，全局源指纹据此作废快路径（条目级指纹只反映源数据）
# v2 = 聚合页锚点范围与渲染压缩（章节级只列 record id；概况节不再锚定未渲染的全部成员行）
COMPILED_VERSION = 2

# 条目 id：首尾字母数字，中间允许 . _ -，长度 3~401（Chroma 上限 512 内留余量）
_ENTRY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,400}$")

_SLUG_MAX_LEN = 48
_ID_HASH_LEN = 6
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_SECTION_HEADING_RE = re.compile(r"^## [^\n]*$", re.M)


def slugify(text: str, max_len: int = _SLUG_MAX_LEN) -> str:
    """任意文本 → ASCII slug（小写、连字符分隔）；中文经 NFKD 后不保留（无注音依赖）。

    例：`威克多 VICTOR ARS-90KⅡ` → `victor-ars-90k-ii`；纯中文标题 → 空串（由 id 短 hash 兜底）。
    """
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    parts = re.findall(r"[A-Za-z0-9]+", ascii_text)
    return "-".join(parts).lower()[:max_len].strip("-")


def entry_id(prefix: str, title: str, salt: str = "", ascii_hint: str = "") -> str:
    """生成条目 id：`{prefix}_{slug}_{短hash}`（hash 取 prefix|title|salt，稳定且防重名）。

    `salt` 用原始 record id 或分组键，保证同名条目（不同来源行）不碰撞。
    """
    digest = hashlib.sha1(f"{prefix}|{title}|{salt}".encode("utf-8")).hexdigest()
    slug = slugify(ascii_hint or title) or "x"
    return f"{prefix}_{slug}_{digest[:_ID_HASH_LEN]}"


def is_valid_entry_id(value: str) -> bool:
    """条目 id 合法性：满足 Chroma 字符规则且不含冒号。"""
    return ":" not in value and bool(_ENTRY_ID_RE.match(value))


def validate_entry_id(value: str) -> str:
    """校验条目 id，非法则抛 ValueError（编译器与加载器都用它把守入口）。"""
    if not is_valid_entry_id(value):
        raise ValueError(f"非法条目 id：{value!r}（要求匹配 {_ENTRY_ID_RE.pattern} 且不含冒号）")
    return value


def split_anchor(link: str) -> tuple[str, str]:
    """拆链接引用：`ent_x#sec-y` → (`ent_x`, `sec-y`)；无锚点时 section 为空串。"""
    entry, _, section = link.partition("#")
    return entry, section


def join_anchor(eid: str, section_key: str = "") -> str:
    """拼链接引用（section_key 为空时只到条目级）。"""
    return f"{eid}#{section_key}" if section_key else eid


@dataclass(frozen=True)
class SourceAnchor:
    """一条内容的原始出处：表名（中文）+ record id + CSV 文件 + 行号（可选到字段级）。"""

    table: str
    id: str
    file: str
    row: int
    field: str = ""

    def to_dict(self) -> dict:
        data: dict = {"table": self.table, "id": self.id, "file": self.file, "row": self.row}
        if self.field:
            data["field"] = self.field
        return data

    @classmethod
    def from_dict(cls, data: dict) -> SourceAnchor:
        return cls(
            table=str(data.get("table", "")),
            id=str(data.get("id", "")),
            file=str(data.get("file", "")),
            row=int(data.get("row", -1)),
            field=str(data.get("field", "") or ""),
        )


@dataclass(frozen=True)
class Section:
    """条目章节：标题 + markdown 正文 + 该章内容的来源锚点。"""

    key: str
    title: str
    body: str
    sources: tuple[SourceAnchor, ...] = ()
    generated: bool = False  # True 表示正文含 LLM 生成段落（W4 起才可能出现）

    def to_dict(self) -> dict:
        data: dict = {"key": self.key, "title": self.title}
        if self.sources:
            data["sources"] = [a.to_dict() for a in self.sources]
        if self.generated:
            data["generated"] = True
        return data

    @classmethod
    def from_dict(cls, data: dict, body: str = "", known: dict[str, SourceAnchor] | None = None) -> Section:
        anchors: list[SourceAnchor] = []
        for raw in data.get("sources") or []:
            if isinstance(raw, str):
                anchors.append((known or {}).get(raw) or SourceAnchor(table="", id=raw, file="", row=-1))
            else:
                anchors.append(SourceAnchor.from_dict(raw))
        return cls(
            key=str(data.get("key", "")),
            title=str(data.get("title", "")),
            body=body,
            sources=tuple(anchors),
            generated=bool(data.get("generated", False)),
        )


@dataclass
class Entry:
    """一个 Wiki 条目（实体页 / 概念页 / 聚合页 / 文档页）。"""

    id: str
    title: str
    type: str
    category: str  # 目录分类路径，如 `装备规格/球拍`、`器材常识/规格常识`
    aliases: tuple[str, ...] = ()
    facets: dict[str, str] = field(default_factory=dict)
    sections: tuple[Section, ...] = ()
    links_out: tuple[str, ...] = ()
    links_in: tuple[str, ...] = ()  # 编译期由 open 链接反向回填
    fingerprint: str = ""  # 该条目所用原始行内容哈希（增量重编译判据）
    template_only: bool = True

    def __post_init__(self) -> None:
        validate_entry_id(self.id)
        if self.type not in ENTRY_TYPES:
            raise ValueError(f"未知条目类型：{self.type!r}")
        if not self.title.strip():
            raise ValueError(f"条目 {self.id} 缺少标题")

    # ---------- 来源与回溯 ----------

    @property
    def sources(self) -> tuple[SourceAnchor, ...]:
        """条目级来源 = 各章节来源按出现顺序去重。"""
        seen: dict[str, SourceAnchor] = {}
        for section in self.sections:
            for anchor in section.sources:
                seen.setdefault(anchor.id, anchor)
        return tuple(seen.values())

    @property
    def record_ids(self) -> tuple[str, ...]:
        """该条目覆盖的原始 record id 集合（`context_precision_strict` 的回算依据）。"""
        return tuple(a.id for a in self.sources)

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    # ---------- 索引与目录文档 ----------

    def page_document(self) -> str:
        """条目级索引文档（`wiki_page` collection）：标题 + 别名 + facets + 章节标题。"""
        parts = [self.title]
        if self.aliases:
            parts.append(" ".join(self.aliases[:4]))
        if self.facets:
            parts.append("，".join(f"{k}：{v}" for k, v in self.facets.items()))
        if self.sections:
            parts.append("章节：" + "｜".join(s.title for s in self.sections))
        return "。".join(p for p in parts if p)

    def toc_hint(self, max_facets: int = 4) -> str:
        """目录行括号内的提示串：别名 + 少量 facet 值（供 orient 判断相关性，不展开条目）。"""
        bits = list(self.aliases[:2])
        bits += [v for k, v in list(self.facets.items())[:max_facets] if v]
        return "|".join(dict.fromkeys(bits))

    # ---------- markdown 读写 ----------

    def frontmatter(self) -> dict:
        """frontmatter dict（顺序即渲染顺序，保证同内容产出同一份文本）。"""
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "category": self.category,
            "aliases": list(self.aliases),
            "facets": dict(self.facets),
            "sections": [
                # 章节级只列 record id：完整出处在条目级 `sources` 保留一份
                {**s.to_dict(), "sources": [a.id for a in s.sources]} if s.sources else s.to_dict()
                for s in self.sections
            ],
            "links": {"out": list(self.links_out), "in": list(self.links_in)},
            "sources": [a.to_dict() for a in self.sources],
            "compiled": {
                "version": COMPILED_VERSION,
                "template_only": self.template_only,
                "source_fingerprint": self.fingerprint,
            },
        }

    def to_markdown(self) -> str:
        """渲染为 `---` YAML frontmatter + `##` 章节正文的条目文件内容。"""
        front = yaml.safe_dump(
            self.frontmatter(),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=10_000,
        )
        blocks = [f"## {s.title}\n{s.body.strip()}\n" for s in self.sections]
        return f"---\n{front}---\n\n" + "\n".join(blocks)

    @classmethod
    def from_markdown(cls, text: str) -> Entry:
        """从条目文件内容还原（正文按章节顺序与 frontmatter `sections` 对齐）。"""
        match = _FRONTMATTER_RE.match(text)
        if not match:
            raise ValueError("条目文件缺少 `---` frontmatter")
        data = yaml.safe_load(match.group(1)) or {}
        bodies = [b.strip() for b in _SECTION_HEADING_RE.split(match.group(2))[1:]]
        raw_sections = data.get("sections") or []
        if len(raw_sections) != len(bodies):
            raise ValueError(
                f"条目 {data.get('id')} frontmatter 章节数({len(raw_sections)})"
                f"与正文标题数({len(bodies)})不一致"
            )
        links = data.get("links") or {}
        compiled = data.get("compiled") or {}
        entry_anchors = [SourceAnchor.from_dict(a) for a in (data.get("sources") or [])]
        known = {a.id: a for a in entry_anchors}
        return cls(
            id=str(data["id"]),
            title=str(data.get("title", "")),
            type=str(data.get("type", "")),
            category=str(data.get("category", "")),
            aliases=tuple(str(a) for a in (data.get("aliases") or [])),
            facets={str(k): str(v) for k, v in (data.get("facets") or {}).items()},
            sections=tuple(
                Section.from_dict(raw, body, known) for raw, body in zip(raw_sections, bodies)
            ),
            links_out=tuple(str(x) for x in (links.get("out") or [])),
            links_in=tuple(str(x) for x in (links.get("in") or [])),
            fingerprint=str(compiled.get("source_fingerprint", "")),
            template_only=bool(compiled.get("template_only", True)),
        )

    def digest(self) -> str:
        """条目内容指纹（frontmatter + 正文），manifest 用它判定「这一页要不要重写」。"""
        return f"sha256:{hashlib.sha256(self.to_markdown().encode('utf-8')).hexdigest()}"


def entry_filename(eid: str) -> str:
    """条目 id → 落盘文件名。"""
    return f"{validate_entry_id(eid)}.md"
