"""Wiki 编译 CLI：`data/processed/` 17 张 CSV → `data/wiki/`（条目页 + manifest + toc）。

用法：
    .venv/Scripts/python.exe -m scripts.build_wiki                    # 幂等编译（纯模板，零 LLM）
    .venv/Scripts/python.exe -m scripts.build_wiki --index            # 编译 + 入 wiki_page/wiki_section
    .venv/Scripts/python.exe -m scripts.build_wiki --force            # 全量重写
    .venv/Scripts/python.exe -m scripts.build_wiki --dry-run          # 只看会写哪些页
    .venv/Scripts/python.exe -m scripts.build_wiki --check            # 只校验（stale/非法即退出码 1）

编译本身零 LLM、全离线；`--index` 需要百炼 embedding 与 data/chroma。
LLM 补概念段落与忠实性校验在 W4 接入。
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from app.core.config import get_settings
from app.ingest.embedder import build_embedder
from app.ingest.serializer import ALL_TABLES
from app.ingest.store import VectorStore
from app.models.spec import SpecTable
from app.wiki.compile import compile_entries, load_records, validate_entries
from app.wiki.indexer import (
    WIKI_PAGE_COLLECTION,
    WIKI_SECTION_COLLECTION,
    index_wiki,
    save_index_state,
)
from app.wiki.manifest import (
    MANIFEST_VERSION,
    Manifest,
    load_entry,
    source_fingerprint,
    write_wiki,
)

logger = logging.getLogger(__name__)


def build(
    wiki_dir: Path,
    data_dir: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    tables: tuple[SpecTable, ...] = ALL_TABLES,
) -> int:
    """编译 + 校验 + 落盘，返回进程退出码。"""
    settings = get_settings()
    data_dir = data_dir or settings.processed_data_dir
    records = load_records(data_dir, tables)
    entries = compile_entries(data_dir=data_dir, tables=tables, records=records)
    validate_entries(entries, records)

    fingerprint = source_fingerprint(data_dir, tables)
    report = write_wiki(wiki_dir, entries, fingerprint, force=force, dry_run=dry_run)
    types = Counter(e.type for e in entries)
    sections = sum(len(e.sections) for e in entries)
    linked = sum(1 for e in entries if e.links_out)
    print(
        f"{'[dry-run] ' if dry_run else ''}{len(records)} 条记录 → {len(entries)} 个条目"
        f"（product {types['product']} / concept {types['concept']} / category {types['category']}），"
        f"{sections} 个章节，{linked} 个条目有出链"
    )
    print(
        f"落盘：写入 {len(report.written)}，跳过 {len(report.skipped)}，删除 {len(report.deleted)}"
        f" → {wiki_dir}"
    )
    return 0


def index(wiki_dir: Path, force: bool = False) -> int:
    """把已编译的条目页索引进 Chroma（wiki_page / wiki_section，需百炼 embedding）。"""
    settings = get_settings()
    manifest = Manifest.load(wiki_dir)
    if manifest is None:
        print(f"wiki 尚未编译，先运行：python -m scripts.build_wiki", file=sys.stderr)
        return 1
    entries = [entry for entry in (load_entry(wiki_dir, eid) for eid in manifest.entries) if entry]
    store = VectorStore(persist_dir=settings.chroma_dir)
    embedder = build_embedder(settings)
    report = index_wiki(store, embedder, entries, force=force, batch_size=settings.ingest_batch_size)
    save_index_state(wiki_dir, manifest.source_fingerprint, report)
    print(
        f"Wiki 索引：wiki_page {store.count(WIKI_PAGE_COLLECTION)} 条 / "
        f"wiki_section {store.count(WIKI_SECTION_COLLECTION)} 条；"
        f"本次重嵌 {report.embedded}，跳过 {report.skipped}，删除 {len(report.deleted)}"
    )
    return 0


def check(wiki_dir: Path, data_dir: Path | None = None, tables: tuple[SpecTable, ...] = ALL_TABLES) -> int:
    """校验已落盘的 wiki 是否新鲜（供 /admin/health 与 CI 复用）。"""
    settings = get_settings()
    data_dir = data_dir or settings.processed_data_dir
    manifest = Manifest.load(wiki_dir)
    if manifest is None:
        print(f"wiki 尚未编译：{wiki_dir}", file=sys.stderr)
        return 1
    if manifest.version != MANIFEST_VERSION:
        print(f"wiki manifest 版本过旧：{manifest.version}", file=sys.stderr)
        return 1
    current = source_fingerprint(data_dir, tables)
    if manifest.source_fingerprint != current:
        print("wiki 落后于源 CSV，需要重编译（python -m scripts.build_wiki）", file=sys.stderr)
        return 1
    missing = [
        eid for eid, summary in manifest.entries.items()
        if not (wiki_dir / str(summary.get("file", ""))).exists()
    ]
    if missing:
        print(f"缺失 {len(missing)} 个条目文件（示例 {missing[:3]}）", file=sys.stderr)
        return 1
    print(f"wiki 新鲜：{manifest.stats}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="badminton-rag Wiki 编译（W1：纯模板）")
    parser.add_argument("--out", type=Path, default=None, help="wiki 输出目录（默认 data/wiki）")
    parser.add_argument("--data-dir", type=Path, default=None, help="源 CSV 目录（默认 data/processed）")
    parser.add_argument("--skip-llm", action="store_true", help="不调 LLM（W1 唯一形态，保留供后续对照）")
    parser.add_argument("--force", action="store_true", help="忽略指纹与 digest，全量重写")
    parser.add_argument("--dry-run", action="store_true", help="只报告将写入哪些页")
    parser.add_argument("--check", action="store_true", help="只校验已落盘 wiki 是否新鲜")
    parser.add_argument("--index", action="store_true", help="编译后写入 wiki_page/wiki_section（需百炼 embedding）")
    parser.add_argument("--index-only", action="store_true", help="跳过编译，只重建向量索引")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not args.skip_llm:
        logger.warning("W1 编译器不接 LLM，`--skip-llm` 之外的形态在 W4 提供；本次按模板编译执行")
    wiki_dir = args.out or settings.wiki_dir
    if args.check:
        sys.exit(check(wiki_dir, args.data_dir))
    if args.index_only:
        sys.exit(index(wiki_dir, force=args.force))
    code = build(wiki_dir, args.data_dir, force=args.force, dry_run=args.dry_run)
    if code == 0 and args.index and not args.dry_run:
        code = index(wiki_dir, force=args.force)
    sys.exit(code)


if __name__ == "__main__":
    main()
