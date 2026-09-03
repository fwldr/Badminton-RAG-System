"""用户反馈路由：POST /feedback（点赞/点踩 + 评论），作为在线 bad case 来源。

登录用户提交时自动关联 user_id（匿名用户 user_id=0），用于按用户维度的个性化与服务台。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_optional, rate_limit
from app.api.errors import ok
from app.db.repos import FeedbackRepo

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    """POST /feedback 请求体。"""

    session_id: str = Field(default="", max_length=100, description="会话标识")
    question: str = Field(min_length=1, max_length=500, description="用户问题")
    answer: str | None = Field(default=None, max_length=2000, description="系统回答")
    rating: Literal[1, -1] = Field(description="1 赞 / -1 踩")
    comment: str | None = Field(default=None, max_length=500, description="评论（可选）")
    trace_id: str | None = Field(default=None, max_length=64, description="/chat 返回的追踪 ID")


@router.post("/feedback", summary="提交问答反馈（点赞/点踩）")
async def submit_feedback(
    req: FeedbackRequest,
    current_user: dict | None = Depends(get_current_user_optional),
    _: None = Depends(rate_limit()),
) -> dict:
    """落库一条反馈；点踩记录会被 collect_bad_cases 收集进 bad case 清单。"""
    fid = FeedbackRepo.insert(
        req.session_id,
        req.question,
        req.answer,
        req.rating,
        req.comment,
        req.trace_id,
        user_id=current_user["id"] if current_user else 0,
    )
    return ok({"id": fid})
