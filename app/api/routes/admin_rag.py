"""管理端「检索与问答调优中心」（RAG Tuning Studio）。

- `POST /admin/rag/debug`：在线测试沙箱（链路回放，见 app/rag/debug.py）；
- `GET/PUT /admin/rag/settings`：运行时检索参数（Top-K / rerank / 敏感词开关）；
- `GET/POST/PUT/DELETE /admin/rag/prompts` + `POST .../activate`：Prompt 模板（预置 3 套）；
- `GET/POST/DELETE /admin/rag/synonyms`、`/admin/rag/blacklist`：同义词 / 敏感词词典。

参数/模板/词典变更后调用 `chat.reload_agent()` 使 agent 单例重建（下次请求生效）。
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.agent.graph import BadmintonAgent
from app.api.deps import admin_rate_limit, require_admin_module
from app.api.errors import ApiError, ErrorCode, ok
from app.api.routes.chat import get_agent, reload_agent
from app.core.config import get_settings
from app.db.repos import PromptTemplateRepo, RagDictRepo, RagSettingsRepo
from app.rag.debug import debug_pipeline, wiki_debug_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/rag",
    tags=["admin-rag"],
    dependencies=[Depends(require_admin_module("rag")), Depends(admin_rate_limit())],
)

_VALID_DICT_TYPES = ("synonym", "blacklist")


class RagDebugRequest(BaseModel):
    """沙箱请求：问题 + 可选候选量、检索模式与是否生成回答。"""

    question: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=8, ge=1, le=20, description="候选块数量（过滤前）")
    mode: Literal["classic", "wiki"] | None = Field(
        default=None,
        description="回放模式：wiki 展示「翻了哪几页」的轨迹；服务端未装配 wiki 时自动回落 classic",
    )
    with_answer: bool = Field(default=True, description="是否调用 LLM 生成回答（省 token 可关）")


class RagSettingsPatch(BaseModel):
    """运行时参数（None=不修改）。"""

    vector_top_k: int | None = Field(default=None, ge=1, le=50)
    filter_top_k: int | None = Field(default=None, ge=1, le=20)
    rerank_enabled: bool | None = None  # 预留（当前 /chat 链路未接精排，仅存储展示）
    blacklist_enabled: bool | None = None  # 敏感词守卫开关（默认关闭）


class PromptIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    system_prompt: str = Field(min_length=1, max_length=8000)
    description: str | None = Field(default=None, max_length=500)


class PromptPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    description: str | None = Field(default=None, max_length=500)


class DictItemIn(BaseModel):
    """词典条目：锚点词 + 同义词组其余词（blacklist 时 values 忽略）。"""

    word: str = Field(min_length=1, max_length=50)
    values: list[str] = Field(default_factory=list, max_length=50)


# -------------------- 在线测试沙箱 --------------------


@router.post("/debug", summary="RAG 调试沙箱（链路回放：路由/扩展/候选/过滤/上下文/回答）")
async def rag_debug(req: RagDebugRequest, agent: BadmintonAgent = Depends(get_agent)) -> dict:
    """回放一次完整检索链路（复用真实 agent 组件；with_answer=false 时不调 LLM）。"""
    try:
        navigator = getattr(agent, "_wiki", None)
        if req.mode == "wiki" and navigator is not None:
            blob = wiki_debug_pipeline(
                navigator,
                req.question,
                agent._llm,
                with_answer=req.with_answer,
                generate_system=getattr(agent, "_generate_system", None),
            )
        else:
            blob = debug_pipeline(
                req.question,
                agent._retriever,
                agent._llm,
                getattr(agent, "_vision_embed", None),
                top_k=req.top_k,
                with_answer=req.with_answer,
                generate_system=getattr(agent, "_generate_system", None),
            )
            blob["mode"] = "classic"
            if req.mode == "wiki":
                blob["wiki_unavailable"] = "服务端未编译/未装配 wiki，已回落 classic 回放"
    except Exception as exc:
        raise ApiError(ErrorCode.INTERNAL, f"沙箱执行失败：{exc}") from exc
    return ok(blob)


# -------------------- 运行时参数 --------------------


@router.get("/settings", summary="运行时检索参数（含默认值合并）")
async def get_rag_settings() -> dict:
    s = get_settings()
    defaults = {
        "vector_top_k": str(s.ask_vector_top_k),
        "filter_top_k": str(s.ask_filter_top_k),
        "rerank_enabled": "false",
        "blacklist_enabled": "false",
    }
    try:
        stored = RagSettingsRepo.get_all()
    except Exception:
        logger.exception("读取运行时参数失败（回退默认）")
        stored = {}
    merged = {
        **defaults,
        **{k: str(v) for k, v in stored.items() if k in defaults},
    }
    return ok({"settings": merged, "defaults": defaults})


@router.put("/settings", summary="更新运行时检索参数（保存后重建 agent）")
async def put_rag_settings(body: RagSettingsPatch) -> dict:
    values: dict[str, str] = {}
    if body.vector_top_k is not None:
        values["vector_top_k"] = str(body.vector_top_k)
    if body.filter_top_k is not None:
        values["filter_top_k"] = str(body.filter_top_k)
    if body.rerank_enabled is not None:
        values["rerank_enabled"] = "true" if body.rerank_enabled else "false"
    if body.blacklist_enabled is not None:
        values["blacklist_enabled"] = "true" if body.blacklist_enabled else "false"
    if not values:
        return ok({"updated": {}})
    try:
        RagSettingsRepo.set_many(values)
    except Exception as exc:
        raise ApiError(ErrorCode.INTERNAL, f"运行时参数保存失败：{exc}") from exc
    reload_agent()  # 参数变更 → 下次请求按新参数重建 agent
    return ok({"updated": values})


# -------------------- Prompt 模板 --------------------


@router.get("/prompts", summary="Prompt 模板列表")
async def list_prompts() -> dict:
    try:
        templates = PromptTemplateRepo.list_all()
    except Exception as exc:
        raise ApiError(ErrorCode.INTERNAL, f"读取模板失败：{exc}") from exc
    return ok({"templates": templates})


@router.post("/prompts", summary="新建 Prompt 模板")
async def create_prompt(body: PromptIn) -> dict:
    try:
        tpl_id = PromptTemplateRepo.create(body.name, body.system_prompt, body.description)
    except Exception as exc:
        raise ApiError(ErrorCode.INTERNAL, f"创建模板失败：{exc}") from exc
    return ok({"id": tpl_id})


@router.put("/prompts/{tpl_id}", summary="更新 Prompt 模板")
async def update_prompt(tpl_id: int, body: PromptPatch) -> dict:
    if PromptTemplateRepo.get(tpl_id) is None:
        raise ApiError(ErrorCode.NOT_FOUND, "模板不存在")
    PromptTemplateRepo.update(tpl_id, body.name, body.system_prompt, body.description)
    # 正在激活的模板被修改 → 也让 agent 重建（新内容生效）
    if (PromptTemplateRepo.get(tpl_id) or {}).get("is_active"):
        reload_agent()
    return ok({"id": tpl_id})


@router.delete("/prompts/{tpl_id}", summary="删除 Prompt 模板")
async def delete_prompt(tpl_id: int) -> dict:
    if PromptTemplateRepo.get(tpl_id) is None:
        raise ApiError(ErrorCode.NOT_FOUND, "模板不存在")
    PromptTemplateRepo.delete(tpl_id)
    reload_agent()
    return ok({"id": tpl_id})


@router.post("/prompts/{tpl_id}/activate", summary="激活 Prompt 模板（唯一 active）")
async def activate_prompt(tpl_id: int) -> dict:
    if not PromptTemplateRepo.set_active(tpl_id):
        raise ApiError(ErrorCode.NOT_FOUND, "模板不存在")
    reload_agent()
    return ok({"id": tpl_id, "active": True})


# -------------------- 同义词 / 敏感词词典 --------------------


@router.get("/synonyms", summary="同义词词典（锚点词 + 组内其余词）")
async def list_synonyms() -> dict:
    return ok({"items": RagDictRepo.list_by_type("synonym")})


@router.post("/synonyms", summary="新增同义词组")
async def add_synonym(item: DictItemIn) -> dict:
    return _add_dict_item("synonym", item)


@router.delete("/synonyms/{entry_id}", summary="删除同义词组")
async def delete_synonym(entry_id: int) -> dict:
    return _delete_dict_item(entry_id)


@router.get("/blacklist", summary="敏感词列表")
async def list_blacklist() -> dict:
    return ok({"items": RagDictRepo.list_by_type("blacklist")})


@router.post("/blacklist", summary="新增敏感词")
async def add_blacklist(item: DictItemIn) -> dict:
    return _add_dict_item("blacklist", item)


@router.delete("/blacklist/{entry_id}", summary="删除敏感词")
async def delete_blacklist(entry_id: int) -> dict:
    return _delete_dict_item(entry_id)


def _add_dict_item(type_: str, item: DictItemIn) -> dict:
    if type_ not in _VALID_DICT_TYPES:
        raise ApiError(ErrorCode.VALIDATION, "词典类型非法")
    values = [str(v).strip() for v in item.values if str(v).strip()]
    values = [v for v in values if v != item.word.strip()]  # 锚点词不入组内词
    try:
        entry_id = RagDictRepo.add(type_, item.word.strip(), values)
    except Exception as exc:  # UNIQUE(type, word) 冲突
        raise ApiError(ErrorCode.VALIDATION, f"词「{item.word}」已存在于词典") from exc
    reload_agent()
    return ok({"id": entry_id})


def _delete_dict_item(entry_id: int) -> dict:
    if not RagDictRepo.delete(entry_id):
        raise ApiError(ErrorCode.NOT_FOUND, "词典条目不存在")
    reload_agent()
    return ok({"id": entry_id})
