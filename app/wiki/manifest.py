"""Wiki 清单与目录：`record_id ↔ entry_id` 反查、来源指纹、TOC 构建与幂等落盘。

派生内容一律落文件（`data/wiki/`，可 git review），DB 不参与（plan §5.2）：

- `entries/<entry_id>.md`：条目页（frontmatter + 章节正文）；
- `manifest.json`：条目摘要（含每节 record 锚点，供 `context_precision_strict` 回算）+ 反查表 + 指纹；
- `toc.json`：两级目录（分类 → 表 → 条目行），是在线 orient 的第一眼输入。

幂等约定：全局 `source_fingerprint` 未变 → 整库零写入；变了则逐条目比 digest，只重写内容变了的页。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.ingest.serializer import ALL_TABLES
from app.models.spec import SpecTable
from app.wiki.schema import COMPILED_VERSION, Entry, split_anchor

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
TOC_VERSION = 1
ENTRIES_SUBDIR = "entries"
MANIFEST_NAME = "manifest.json"
TOC_NAME = "toc.json"


@dataclass
class WriteReport:
    """一次落盘的结果（CLI 与测试的断言对象）。"""

    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.written or self.deleted)


@dataclass
class Manifest:
    """`manifest.json` 的内存表示。"""

    version: int = MANIFEST_VERSION
    source_fingerprint: str = ""
    entries: dict[str, dict] = field(default_factory=dict)
    record_to_entries: dict[str, list[str]] = field(default_factory=dict)

    # ---------- 查询 ----------

    def entry_ids_for_record(self, record_id: str) -> list[str]:
        """原始 record → 所属条目（hybrid 检索命中的行反查条目用，plan §4.2 第 3 步）。"""
        return list(self.record_to_entries.get(record_id, ()))

    def entry_summary(self, eid: str) -> dict | None:
        return self.entries.get(eid)

    def title(self, eid: str) -> str:
        link_entry, _, _ = eid.partition("#")
        return str((self.entries.get(link_entry) or {}).get("title", link_entry))

    @property
    def stats(self) -> dict:
        return {
            "entries": len(self.entries),
            "records": len(self.record_to_entries),
            "by_type": _count_types(self.entries),
        }

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "source_fingerprint": self.source_fingerprint,
            "stats": self.stats,
            "entries": self.entries,
            "record_to_entries": self.record_to_entries,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Manifest:
        return cls(
            version=int(data.get("version", MANIFEST_VERSION)),
            source_fingerprint=str(data.get("source_fingerprint", "")),
            entries=dict(data.get("entries") or {}),
            record_to_entries={
                k: list(v) for k, v in (data.get("record_to_entries") or {}).items()
            },
        )

    def save(self, wiki_dir: Path) -> None:
        wiki_dir.mkdir(parents=True, exist_ok=True)
        path = wiki_dir / MANIFEST_NAME
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, wiki_dir: Path) -> Manifest | None:
        path = wiki_dir / MANIFEST_NAME
        if not path.exists():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _count_types(entries: dict[str, dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in entries.values():
        key = str(summary.get("type", ""))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _entry_summary(entry: Entry, filename: str) -> dict:
    """manifest 里的条目摘要：只存导航/评测需要的结构化信息，正文仍以其 md 为准。"""
    return {
        "title": entry.title,
        "type": entry.type,
        "category": entry.category,
        "aliases": list(entry.aliases),
        "file": f"{ENTRIES_SUBDIR}/{filename}",
        "digest": entry.digest(),
        "fingerprint": entry.fingerprint,
        "records": list(entry.record_ids),
        "links_out": list(entry.links_out),
        "sections": [
            {"key": s.key, "title": s.title, "records": [a.id for a in s.sources]}
            for s in entry.sections
        ],
    }


def build_manifest(entries: list[Entry], source_fingerprint: str) -> Manifest:
    """由条目列表构建 manifest（含 record → entries 反查表）。"""
    manifest = Manifest(source_fingerprint=source_fingerprint)
    for entry in entries:
        summary = _entry_summary(entry, f"{entry.id}.md")
        if entry.id in manifest.entries:
            raise ValueError(f"条目 id 重复：{entry.id}")
        manifest.entries[entry.id] = summary
        for rid in entry.record_ids:
            targets = manifest.record_to_entries.setdefault(rid, [])
            if entry.id not in targets:
                targets.append(entry.id)
    return manifest


def source_fingerprint(
    data_dir: Path, tables: tuple[SpecTable, ...] = ALL_TABLES
) -> str:
    """全部源 CSV 的字节指纹（wiki 是否落后于事实源的唯一判据，plan 风险 5）。

    种子含结构/清单版本号：模板渲染规则升级（`COMPILED_VERSION` +1）后即使 CSV 未变也会判定需重编译。
    """
    digest = hashlib.sha256()
    digest.update(f"schema=v{COMPILED_VERSION}|manifest=v{MANIFEST_VERSION}\x00".encode("utf-8"))
    for table in tables:
        path = data_dir / table.csv_file
        digest.update(table.csv_file.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
    return f"sha256:{digest.hexdigest()}"


def build_toc(entries: list[Entry]) -> dict:
    """两级目录：第一级分类（计数），第二级按表列出条目行（标题 + 别名/facet 摘要）。

    条目行是给 LLM 看的压缩视图，`hint` 让它在不展开条目的前提下判断相关性。
    """
    groups: dict[str, dict[str, list[Entry]]] = {}
    for entry in entries:
        parts = entry.category.split("/")
        level1 = parts[0] if parts else "未分类"
        level2 = parts[1] if len(parts) > 1 else "(其他)"
        groups.setdefault(level1, {}).setdefault(level2, []).append(entry)

    categories: list[dict] = []
    for level1 in sorted(groups):
        tables = []
        for level2 in sorted(groups[level1]):
            items = sorted(groups[level1][level2], key=lambda e: (e.type, e.title, e.id))
            tables.append(
                {
                    "path": f"{level1}/{level2}",
                    "name": level2,
                    "count": len(items),
                    "entries": [
                        {
                            "id": e.id,
                            "title": e.title,
                            "type": e.type,
                            "hint": e.toc_hint(),
                            "sections": [s.title for s in e.sections],
                        }
                        for e in items
                    ],
                }
            )
        categories.append({"name": level1, "count": sum(t["count"] for t in tables), "tables": tables})

    return {
        "version": TOC_VERSION,
        "total_entries": len(entries),
        "categories": categories,
    }


def save_toc(wiki_dir: Path, toc: dict) -> None:
    (wiki_dir / TOC_NAME).write_text(
        json.dumps(toc, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def entry_path(wiki_dir: Path, eid: str) -> Path:
    """条目 id → 落盘路径（入口校验 id 合法性，避免越目录写入）。"""
    return wiki_dir / ENTRIES_SUBDIR / f"{split_anchor(eid)[0]}.md"


def load_entry(wiki_dir: Path, eid: str) -> Entry | None:
    """按 id 读回条目（正文从 md 解析；不存在返回 None）。"""
    path = entry_path(wiki_dir, eid)
    if not path.exists():
        return None
    return Entry.from_markdown(path.read_text(encoding="utf-8"))


def write_wiki(
    wiki_dir: Path,
    entries: list[Entry],
    fingerprint: str,
    force: bool = False,
    dry_run: bool = False,
) -> WriteReport:
    """幂等落盘：指纹未变整库零写入；否则只重写 digest 变化的页，并清理已消失的页。"""
    report = WriteReport()
    wiki_dir.mkdir(parents=True, exist_ok=True)
    entries_dir = wiki_dir / ENTRIES_SUBDIR
    entries_dir.mkdir(parents=True, exist_ok=True)

    existing = Manifest.load(wiki_dir)
    if (
        not force
        and existing is not None
        and existing.source_fingerprint == fingerprint
        and existing.version == MANIFEST_VERSION
        and (wiki_dir / TOC_NAME).exists()
        and set(existing.entries) == {e.id for e in entries}
    ):
        report.skipped = [e.id for e in entries]
        logger.info("Wiki 源数据未变化（%s），零写入", fingerprint[:18])
        return report

    old_entries = dict(existing.entries) if existing else {}
    for entry in entries:
        path = entries_dir / f"{entry.id}.md"
        summary = old_entries.get(entry.id) or {}
        if not force and path.exists() and summary.get("digest") == entry.digest():
            report.skipped.append(entry.id)
            continue
        report.written.append(entry.id)
        if not dry_run:
            path.write_text(entry.to_markdown(), encoding="utf-8")

    for stale in _stale_ids(existing, entries):
        report.deleted.append(stale)
        if not dry_run:
            path = entries_dir / f"{stale}.md"
            if path.exists():
                path.unlink()

    if not dry_run:
        manifest = build_manifest(entries, fingerprint)
        manifest.save(wiki_dir)
        save_toc(wiki_dir, build_toc(entries))
    logger.info(
        "Wiki 落盘：写入 %d，跳过 %d，删除 %d", len(report.written), len(report.skipped), len(report.deleted)
    )
    return report


def _stale_ids(existing: Manifest | None, entries: list[Entry]) -> list[str]:
    """旧 manifest 里有、新结果里没有的条目 → 需要删掉对应 md。"""
    if existing is None:
        return []
    keep = {e.id for e in entries}
    return sorted(set(existing.entries) - keep)


def reset_wiki(wiki_dir: Path) -> None:
    """清空 `data/wiki/`（重建用；只删派生目录，不触碰 data/processed 事实源）。"""
    if wiki_dir.exists():
        shutil.rmtree(wiki_dir)
