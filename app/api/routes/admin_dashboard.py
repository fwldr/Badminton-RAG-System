"""管理端 Dashboard：知识库总览指标卡 + 组件健康探活。

- `GET /admin/dashboard`：文档（按类型）、向量总览、问答消息/今日、用户数、待办（待审纠错/
  失败文档）、反馈（赞/踩）、token 成本报表（复用 /chat stats 的进程计数器）；
- `GET /admin/health`：DB / 向量库 / 百炼（LLM+embedding） / SiliconFlow 探活（超时 3s，best-effort）。

探针经 `get_probes()` 依赖注入，测试可 override 为 stub（不触网）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

import httpx
from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.deps import admin_rate_limit, require_admin_module
from app.api.errors import ok
from app.api.routes.chat import _token_counter
from app.api.routes.kb import get_kb_store
from app.core.config import get_settings
from app.db.database import get_conn
from app.db.repos import (
    ConversationRepo,
    CorrectionRepo,
    DocRepo,
    FeedbackRepo,
    MessageRepo,
    UserRepo,
)
from app.ingest.store import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin-dashboard"],
    dependencies=[Depends(require_admin_module("dashboard")), Depends(admin_rate_limit())],
)

_TIMEOUT = 3.0


def _mask_key(key: str | None) -> str:
    """密钥掩码：仅保留前 4 位（展示健康配置时避免泄漏完整 key）。"""
    if not key:
        return ""
    return key[:4] + "…" if len(key) > 4 else "…"


# -------------------- 探针（同步逻辑放在 _probe_*；HTTP 探针独立函数便于注入） --------------------


def _probe_db() -> dict:
    row = get_conn().execute("SELECT 1 AS ok").fetchone()
    return {"ok": bool(row)}


def _probe_chroma(store: VectorStore) -> dict:
    names = store.list_collections()
    return {"collections": len(names)}


def _probe_http(url: str, headers: dict | None = None) -> dict:
    """GET 探活（3s 超时；404/200 都视为服务可达，仅网络/网关错误视为异常）。"""
    resp = httpx.get(url, headers=headers, timeout=_TIMEOUT)
    return {"url": url, "status": resp.status_code}


def _probe_llm(settings) -> dict:
    """生成 LLM 与文本 embedding 同账号同端点，探一次 /models 即覆盖两者。"""
    if not settings.llm_api_key:
        return {"configured": False, "detail": "未配置 LLM_API_KEY"}
    return {
        "configured": True,
        "key": _mask_key(settings.llm_api_key),
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        **_probe_http(f"{settings.llm_base_url}/models",
                      headers={"Authorization": f"Bearer {settings.llm_api_key}"}),
    }


def _probe_vision(settings) -> dict:
    if not getattr(settings, "vision_embed_enabled", False):
        return {"enabled": False}
    key = (settings.vision_api_key or settings.rerank_api_key or "").strip()
    if not key:
        return {"enabled": True, "configured": False}
    return {
        "enabled": True,
        "key": _mask_key(key),
        **_probe_http(f"{settings.vision_base_url}/models",
                      headers={"Authorization": f"Bearer {key}"}),
    }


def _build_probes(settings, store: VectorStore) -> dict[str, Callable[[], dict]]:
    """探针注册表（测试可经 get_probes 依赖 override 替换）。"""
    return {
        "数据库": lambda: _probe_db(),
        "向量库": lambda: _probe_chroma(store),
        "百炼": lambda: _probe_llm(settings),
        "SiliconFlow": lambda: _probe_vision(settings),
    }


def get_probes() -> dict[str, Callable[[], dict]]:
    """FastAPI 依赖：构建探针注册表（测试 override 用）。"""
    return _build_probes(get_settings(), get_kb_store())


# -------------------- 端点 --------------------


@router.get("/dashboard", summary="知识库总览（文档/向量/问答/待办/互动）")
async def dashboard(
    store: VectorStore = Depends(get_kb_store),
) -> dict:
    """总览指标卡数据：文档、向量、消息、用户、待办、反馈与 token 成本。"""
    docs_by_type = DocRepo.count_by_type()
    docs_total = sum(docs_by_type.values())
    failed_docs = DocRepo.count_by_status("failed")

    vector_tables: list[dict] = []
    total_chunks = 0
    try:
        for name in sorted(store.list_collections()):
            try:
                n = store.count(name)
            except Exception:
                n = 0
            total_chunks += n
            vector_tables.append({"collection": name, "chunks": n})
    except Exception:
        logger.warning("chunk 统计失败（降级为空）", exc_info=True)

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        users_total = UserRepo.count()
    except Exception:
        users_total = 0

    return ok({
        "documents": {
            "total": docs_total,
            "by_type": docs_by_type,
            "failed": failed_docs,
        },
        "vectors": {
            "total_chunks": total_chunks,
            "collections": len(vector_tables),
            "tables": vector_tables,
        },
        "activity": {
            "users": users_total,
            "conversations": ConversationRepo.count_all(),
            "messages": MessageRepo.count_all(),
            "messages_today": MessageRepo.count_since(today),
        },
        "todo": {
            "pending_corrections": CorrectionRepo.count_by_status("pending"),
            "failed_documents": failed_docs,
        },
        "feedback": {
            "total": FeedbackRepo.count(),
            "dislikes": FeedbackRepo.count_dislikes(),
        },
        "routes": _token_counter.report(),
    })


@router.get("/health", summary="系统健康探活（DB/向量库/百炼/SiliconFlow）")
async def health(
    probes: dict[str, Callable[[], dict]] = Depends(get_probes),
) -> dict:
    """各组件探活结果（best-effort：单个失败不影响其它；无网络时 HTTP 探针标记异常）。"""
    results: list[dict] = []
    for name, probe in probes.items():
        try:
            detail = await run_in_threadpool(probe)
            results.append({"name": name, "status": "ok", "detail": detail})
        except httpx.HTTPError as exc:
            results.append({"name": name, "status": "error", "detail": {"error": str(exc)[:200]}})
        except Exception as exc:  # noqa: BLE001 - 探活必须容忍任何异常
            results.append({"name": name, "status": "error", "detail": {"error": str(exc)[:200]}})
    failed = [r["name"] for r in results if r["status"] != "ok"]
    return ok({"items": results, "degraded": bool(failed), "failed": failed})
