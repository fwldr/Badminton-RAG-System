"""POST /chat：Agentic RAG 对话接口（路由 Agent + 工具调用 + 多轮记忆 + 回答校验）。

Phase 4 新增：
- Langfuse tracer：每请求一条 trace（route→工具→generate→verify），span 含耗时/token；
  默认 NullTracer（内存记账，不触网），开关见 .env 的 LANGFUSE_*；
- FAQ 缓存：无历史会话重复问题直接命中（cached=true，跳过 agent 调用）；
- 成本统计：每请求 token 按 route 聚合，`GET /chat/stats`（管理鉴权）查看报表。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.agent.graph import BadmintonAgent
from app.agent.memory import MemoryStore, compress_history
from app.api.deps import admin_rate_limit, get_current_user_optional, rate_limit, require_admin_access
from app.api.errors import ok
from app.core.config import get_settings
from app.db.repos import (
    ConversationRepo,
    MessageRepo,
    PromptTemplateRepo,
    RagDictRepo,
    RagSettingsRepo,
)
from app.ingest.embedder import build_embedder
from app.ingest.store import VectorStore
from app.ingest.vision_embed import build_vision_embedder
from app.observability.faq_cache import FaqCache
from app.observability.tracer import build_tracer
from app.observability.usage import TokenCounter
from app.rag.guard import blacklist_reply, contains_blacklist
from app.rag.llm import FALLBACK_ANSWER, LLMClient
from app.rag.retriever import Retriever
from app.wiki.navigator import build_navigator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    """POST /chat 请求体。"""

    session_id: str = Field(min_length=1, max_length=100, description="会话标识（多轮记忆用）")
    question: str = Field(min_length=1, max_length=500, description="用户问题")
    scope: Literal["equipment", "rules", "technique", "document"] | None = Field(
        default=None, description="范围限定（可选）：强制在该检索范围内回答"
    )
    mode: Literal["classic", "wiki"] | None = Field(
        default=None, description="检索模式（可选）：null 时取服务端 WIKI_MODE_ENABLED 全局开关"
    )


class ChatResponse(BaseModel):
    """POST /chat 响应体。"""

    answer: str
    sources: list[dict] = Field(default_factory=list, description="引用来源 [{table, brand, model}]")
    images: list[dict] = Field(default_factory=list, description="图片引用 [{url, title}]（图片文档展示用）")
    clarification: str | None = Field(default=None, description="需要澄清时的提示")
    trace: list[dict] = Field(default_factory=list, description="Agent 节点执行记录")
    trace_id: str = Field(default="", description="本次请求追踪 ID（Langfuse trace 对应）")
    cached: bool = Field(default=False, description="是否命中常见问题缓存")
    mode: str = Field(default="classic", description="实际生效的检索模式 classic | wiki")
    wiki_trace: dict | None = Field(
        default=None, description="wiki 模式 orient 轨迹（选了哪些分类/条目、降级原因）"
    )
    langfuse_url: str | None = Field(default=None, description="Langfuse trace 页面 URL（未启用时为 null）")


# 进程级单例（Chroma PersistentClient 每进程一个实例）
_agent: BadmintonAgent | None = None
_memory = MemoryStore()
_token_counter = TokenCounter()
_faq_cache: FaqCache | None = None
_tracer = None  # 进程级 tracer（供关闭时 flush）
_guard_cache: dict | None = None  # 敏感词守卫配置缓存（reload_agent 时失效）


def _get_guard_config() -> tuple[bool, list[str]]:
    """读运行时敏感词配置（blacklist_enabled + 黑名单词表）；异常回退为关闭。"""
    global _guard_cache
    if _guard_cache is None:
        enabled, words = False, []
        try:
            vals = RagSettingsRepo.get_all()
            enabled = str(vals.get("blacklist_enabled", "")).strip().lower() in (
                "1", "true", "on", "yes"
            )
            words = RagDictRepo.blacklist_words()
        except Exception:
            logger.exception("读取敏感词配置失败，守卫按关闭处理")
        _guard_cache = {"enabled": enabled, "words": words}
    return _guard_cache["enabled"], _guard_cache["words"]


def _get_faq_cache() -> FaqCache:
    global _faq_cache
    if _faq_cache is None:
        s = get_settings()
        _faq_cache = FaqCache(capacity=s.faq_cache_capacity, ttl=s.faq_cache_ttl)
    return _faq_cache


def _build_agent() -> BadmintonAgent:
    global _tracer
    settings = get_settings()
    store = VectorStore(persist_dir=settings.chroma_dir)
    embedder = build_embedder(settings)
    # RAG 调优中心运行时参数（缺表/异常回退 config 默认，绝不阻断问答链路）
    vec_top_k = settings.ask_vector_top_k
    filter_top_k = settings.ask_filter_top_k
    synonyms: list[tuple[str, ...]] | None = None
    generate_system: str | None = None
    try:
        db_vals = RagSettingsRepo.get_all()
        if str(db_vals.get("vector_top_k") or "").strip().isdigit():
            vec_top_k = int(db_vals["vector_top_k"])
        if str(db_vals.get("filter_top_k") or "").strip().isdigit():
            filter_top_k = int(db_vals["filter_top_k"])
        groups = RagDictRepo.synonyms_groups()
        if groups:
            synonyms = groups
        tpl = PromptTemplateRepo.get_active()
        if tpl and (tpl.get("system_prompt") or "").strip():
            generate_system = tpl["system_prompt"]
    except Exception:
        logger.exception("读取 RAG 调优配置失败，回退默认")
    retriever = Retriever(store, embedder, use_bm25=True, extra_synonyms=synonyms)
    llm = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "",
        model=settings.llm_model,
    )
    _tracer = build_tracer(settings)
    vision_embed = build_vision_embedder(settings)
    wiki = build_navigator(
        store, retriever, llm, settings.wiki_dir, settings.processed_data_dir,
        embedder=embedder, max_steps=settings.wiki_max_steps,
    )
    return BadmintonAgent(
        retriever,
        llm,
        vector_top_k=vec_top_k,
        filter_top_k=filter_top_k,
        memory=_memory,
        tracer=_tracer,
        vision_embed=vision_embed,
        generate_system=generate_system,
        wiki=wiki,
        default_mode="wiki" if settings.wiki_mode_enabled else "classic",
    )


def reload_agent() -> None:
    """RAG 调优中心（参数/同义词/模板/敏感词）变更后调用：清单例与守卫缓存，下次请求重建。"""
    global _agent, _guard_cache
    _agent = None
    _guard_cache = None


def flush_tracer() -> None:
    """应用关闭时调用：把 Langfuse 队列中的 trace 刷出（避免进程退出丢 trace）。"""
    if _tracer is not None:
        try:
            _tracer.flush()
        except Exception:
            logger.exception("Langfuse flush 失败（旁路，忽略）")


def get_agent() -> BadmintonAgent:
    """提供 BadmintonAgent 依赖；测试可通过 dependency_overrides 替换。"""
    global _agent
    if _agent is None:
        _agent = _build_agent()
    return _agent


@router.post("/chat", summary="Agentic RAG 对话（路由/工具/记忆/校验）")
async def chat(
    req: ChatRequest,
    agent: BadmintonAgent = Depends(get_agent),
    current_user: dict | None = Depends(get_current_user_optional),
    _: None = Depends(rate_limit()),
) -> dict:
    """Agentic 问答：自动路由 → 工具调用 → 生成 → 校验，支持多轮记忆与范围限定。

    登录用户自动落库会话与消息（个人知识工作台数据源）；失败不影响回答。
    """
    trace_id = uuid.uuid4().hex  # 32 位 hex，作为 Langfuse trace id 与响应字段
    # 敏感词守卫（RAG 词典黑名单；blacklist_enabled 默认关闭，开启后命中直接拒绝）
    guard_enabled, guard_words = _get_guard_config()
    if guard_enabled:
        hit = contains_blacklist(req.question, guard_words)
        if hit:
            return ok(blacklist_reply(hit))
    # 多轮记忆：取历史 → 压缩 → 追加当前问题
    history = _memory.get(req.session_id)

    # FAQ 缓存：仅无历史时查/写（多轮下同一句可能指代不同对象，绝不缓存）
    if not history:
        cached = _get_faq_cache().get(req.question)
        if cached is not None:
            return ok({**cached, "trace_id": trace_id, "cached": True, "langfuse_url": None,
                       "images": cached.get("images") or []})

    compressed = compress_history(history, getattr(agent, "_llm", None))
    tracer = getattr(agent, "_tracer", None)
    if tracer is not None:
        tracer.start_trace(
            f"/chat {trace_id}",
            input={"session_id": req.session_id, "question": req.question},
            session_id=req.session_id,
            tags=["chat"],
            trace_id=trace_id,
        )

    result = agent.invoke(
        {
            "question": req.question,
            "session_id": req.session_id,
            "scope": req.scope,
            "mode": req.mode,
            "history": compressed,
            "contexts": [],
            "sources": [],
            "sub_questions": [],
            "retry_count": 0,
            "trace": [],
        }
    )

    route = result.get("route", "")
    if tracer is not None:
        tracer.end_trace(
            output={
                "route": route,
                "answer": (result.get("answer") or "")[:200],
                "retry_count": result.get("retry_count", 0),
                "fallback": (result.get("answer") or "") == FALLBACK_ANSWER,
            },
            tags=["chat", route],
        )
        # 成本统计：本 trace 各节点 token 合计 → 按 route 聚合
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for node_tokens in tracer.token_summary().values():
            for key in usage:
                usage[key] += node_tokens.get(key, 0)
        _token_counter.add(route, usage)

    # 记录本轮对话到记忆
    _memory.append(req.session_id, {"role": "user", "content": req.question})
    _memory.append(req.session_id, {"role": "assistant", "content": result.get("answer", "")})

    payload = {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "images": result.get("images") or [],
        "clarification": result.get("clarification"),
        "trace": result.get("trace", []),
        "trace_id": trace_id,
        "cached": False,
        "mode": result.get("mode") or "classic",
        "wiki_trace": result.get("wiki_trace") or None,
        # Langfuse 启用时返回 trace 页面 URL；未启用（NullTracer）返回空串 → 归一为 None
        "langfuse_url": (tracer.trace_url(trace_id) if tracer is not None else "") or None,
    }
    # 写缓存：无历史 + 已校验 + 无澄清 + 非闲聊
    if not history and result.get("verified") and not result.get("clarification") and route != "chitchat":
        _get_faq_cache().set(
            req.question,
            {
                "answer": payload["answer"],
                "sources": payload["sources"],
                "images": payload["images"],
                "clarification": payload["clarification"],
                "trace": payload["trace"],
                "mode": payload["mode"],
                "wiki_trace": payload["wiki_trace"],
            },
        )

    # 登录用户：会话与消息落库（个人知识工作台；旁路，失败不影响回答）
    if current_user is not None:
        try:
            conv_id = ConversationRepo.upsert(
                current_user["id"], req.session_id, title=req.question[:20]
            )
            MessageRepo.add(conv_id, "user", req.question)
            MessageRepo.add(
                conv_id, "assistant", payload["answer"],
                sources_json=json.dumps(payload["sources"], ensure_ascii=False) if payload["sources"] else None,
                trace_id=trace_id,
                cached=1 if payload["cached"] else 0,
            )
        except Exception:
            logger.exception("会话落库失败（旁路，不影响回答）")

    return ok(payload)


@router.get("/chat/stats", summary="对话成本统计（按 route 聚合 token，需管理员）",
            dependencies=[Depends(require_admin_access)])
async def chat_stats(_: None = Depends(admin_rate_limit())) -> dict:
    """返回按 route 聚合的 token 用量与调用次数报表。"""
    return ok({"rows": _token_counter.report()})
