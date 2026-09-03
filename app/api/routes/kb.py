"""KB 路由：/kb/overview（统计，公开）+ /kb/catalog（知识库目录，公开）。

catalog 按业务语义把知识库内容分组（装备规格/规则/技术/伤病康复/球星/文档资料等），
供「发现」页分类浏览并一键唤起问答。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends

from app.api.deps import rate_limit
from app.api.errors import ok
from app.core.config import get_settings
from app.db.repos import DocRepo
from app.ingest.serializer import SPEC_TABLES
from app.ingest.store import VectorStore, display_name

logger = logging.getLogger(__name__)

router = APIRouter(tags=["kb"])

# 知识文件 → 分类的映射规则（按文件名关键词匹配；顺序即分类顺序）
_KNOWLEDGE_CATEGORIES: list[tuple[str, str, tuple[str, ...]]] = [
    ("规则库", "📜", ("规则", "裁判", "bwf", "赛事", "比赛")),
    ("技术库", "🏸", ("技术", "手法", "技巧", "步法", "发球", "接杀", "网前", "高远", "扣杀", "吊球", "杀球")),
    ("伤病康复库", "💪", ("伤病", "康复", "受伤", "预防", "网球肘", "腕", "膝", "肩")),
    ("球星专辑", "🌟", ("球星", "林丹", "李宗伟", "陶菲克", "盖德", "谌龙", "安赛龙")),
]

_CATEGORY_ICONS = {"装备规格": "🎾", "文档资料": "📄", "其他知识": "📚"}

# 进程级单例（Chroma PersistentClient 每进程一个实例）
_store: VectorStore | None = None


def get_kb_store() -> VectorStore:
    """提供 VectorStore 依赖（只读统计）；测试可 dependency_overrides 替换为内存版。"""
    global _store
    if _store is None:
        settings = get_settings()
        _store = VectorStore(persist_dir=settings.chroma_dir)
    return _store


def scan_processed(processed_dir: Path) -> dict:
    """扫描 data/processed：规格表 CSV + knowledge/ 知识表 CSV（不依赖 chroma 在线）。"""
    spec_tables: list[str] = []
    knowledge_files: list[str] = []
    try:
        for spec in SPEC_TABLES:
            if (processed_dir / spec.csv_file).exists():
                spec_tables.append(spec.name)
        kdir = processed_dir / "knowledge"
        if kdir.is_dir():
            knowledge_files = sorted(p.stem for p in kdir.glob("*.csv"))
    except OSError:
        logger.warning("扫描 processed 目录失败（降级为空清单）", exc_info=True)
    return {"spec_tables": spec_tables, "knowledge_files": knowledge_files}


@router.get("/kb/overview", summary="知识库统计（表 / chunk 数 / 文件数，公开只读）")
async def kb_overview(
    store: VectorStore = Depends(get_kb_store),
    _: None = Depends(rate_limit()),
) -> dict:
    """返回各 collection 的 chunk 数与知识库文件清单。"""
    tables: list[dict] = []
    total_chunks = 0
    try:
        names = store.list_collections()
        for name in sorted(names):
            try:
                n = store.count(name)
            except Exception:
                n = 0
            total_chunks += n
            # 对外展示中文表名（英文 collection 名内部使用）
            tables.append({"table": display_name(name), "chunks": n})
    except Exception:
        logger.warning("读取 chroma collection 统计失败（降级为空表清单）", exc_info=True)

    files = scan_processed(get_settings().processed_data_dir)
    return ok(
        {
            "tables": tables,
            "spec_tables": files["spec_tables"],
            "knowledge_files": files["knowledge_files"],
            "total_chunks": total_chunks,
        }
    )


def _classify_knowledge(name: str) -> tuple[str, str]:
    """按文件名关键词归类知识文件 → (分类名, 图标)。"""
    lower = name.lower()
    for cat, icon, keywords in _KNOWLEDGE_CATEGORIES:
        if any(k in lower for k in keywords):
            return cat, icon
    return "其他知识", _CATEGORY_ICONS["其他知识"]


def _count(store: VectorStore, name: str) -> int:
    try:
        return store.count(name)
    except Exception:
        return 0


@router.get("/kb/catalog", summary="知识库目录（分类浏览，公开）")
async def kb_catalog(
    store: VectorStore = Depends(get_kb_store),
    _: None = Depends(rate_limit()),
) -> dict:
    """返回分类目录：装备规格 / 规则库 / 技术库 / 伤病康复库 / 球星专辑 / 文档资料 / 其他。"""
    settings = get_settings()
    categories: dict[str, dict] = {}

    def _cat(name: str, icon: str) -> dict:
        return categories.setdefault(name, {"name": name, "icon": icon, "items": []})

    # 装备规格：5 张规格表
    spec_cat = _cat("装备规格", _CATEGORY_ICONS["装备规格"])
    for spec in SPEC_TABLES:
        if (settings.processed_data_dir / spec.csv_file).exists():
            spec_cat["items"].append({
                "name": spec.name, "kind": "spec", "category": "装备规格",
                "source": spec.csv_file, "chunks": _count(store, spec.name),
            })

    # 知识文件：按文件名关键词归类
    kdir = settings.processed_data_dir / "knowledge"
    try:
        for p in sorted(kdir.glob("*.csv")) if kdir.is_dir() else []:
            name = p.stem
            icon = ""
            cat, icon = _classify_knowledge(name)
            _cat(cat, icon)["items"].append({
                "name": name, "kind": "knowledge", "category": cat,
                "source": p.name, "chunks": _count(store, name),
            })
    except OSError:
        logger.warning("扫描 knowledge 目录失败（catalog 降级）")

    # 已入库文档（管理端上传/CLI）
    try:
        doc_items = _cat("文档资料", _CATEGORY_ICONS["文档资料"])
        for doc in DocRepo.list_all():
            if doc["status"] == "ready":
                doc_items["items"].append({
                    "name": doc["filename"], "kind": "doc", "category": "文档资料",
                    "source": f"doc_{doc['id']}", "chunks": doc["chunk_count"],
                })
    except Exception:
        logger.warning("读取文档目录失败（catalog 降级）")

    # 空分类不返回；分类顺序固定
    names = [c["name"] for c in categories.values()]
    ordered = [n for n in ["装备规格", "规则库", "技术库", "伤病康复库", "球星专辑", "文档资料", "其他知识"] if n in names]
    return ok({"categories": [categories[n] for n in ordered]})
