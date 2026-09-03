"""管理端「内容审核与反馈处理」：纠错工单池 + 低质量回答聚合。

- `GET /admin/corrections`：纠错工单（状态筛选，JOIN 提交者）；
- `PATCH /admin/corrections/{id}`：采纳/驳回/转讨论（+ 管理员回复；采纳给提交者发通知）；
- `GET /admin/qc/bad`：同一问题多次点踩的聚合列表（低质量回答干预提示）。
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import admin_rate_limit, require_admin_module
from app.api.errors import ApiError, ErrorCode, ok
from app.core.config import get_settings
from app.db.repos import CorrectionRepo, FeedbackRepo, NotificationRepo, UserRepo
from app.security import wx_sec

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin-review"],
    dependencies=[Depends(require_admin_module("review")), Depends(admin_rate_limit())],
)

_CORRECTION_STATUSES = ("pending", "accepted", "rejected", "discussion")


class CorrectionPatch(BaseModel):
    """审核动作：status 变更 + 可选管理员回复（驳回/转讨论时建议填写理由）。"""

    status: Literal["pending", "accepted", "rejected", "discussion"]
    admin_reply: str | None = Field(default=None, max_length=1000)


@router.get("/corrections", summary="纠错工单列表（状态筛选 + 分页）")
async def list_corrections(
    status: str | None = Query(default=None, description="pending/accepted/rejected/discussion"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    if status is not None and status not in _CORRECTION_STATUSES:
        raise ApiError(ErrorCode.VALIDATION, f"status 仅支持 {_CORRECTION_STATUSES}")
    try:
        items = CorrectionRepo.list_all(status=status, limit=limit, offset=offset)
        total = (
            CorrectionRepo.count_by_status(status)
            if status else
            sum(CorrectionRepo.count_by_status(s) for s in _CORRECTION_STATUSES)
        )
    except Exception as exc:
        raise ApiError(ErrorCode.INTERNAL, f"读取纠错工单失败：{exc}") from exc
    return ok({"total": total, "items": items, "status": status})


@router.patch("/corrections/{corr_id}", summary="审核纠错工单（采纳/驳回/转讨论）")
async def patch_correction(corr_id: int, body: CorrectionPatch) -> dict:
    """审核动作：采纳（accepted）→ 给提交者发「纠错已被采纳」通知，闭环到消息中心；
    若提交者为微信小程序用户且已配置订阅模板，同步推送微信订阅消息（旁路）。"""
    corr = CorrectionRepo.get_any(corr_id)
    if corr is None:
        raise ApiError(ErrorCode.NOT_FOUND, "纠错工单不存在")
    CorrectionRepo.update(corr_id, body.status, body.admin_reply)
    # 采纳 → 通知提交者（旁路：通知写失败不影响审核结果）
    if body.status == "accepted":
        try:
            NotificationRepo.create(
                corr["user_id"], "correction",
                "您的纠错已被采纳",
                f"您提交的纠错已采纳：{body.admin_reply or '感谢您的贡献！'}",
            )
        except Exception:
            logger.exception("采纳通知发送失败（旁路）")
        _wx_notify_correction(corr, body.admin_reply)
    updated = CorrectionRepo.get_any(corr_id)
    return ok({"correction": updated, "notified": body.status == "accepted"})


def _wx_notify_correction(corr: dict, admin_reply: str | None) -> None:
    """微信订阅消息推送（未配置模板或非微信账号时静默跳过）。"""
    try:
        record = UserRepo.get_by_id(corr["user_id"])
        openid = record.get("openid") if record else None
        if not openid:
            return
        wx_sec.send_subscribe_notice(
            openid,
            get_settings().wx_subscribe_template_id,
            page="pages/profile/index",
            data={
                "thing1": {"value": (corr.get("doc_ref") or "内容纠错")[:20]},
                "phrase2": {"value": "已采纳"},
            },
        )
    except Exception:
        logger.warning("微信订阅消息通知失败（旁路）", exc_info=True)


@router.get("/qc/bad", summary="低质量回答聚合（同一问题多次点踩）")
async def bad_questions(
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """点踩聚合列表（按问题分组计数 + 最近一条点踩的评论/ trace_id），供管理员干预或补语料。"""
    try:
        items = FeedbackRepo.bad_questions(limit=limit)
    except Exception as exc:
        raise ApiError(ErrorCode.INTERNAL, f"聚合点踩数据失败：{exc}") from exc
    return ok({"items": items})
