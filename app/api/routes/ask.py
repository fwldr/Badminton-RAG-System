"""POST /ask：羽毛球装备 RAG 问答接口（统一响应 + 限流 + 审计埋点）。"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, Request

from app.api.deps import rate_limit
from app.api.errors import ok
from app.core.config import get_settings
from app.db.repos import AuditRepo
from app.ingest.embedder import build_embedder
from app.ingest.store import VectorStore
from app.models.schema import AskRequest
from app.rag.llm import LLMClient
from app.rag.reranker import build_reranker
from app.rag.retriever import Retriever
from app.rag.service import AskService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ask"])

# 进程级单例（Chroma 持久化客户端每个进程只应持有一个实例）
_service: AskService | None = None


def _build_service() -> AskService:
    settings = get_settings()
    store = VectorStore(persist_dir=settings.chroma_dir)
    embedder = build_embedder(settings)
    # 生产链路默认开启 BM25 混合检索（向量对短属性词如「红色」召回弱）
    retriever = Retriever(store, embedder, use_bm25=True)
    llm = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "",
        model=settings.llm_model,
    )
    # 精排器按 ask_use_rerank 开关构建（默认 False → None，不接入）
    return AskService(
        retriever,
        llm,
        vector_top_k=settings.ask_vector_top_k,
        filter_top_k=settings.ask_filter_top_k,
        reranker=build_reranker(settings),
    )


def get_ask_service() -> AskService:
    """提供 AskService 依赖；测试可通过 dependency_overrides 替换。"""
    global _service
    if _service is None:
        _service = _build_service()
    return _service


@router.post("/ask", summary="羽毛球装备问答")
async def ask(
    req: AskRequest,
    request: Request,
    service: AskService = Depends(get_ask_service),
    _: None = Depends(rate_limit()),
) -> dict:
    """RAG 问答：检索 → 过滤 → 生成中文回答，末尾附来源；成功后落审计日志。"""
    started = time.monotonic()
    result = service.ask(req.question)
    latency_ms = int((time.monotonic() - started) * 1000)

    # 审计埋点：谁在何时问了什么、引用来源留痕（同请求写；生产可换队列异步落库）
    try:
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
        sources = [
            {"table": s.table, "brand": s.brand, "model": s.model} for s in result.sources
        ]
        AuditRepo.insert(
            client_ip=client_ip,
            question=req.question,
            answer=result.answer,
            sources_json=json.dumps(sources, ensure_ascii=False) if sources else None,
            latency_ms=latency_ms,
        )
    except Exception:
        logger.exception("审计日志写入失败（不影响问答响应）")

    return ok({"answer": result.answer, "sources": result.sources})
