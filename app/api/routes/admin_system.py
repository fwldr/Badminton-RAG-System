"""管理端「系统运维与设置」：模型/密钥/限流等配置的只读展示（密钥掩码）。

说明：运行时可改密钥/限流参数属 P2 之后（涉及 .env 热生效语义），本阶段只读展示
变化值来源（config），前端可见、可核对；敏感 key 一律掩码。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import admin_rate_limit, require_admin_module
from app.api.errors import ok
from app.core.config import get_settings

router = APIRouter(
    prefix="/admin/system",
    tags=["admin-system"],
    dependencies=[Depends(require_admin_module("system")), Depends(admin_rate_limit())],
)


def _mask(value: str | None) -> str:
    """密钥掩码：仅保留前 4 位（长度不足全掩）。"""
    if not value:
        return ""
    return value[:4] + "…" if len(value) > 4 else "…"


@router.get("", summary="系统配置（只读；密钥掩码展示）")
async def system_config() -> dict:
    s = get_settings()
    return ok({
        "models": {
            "embedding": {"provider": "百炼 DashScope", "base_url": s.llm_base_url, "model": s.embedding_model},
            "llm": {
                "provider": "百炼 DashScope",
                "base_url": s.llm_base_url,
                "model": s.llm_model,
                "api_key": _mask(s.llm_api_key),
            },
            "rerank": {
                "enabled": s.ask_use_rerank,
                "base_url": s.rerank_base_url,
                "model": s.rerank_model,
                "api_key": _mask(s.rerank_api_key),
            },
            "vision": {
                "enabled": s.vision_embed_enabled,
                "base_url": s.vision_base_url,
                "model": s.vision_embed_model,
                "api_key": _mask(s.vision_api_key or s.rerank_api_key),
            },
        },
        "database": {
            "backend": s.db_backend,
            "host": s.mysql_host if s.db_backend == "mysql" else "本地文件",
            "name": s.mysql_db if s.db_backend == "mysql" else str(s.db_path),
        },
        "vector_store": {"dir": str(s.chroma_dir)},
        "limits": {
            "ask_capacity": s.rate_limit_ask_capacity,
            "ask_refill_per_sec": s.rate_limit_ask_refill,
            "admin_capacity": s.rate_limit_admin_capacity,
            "admin_refill_per_sec": s.rate_limit_admin_refill,
            "upload_max_mb": s.upload_max_size // (1024 * 1024),
        },
        "ingest": {
            "doc_chunk_size": s.doc_chunk_size,
            "doc_chunk_overlap": s.doc_chunk_overlap,
            "ocr_engine": s.ocr_engine,
            "ocr_min_chars": s.ocr_min_chars,
        },
        "auth": {
            "token_ttl_hours": round(s.auth_token_ttl / 3600, 1),
            "bootstrap_admin": s.bootstrap_admin_username or "（未配置）",
        },
    })
