"""Wiki 索引层：条目页 → 两个 Chroma collection。

- `wiki_page`：条目级文档（标题 + 别名 + facets + 章节标题），给 orient 做「哪个条目」的粗定位；
- `wiki_section`：章节级全文（`《条目》§章节` + 正文），是在线**读知识单元**的对象，
  也是 BM25/精排后续接入的目标。

向量只覆盖**内容变了**的页：按 `digest` 元数据比对，未变不重嵌（几千段全量重嵌按量计费，成本高）。
`index_state.json` 记录源指纹，用于判定「wiki 已编译但索引还是旧的」。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.ingest.embedder import Embedder
from app.ingest.store import VectorStore
from app.wiki.schema import Entry
from app.wiki.manifest import MANIFEST_NAME, Manifest

logger = logging.getLogger(__name__)

WIKI_PAGE_COLLECTION = "wiki_page"
WIKI_SECTION_COLLECTION = "wiki_section"
INDEX_STATE_NAME = "index_state.json"
ID_SEP = "#"


def section_doc_id(eid: str, section_key: str) -> str:
    """章节文档 id：`entry_id#section_key`（条目 id 不含冒号，故 record-id 拆分逻辑不受影响）。"""
    return f"{eid}{ID_SEP}{section_key}"


def split_section_doc_id(doc_id: str) -> tuple[str, str]:
    entry, _, key = doc_id.partition(ID_SEP)
    return entry, key


def _doc_digest(document: str, meta: dict) -> str:
    """重嵌判据 = **将被写入的内容**（文档文本 + 元数据）。

    不用整份条目 md 的 digest：frontmatter（条目级指纹等）变动不会改变可检索内容，
    按 md 比对会让一次模板调整重嵌全库。
    """
    payload = document + "\x1f" + json.dumps(meta, ensure_ascii=False, sort_keys=True)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


@dataclass
class IndexReport:
    """一次索引的结果（写盘与重嵌计数，CLI 与测试断言对象）。"""

    pages_written: list[str] = field(default_factory=list)
    sections_written: list[str] = field(default_factory=list)
    skipped: int = 0
    deleted: list[str] = field(default_factory=list)

    @property
    def embedded(self) -> int:
        return len(self.pages_written) + len(self.sections_written)


def _meta_for(entry: Entry, section_key: str = "", section_title: str = "") -> dict:
    """Chroma metadata 只允许标量 → facets/records 一律 JSON 字符串化。"""
    return {
        "entry_id": entry.id,
        "entry_title": entry.title,
        "entry_type": entry.type,
        "category": entry.category,
        "section_key": section_key,
        "section_title": section_title,
        "facets": json.dumps(entry.facets, ensure_ascii=False, sort_keys=True),
        "records": json.dumps(
            [a.id for s in entry.sections if s.key == section_key or not section_key for a in s.sources],
            ensure_ascii=False,
        ),
        "wiki": 1,
    }


def _existing_digests(store: VectorStore, collection: str, ids: list[str]) -> dict[str, str]:
    """已入库文档的 id → digest（分批查，避免一次传数千 id）。"""
    found: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        for hit in store.get(collection, batch):
            found[hit["id"]] = str((hit.get("metadata") or {}).get("digest", ""))
    return found


def index_wiki(
    store: VectorStore,
    embedder: Embedder,
    entries: list[Entry],
    force: bool = False,
    batch_size: int = 32,
) -> IndexReport:
    """把条目写入两个 collection（幂等：digest 未变不重嵌，消失的 id 会删除）。"""
    report = IndexReport()
    page_ids = [e.id for e in entries]
    section_ids = [section_doc_id(e.id, s.key) for e in entries for s in e.sections]

    old_pages = {} if force else _existing_digests(store, WIKI_PAGE_COLLECTION, page_ids)
    old_sections = {} if force else _existing_digests(store, WIKI_SECTION_COLLECTION, section_ids)

    pending: dict[str, tuple[list[str], list[str], list[dict]]] = {
        WIKI_PAGE_COLLECTION: ([], [], []),
        WIKI_SECTION_COLLECTION: ([], [], []),
    }

    old: dict[str, dict[str, str]] = {
        WIKI_PAGE_COLLECTION: old_pages if not force else {},
        WIKI_SECTION_COLLECTION: old_sections if not force else {},
    }

    def offer(collection: str, doc_id: str, text: str, meta: dict) -> None:
        digest = _doc_digest(text, meta)
        if not force and old[collection].get(doc_id) == digest:
            report.skipped += 1
            return
        meta["digest"] = digest
        ids, docs, metas = pending[collection]
        ids.append(doc_id)
        docs.append(text)
        metas.append(meta)

    for entry in entries:
        offer(WIKI_PAGE_COLLECTION, entry.id, entry.page_document(), _meta_for(entry))
        for section in entry.sections:
            offer(
                WIKI_SECTION_COLLECTION,
                section_doc_id(entry.id, section.key),
                f"《{entry.title}》§{section.title}\n{section.body}",
                _meta_for(entry, section_key=section.key, section_title=section.title),
            )

    for collection, (ids, docs, metas) in pending.items():
        for start in range(0, len(ids), batch_size):
            stop = start + batch_size
            store.add(
                collection,
                ids[start:stop],
                docs[start:stop],
                metas[start:stop],
                embedder.embed(docs[start:stop]),
            )
        target = report.pages_written if collection == WIKI_PAGE_COLLECTION else report.sections_written
        target.extend(ids)

    report.deleted = _delete_stale(store, WIKI_PAGE_COLLECTION, page_ids)
    report.deleted += _delete_stale(store, WIKI_SECTION_COLLECTION, section_ids)
    logger.info(
        "Wiki 索引：page 写 %d / section 写 %d / 跳过 %d / 删除 %d",
        len(report.pages_written), len(report.sections_written), report.skipped, len(report.deleted),
    )
    return report


def _delete_stale(store: VectorStore, collection: str, keep_ids: list[str]) -> list[str]:
    """删掉「库里还在、本次编译已不存在」的文档 id。"""
    keep = set(keep_ids)
    ids = [doc_id for doc_id in store.list_ids(collection) if doc_id not in keep]
    if ids:
        store.delete(collection, ids)
    return ids


def save_index_state(wiki_dir: Path, source_fingerprint: str, report: IndexReport) -> None:
    (wiki_dir / INDEX_STATE_NAME).write_text(
        json.dumps(
            {
                "source_fingerprint": source_fingerprint,
                "embedded": report.embedded,
                "skipped": report.skipped,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def index_state(wiki_dir: Path) -> dict:
    path = wiki_dir / INDEX_STATE_NAME
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def index_is_current(wiki_dir: Path, store: VectorStore) -> bool:
    """索引是否对应当前编译产物（源指纹一致且两个 collection 非空）。"""
    state = index_state(wiki_dir)
    manifest_path = wiki_dir / MANIFEST_NAME
    if not state or not manifest_path.exists():
        return False
    if state.get("source_fingerprint") != Manifest.load(wiki_dir).source_fingerprint:
        return False
    return (
        store.count(WIKI_PAGE_COLLECTION) > 0 and store.count(WIKI_SECTION_COLLECTION) > 0
    )
