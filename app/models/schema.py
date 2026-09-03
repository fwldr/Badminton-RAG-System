"""API 请求/响应数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """POST /ask 请求体。"""

    question: str = Field(min_length=1, max_length=500, description="用户问题")


class AskSource(BaseModel):
    """回答引用的来源（品牌 + 型号 + 所属表）。"""

    table: str = Field(description="所属规格表（如 racket_specs）")
    brand: str = Field(description="品牌")
    model: str = Field(description="型号")


class AskResponse(BaseModel):
    """POST /ask 响应体。"""

    answer: str = Field(description="中文回答，末尾附来源")
    sources: list[AskSource] = Field(default_factory=list, description="引用的商品来源")
