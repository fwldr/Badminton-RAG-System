"""入库流水线：17 张 CSV 规格表 → 行级序列化 → 百炼 embedding 生成向量 → 批量写入 Chroma；
PDF/图片/文本文档可经 --dir 批量入库（每文件一 collection，按文件 hash 幂等）。

用法：
    .venv/Scripts/python.exe -m app.ingest.pipeline                        # 只入 17 张 CSV（全量，现状）
    .venv/Scripts/python.exe -m app.ingest.pipeline --sync                 # 17 张 CSV 增量同步
    .venv/Scripts/python.exe -m app.ingest.pipeline --sync --tables 球拍   # 只增量同步指定表
    .venv/Scripts/python.exe -m app.ingest.pipeline --dir data/raw_docs    # CSV 照常 + 追加目录内文档
    .venv/Scripts/python.exe -m app.ingest.pipeline --dir data/raw_docs --only-docs  # 只入文档

CSV 两种入库模式：默认全量 upsert（幂等覆盖）；--sync 按行比对增量
（行 id = `{collection}:{sha1(主键)[:12]}` 与行位置无关，metadata 带内容 digest，
只对 digest 变化的行重嵌、CSV 已删除的行自动清理）——改 3 行就只调 3 次 embedding。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.ingest.doc_ingest import IMAGE_EXTS, ingest_document
from app.ingest.embedder import Embedder, build_embedder
from app.ingest.ocr import build_ocr_engine
from app.ingest.serializer import ALL_TABLES
from app.ingest.store import VectorStore, collection_name
from app.ingest.vision_embed import build_vision_embedder
from app.models.spec import SpecTable

logger = logging.getLogger(__name__)

# CLI 批量入库支持的真实扩展名（按扩展名分派，不信任 Content-Type）
DOC_EXTS = {"pdf", "txt", "md", "csv"} | IMAGE_EXTS


def load_rows(csv_file: Path) -> list[dict]:
    """读取 UTF-8 带 BOM 的 CSV 为 dict 列表。"""
    with open(csv_file, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------- 行级稳定 id 与内容指纹（增量入库的支点） ----------


def _pk_of(table: SpecTable, row: dict) -> str:
    """主键串。主键列值全为空时回退「整行内容哈希」——id 随内容变，等价一次性记录。"""
    vals = [str(row.get(c) or "").strip() for c in table.primary_key]
    if any(vals):
        return "|".join(vals)
    return "\x1e".join(
        f"{k}={str(v or '').strip()}" for k, v in sorted(row.items()) if k is not None
    )


def row_ids(table: SpecTable, rows: list[dict]) -> list[str]:
    """批量生成行 id：`{collection}:{sha1(主键串)[:12]}`，与行在文件中的位置无关。

    - 位置无关 → 中间插行/删行不影响其余行的 id，增量同步只重嵌真正变化的行；
    - 同键重复（源数据质量，如球拍「双刃7」两行）按出现序加 `-2`/`-3` 后缀消歧，
      upsert 不互相覆盖；删除同组首行会使后续行序号前移，代价仅组内多一次重嵌；
    - id 保持 `{collection}:{不含冒号的键}` 形态，`rpartition(":")` 反查 collection 的
      既有逻辑（retriever._fetch_record / navigator._source_table）不受影响。
    """
    coll = collection_name(table.name)
    seen: dict[str, int] = {}
    ids: list[str] = []
    for row in rows:
        h = hashlib.sha1(_pk_of(table, row).encode("utf-8")).hexdigest()[:12]
        n = seen.get(h, 0) + 1
        seen[h] = n
        ids.append(f"{coll}:{h}" if n == 1 else f"{coll}:{h}-{n}")
    dups = sum(c - 1 for c in seen.values() if c > 1)
    if dups:
        logger.warning("表 %s 有 %d 行主键重复，已按出现序加后缀消歧", table.name, dups)
    return ids


def _row_digest(text: str, meta: dict) -> str:
    """重嵌判据 = 序列化文本 + 过滤 metadata（不含 digest 自身）。

    与 wiki indexer._doc_digest 同一思想：只有将要入库的内容真正变化才重嵌；
    列名增删、空白变化等不影响序列化文本的编辑自动零成本。
    """
    payload = text + "\x1f" + json.dumps(meta, ensure_ascii=False, sort_keys=True)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _existing_digests(store: VectorStore, table: str, ids: list[str]) -> dict[str, str]:
    """已入库文档的 id → digest（分批按 id 查 metadata，不拉向量）。"""
    found: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        for hit in store.get(table, batch):
            found[hit["id"]] = str((hit.get("metadata") or {}).get("digest", ""))
    return found


def _build_metadata(row: dict, fields: tuple[str, ...], source_name: str = "") -> dict:
    """仅保留非空的可过滤字段；来源文件列缺失时回退为表名（知识表 CSV 无此列）。"""
    meta = {}
    for field in fields:
        value = str(row.get(field) or "").strip()
        if not value and field == "来源文件":
            value = source_name
        if value:
            meta[field] = value
    return meta


def ingest_table(
    store: VectorStore,
    embedder: Embedder,
    table: SpecTable,
    data_dir: Path,
    batch_size: int = 16,
) -> int:
    """入库单张表，返回入库条数。"""
    rows = load_rows(data_dir / table.csv_file)
    if not rows:
        logger.warning("表 %s 无数据，跳过", table.name)
        return 0

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    total = 0

    def flush() -> None:
        nonlocal total
        if not ids:
            return
        embeddings = embedder.embed(documents)
        store.add(table.name, ids, documents, metadatas, embeddings)
        logger.info(
            "%s 已入库 %d 条（累计 %d/%d）",
            table.name, len(ids), total + len(ids), len(rows),
        )
        total += len(ids)
        ids.clear()
        documents.clear()
        metadatas.clear()

    for rid, row in zip(row_ids(table, rows), rows):
        text = table.serializer(row)
        if not text:
            continue
        # id = `{collection}:{sha1(主键)[:12]}`（与行位置无关，键内无冒号）；
        # metadata 带内容 digest，供 --sync 增量比对（重入库幂等覆盖、不产生双份）
        meta = _build_metadata(row, table.metadata_fields, Path(table.csv_file).stem)
        meta["digest"] = _row_digest(text, dict(meta))
        ids.append(rid)
        documents.append(text)
        metadatas.append(meta)
        if len(ids) >= batch_size:
            flush()
    flush()
    return total


def run_ingest(
    store: VectorStore,
    embedder: Embedder,
    tables: tuple[SpecTable, ...] = ALL_TABLES,
    data_dir: Path | None = None,
    batch_size: int = 16,
) -> dict[str, int]:
    """入库全部表，返回 {表名: 入库条数}。"""
    settings = get_settings()
    data_dir = data_dir or settings.processed_data_dir
    summary: dict[str, int] = {}
    for table in tables:
        summary[table.name] = ingest_table(store, embedder, table, data_dir, batch_size)
    return summary


@dataclass
class SyncReport:
    """单表增量同步结果（CLI 打印与测试断言对象）。"""

    table: str
    embedded: int = 0                              # 实际重嵌的行数（新增 + 内容变化）
    skipped: int = 0                               # digest 未变、零成本跳过的行数
    deleted: list[str] = field(default_factory=list)  # 库里还在、CSV 已消失的陈旧 id

    def to_line(self) -> str:
        return f"{self.table}: 重嵌 {self.embedded} / 跳过 {self.skipped} / 删除 {len(self.deleted)}"


def sync_table(
    store: VectorStore,
    embedder: Embedder,
    table: SpecTable,
    data_dir: Path | None = None,
    batch_size: int = 16,
) -> SyncReport:
    """单表增量同步：只对内容变化（含新增）的行重嵌，自动清理已删除/迁移前的陈旧行。

    幂等：连续跑两次，第二次 embedded=0、deleted=0。首次对旧 `{coll}:{行号}` 库跑 sync，
    全部行按新主键 id 重嵌一次（= 迁移），旧行号 id 落入陈旧集被删除——迁移无需单独命令。
    """
    settings = get_settings()
    data_dir = data_dir or settings.processed_data_dir
    report = SyncReport(table=table.name)

    rows = load_rows(data_dir / table.csv_file)
    desired: dict[str, tuple[str, dict]] = {}
    for rid, row in zip(row_ids(table, rows), rows):
        text = table.serializer(row)
        if not text:
            continue
        meta = _build_metadata(row, table.metadata_fields, Path(table.csv_file).stem)
        meta["digest"] = _row_digest(text, dict(meta))
        desired[rid] = (text, meta)

    old = _existing_digests(store, table.name, list(desired))
    changed = [rid for rid, (_, meta) in desired.items() if old.get(rid) != meta["digest"]]
    report.skipped = len(desired) - len(changed)

    for start in range(0, len(changed), batch_size):
        part = changed[start : start + batch_size]
        docs = [desired[rid][0] for rid in part]
        metas = [desired[rid][1] for rid in part]
        store.add(table.name, part, docs, metas, embedder.embed(docs))
        report.embedded += len(part)

    # 陈旧行清理：库里存在但新 CSV 不再产出（被删的行 / 迁移前的行号 id / 改过主键的旧行）
    keep = set(desired)
    stale = [doc_id for doc_id in store.list_ids(table.name) if doc_id not in keep]
    if stale:
        store.delete(table.name, stale)
    report.deleted = stale

    logger.info("增量同步 %s：%s", table.name, report.to_line())
    return report


def sync_all(
    store: VectorStore,
    embedder: Embedder,
    tables: tuple[SpecTable, ...] = ALL_TABLES,
    data_dir: Path | None = None,
    batch_size: int = 16,
) -> dict[str, SyncReport]:
    """增量同步多张表，返回 {表名: SyncReport}。"""
    return {t.name: sync_table(store, embedder, t, data_dir, batch_size) for t in tables}


def collect_doc_files(doc_dir: Path) -> list[Path]:
    """递归收集目录下的可入库文档文件（按真实扩展名，跳过目录）。"""
    if not doc_dir.exists():
        logger.warning("文档目录不存在：%s", doc_dir)
        return []
    files = [
        p for p in doc_dir.rglob("*")
        if p.is_file() and p.suffix.lower().lstrip(".") in DOC_EXTS
    ]
    return sorted(files)


def ingest_documents(
    store: VectorStore,
    embedder: Embedder,
    files: list[Path],
    ocr=None,
    vision_embed=None,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    ocr_min_chars: int = 20,
    image_dir: Path | None = None,
) -> dict[str, int]:
    """批量入库文档文件（每文件一 collection，按文件 hash 幂等）。

    返回 {文件路径字符串: chunk 数}；已入库（同 hash collection 存在）的文件跳过。
    图片 collection 的迁移升级：旧版本入库的图片缺「图片URL」（无展示链接）→
    删除重建，保证存量图片也能在聊天回答中展示。
    """
    summary: dict[str, int] = {}
    for path in files:
        try:
            file_bytes = path.read_bytes()
            ext = path.suffix.lower().lstrip(".")
            file_hash = hashlib.sha256(file_bytes).hexdigest()[:12]
            prefix = "pdf" if ext == "pdf" else ("img" if ext in IMAGE_EXTS else "doc")
            collection = f"{prefix}_{file_hash}"
            if collection in store.list_collections():
                # 旧图片数据检测：source_type=image 且缺「图片URL」→ 视为旧格式，重建
                stale = False
                if collection.startswith("img_"):
                    hits = store.get_all(collection)
                    if hits and hits[0]["metadata"].get("source_type") == "image" \
                            and not hits[0]["metadata"].get("图片URL"):
                        stale = True
                if not stale:
                    logger.info("已入库，跳过：%s", path)
                    continue
                store.delete_collection(collection)
                logger.info("图片旧数据缺「图片URL」，重建：%s", path)
            status, count, err = ingest_document(
                file_bytes,
                path.name,
                None,  # CLI 无 DB 记录，doc_id 置空
                store,
                embedder,
                ocr=ocr,
                vision_embed=vision_embed,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                ocr_min_chars=ocr_min_chars,
                collection=collection,
                source_path=str(path),
                image_dir=image_dir,
            )
            summary[str(path)] = count
            if status != "ready":
                logger.warning("入库失败 %s：%s", path, err)
        except Exception:
            logger.exception("入库异常：%s", path)
    return summary


def main() -> None:
    """真实入库：百炼 embedding → PersistentClient(data/chroma)。"""
    parser = argparse.ArgumentParser(description="badminton-rag 入库流水线")
    parser.add_argument(
        "--dir", type=Path, default=None,
        help="批量入库目录（递归扫描 pdf/图片/txt/md/csv；默认 data/raw_docs）",
    )
    parser.add_argument(
        "--only-docs", action="store_true",
        help="只入库文档目录，不跑 17 张 CSV",
    )
    parser.add_argument(
        "--sync", action="store_true",
        help="CSV 增量同步：按行主键 + 内容 digest 只重嵌变化的行，并删除陈旧行（不加则整表全量 upsert）",
    )
    parser.add_argument(
        "--tables", default=None,
        help="逗号分隔中文表名（如 球拍,羽毛球），默认全部；配合 --sync 与全量模式均可",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    settings = get_settings()
    embedder = build_embedder(settings)
    store = VectorStore(persist_dir=settings.chroma_dir)

    if args.tables:
        names = [s.strip() for s in args.tables.split(",") if s.strip()]
        by_name = {t.name: t for t in ALL_TABLES}
        unknown = [n for n in names if n not in by_name]
        if unknown:
            parser.error(f"未知表名 {unknown}；可选：{'、'.join(by_name)}")
        selected_tables = tuple(by_name[n] for n in names)
    else:
        selected_tables = ALL_TABLES

    if not args.only_docs:
        if args.sync:
            reports = sync_all(
                store, embedder, tables=selected_tables,
                batch_size=settings.ingest_batch_size,
            )
            logger.info(
                "CSV 增量同步完成：重嵌 %d / 跳过 %d / 删除 %d（%s）",
                sum(r.embedded for r in reports.values()),
                sum(r.skipped for r in reports.values()),
                sum(len(r.deleted) for r in reports.values()),
                "；".join(r.to_line() for r in reports.values() if r.embedded or r.deleted),
            )
        else:
            summary = run_ingest(
                store,
                embedder,
                tables=selected_tables,
                batch_size=settings.ingest_batch_size,
            )
            logger.info("CSV 入库完成：%s", summary)

    doc_dir = args.dir or settings.raw_docs_dir
    if doc_dir.exists():
        files = collect_doc_files(doc_dir)
        if files:
            ocr = build_ocr_engine(settings)
            vision_embed = build_vision_embedder(settings)
            doc_summary = ingest_documents(
                store,
                embedder,
                files,
                ocr=ocr,
                vision_embed=vision_embed,
                chunk_size=settings.doc_chunk_size,
                chunk_overlap=settings.doc_chunk_overlap,
                ocr_min_chars=settings.ocr_min_chars,
                image_dir=settings.doc_images_dir,
            )
            logger.info("文档入库完成：%d 个文件 %s", len(doc_summary), doc_summary)
        else:
            logger.info("文档目录无可入库文件：%s", doc_dir)
    else:
        logger.info("文档目录不存在，跳过文档入库：%s", doc_dir)


if __name__ == "__main__":
    main()
