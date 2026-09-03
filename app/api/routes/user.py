"""用户端路由（/user/*）：个人知识工作台 + 知识探索 + 互动贡献 + 账户服务。

全部依赖登录（get_current_user）。包括：
- 会话：历史对话记录（搜索/标签/收藏/重命名/删除）
- 收藏夹与文件夹
- 动态（文本+图片）与热门问答排行
- 内容纠错提交、消息通知
- 图片上传（动态配图）
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.api.errors import ApiError, ErrorCode, ok
from app.core.config import get_settings
from app.db.database import get_conn
from app.db.repos import (
    ConversationRepo,
    CorrectionRepo,
    FavoriteFolderRepo,
    FavoriteRepo,
    MessageRepo,
    NotificationRepo,
    PostRepo,
    UserRepo,
)
from app.ingest.doc_ingest import IMAGE_EXTS
from app.security import wx_sec

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["user"])

_UPLOAD_IMAGE_LIMIT = 2 * 1024 * 1024  # 单张动态图片 ≤ 2MB


def _text_guard(user: dict, content: str) -> None:
    """微信内容安全（UGC 三处：动态/回复/纠错）。未配置或检查失败时放行，绝不阻断。"""
    try:
        record = UserRepo.get_by_id(user["id"])
        openid = record.get("openid") if record else None
        result = wx_sec.check_text(content, openid)
    except Exception:
        logger.warning("内容安全校验异常，放行", exc_info=True)
        return
    if result is False:
        raise ApiError(ErrorCode.VALIDATION, "内容未通过安全校验，请修改后重试")


def _parse_sources(sources_json: str | None) -> list[dict]:
    if not sources_json:
        return []
    try:
        data = json.loads(sources_json)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


# ==================== 会话（历史对话记录） ====================


class ConversationCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=100)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    tag: str | None = Field(default=None, max_length=32)
    is_favorite: bool | None = None


@router.get("/conversations", summary="会话列表（搜索/标签/收藏筛选）")
async def list_conversations(
    user: dict = Depends(get_current_user),
    q: str = Query("", max_length=100),
    tag: str = Query("", max_length=32),
    favorite: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    convs = ConversationRepo.list_user(user["id"], q=q, tag=tag, favorite=favorite, limit=limit, offset=offset)
    return ok({"total": ConversationRepo.count_user(user["id"]), "conversations": convs})


@router.post("/conversations", summary="创建会话")
async def create_conversation(
    body: ConversationCreate,
    user: dict = Depends(get_current_user),
) -> dict:
    conv_id = ConversationRepo.upsert(user["id"], body.session_id, title=body.title)
    return ok({"id": conv_id, "session_id": body.session_id})


@router.get("/conversations/{conv_id}", summary="会话详情（含消息）")
async def conversation_detail(
    conv_id: int,
    user: dict = Depends(get_current_user),
) -> dict:
    conv = ConversationRepo.get(user["id"], conv_id)
    if not conv:
        raise ApiError(ErrorCode.NOT_FOUND, "会话不存在")
    messages = MessageRepo.list_conversation(conv_id)
    return ok({
        **conv,
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "sources": _parse_sources(m["sources_json"]),
                "trace_id": m["trace_id"],
                "cached": m["cached"],
                "created_at": m["created_at"],
            }
            for m in messages
        ],
    })


@router.patch("/conversations/{conv_id}", summary="重命名/标签/收藏")
async def patch_conversation(
    conv_id: int,
    body: ConversationPatch,
    user: dict = Depends(get_current_user),
) -> dict:
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if "is_favorite" in fields and isinstance(fields["is_favorite"], bool):
        fields["is_favorite"] = 1 if fields["is_favorite"] else 0
    conv = ConversationRepo.update(user["id"], conv_id, fields)
    if not conv:
        raise ApiError(ErrorCode.NOT_FOUND, "会话不存在")
    return ok(conv)


@router.delete("/conversations/{conv_id}", summary="删除会话")
async def delete_conversation(
    conv_id: int,
    user: dict = Depends(get_current_user),
) -> dict:
    if not ConversationRepo.delete(user["id"], conv_id):
        raise ApiError(ErrorCode.NOT_FOUND, "会话不存在")
    return ok({"id": conv_id})


# ==================== 收藏夹与文件夹 ====================


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)


@router.get("/folders", summary="收藏文件夹列表")
async def list_folders(user: dict = Depends(get_current_user)) -> dict:
    return ok({"folders": FavoriteFolderRepo.list_user(user["id"])})


@router.post("/folders", summary="新建收藏文件夹")
async def create_folder(body: FolderCreate, user: dict = Depends(get_current_user)) -> dict:
    fid = FavoriteFolderRepo.create(user["id"], body.name.strip())
    return ok({"id": fid, "name": body.name.strip()})


@router.delete("/folders/{folder_id}", summary="删除收藏文件夹（收藏保留为未分类）")
async def delete_folder(folder_id: int, user: dict = Depends(get_current_user)) -> dict:
    if not FavoriteFolderRepo.delete(user["id"], folder_id):
        raise ApiError(ErrorCode.NOT_FOUND, "文件夹不存在")
    return ok({"id": folder_id})


class FavoriteCreate(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=8000)
    sources: list[dict] = Field(default_factory=list)
    folder_id: int | None = None


class FavoritePatch(BaseModel):
    folder_id: int | None = None


@router.get("/favorites", summary="收藏列表（搜索/按文件夹筛选）")
async def list_favorites(
    user: dict = Depends(get_current_user),
    q: str = Query("", max_length=100),
    folder_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    favs = FavoriteRepo.list_user(user["id"], q=q, folder_id=folder_id, limit=limit, offset=offset)
    for f in favs:
        f["sources"] = _parse_sources(f["sources_json"])
    return ok({"total": FavoriteRepo.count_user(user["id"]), "favorites": favs})


@router.post("/favorites", summary="收藏回答")
async def create_favorite(body: FavoriteCreate, user: dict = Depends(get_current_user)) -> dict:
    if body.folder_id is not None and not FavoriteFolderRepo.get(user["id"], body.folder_id):
        raise ApiError(ErrorCode.VALIDATION, "文件夹不存在")
    fav_id = FavoriteRepo.create(
        user["id"], body.question, body.answer,
        sources_json=json.dumps(body.sources, ensure_ascii=False) if body.sources else None,
        folder_id=body.folder_id,
    )
    return ok({"id": fav_id})


@router.patch("/favorites/{fav_id}", summary="移动收藏到文件夹（folder_id=null 取消分类）")
async def patch_favorite(
    fav_id: int,
    body: FavoritePatch,
    user: dict = Depends(get_current_user),
) -> dict:
    if not FavoriteRepo.get(user["id"], fav_id):
        raise ApiError(ErrorCode.NOT_FOUND, "收藏不存在")
    if body.folder_id is not None and not FavoriteFolderRepo.get(user["id"], body.folder_id):
        raise ApiError(ErrorCode.VALIDATION, "文件夹不存在")
    fav_id_ret = FavoriteRepo.set_folder(user["id"], fav_id, body.folder_id)
    return ok({"id": fav_id, "folder_id": body.folder_id})


@router.delete("/favorites/{fav_id}", summary="删除收藏")
async def delete_favorite(fav_id: int, user: dict = Depends(get_current_user)) -> dict:
    if not FavoriteRepo.delete(user["id"], fav_id):
        raise ApiError(ErrorCode.NOT_FOUND, "收藏不存在")
    return ok({"id": fav_id})


# ==================== 动态与热门 ====================


class PostCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000, description="动态正文（文本+图片）")
    images: list[str] = Field(default_factory=list, max_length=3, description="/uploads/posts/xxx 路径")


@router.get("/posts", summary="球友动态（公开浏览，含已赞状态与回复数）")
async def list_posts(
    user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    posts = PostRepo.list_feed(viewer_id=user["id"], limit=limit, offset=offset)
    for p in posts:
        try:
            p["images"] = json.loads(p["images_json"]) if p["images_json"] else []
        except (ValueError, TypeError):
            p["images"] = []
    return ok({"total": PostRepo.count(), "posts": posts})


@router.post("/posts", summary="发布动态（文本 + 图片）")
async def create_post(body: PostCreate, user: dict = Depends(get_current_user)) -> dict:
    if len(body.images) > 3:
        raise ApiError(ErrorCode.VALIDATION, "最多 3 张图片")
    for path in body.images:
        if not path.startswith("/uploads/"):
            raise ApiError(ErrorCode.VALIDATION, "图片路径非法")
    _text_guard(user, body.content)
    pid = PostRepo.create(user["id"], body.content.strip(), body.images)
    return ok({"id": pid})


@router.get("/posts/{post_id}", summary="动态详情（含已赞状态与回复数）")
async def get_post(post_id: int, user: dict = Depends(get_current_user)) -> dict:
    post = PostRepo.get_feed(post_id, viewer_id=user["id"])
    if post is None:
        raise ApiError(ErrorCode.NOT_FOUND, "动态不存在")
    try:
        post["images"] = json.loads(post["images_json"]) if post["images_json"] else []
    except (ValueError, TypeError):
        post["images"] = []
    return ok({"post": post})


@router.post("/posts/{post_id}/like", summary="点赞动态（再点取消；每用户每条限 1 次）")
async def like_post(post_id: int, user: dict = Depends(get_current_user)) -> dict:
    if PostRepo.get(post_id) is None:
        raise ApiError(ErrorCode.NOT_FOUND, "动态不存在")
    res = PostRepo.toggle_like(post_id, user["id"])
    return ok({"id": post_id, **res})


@router.get("/posts/{post_id}/replies", summary="动态回复列表（一级 + 楼中楼）")
async def list_replies(post_id: int, user: dict = Depends(get_current_user)) -> dict:
    if PostRepo.get(post_id) is None:
        raise ApiError(ErrorCode.NOT_FOUND, "动态不存在")
    replies = PostRepo.list_replies(post_id, viewer_id=user["id"])
    return ok({"replies": replies})


class ReplyCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500, description="回复内容（文本）")
    parent_id: int | None = Field(default=None, description="被回复的一级回复 id（楼中楼；NULL=直接回复动态）")


@router.post("/posts/{post_id}/replies", summary="回复动态（可回复他人回复，仅一层楼中楼）")
async def create_reply(post_id: int, body: ReplyCreate, user: dict = Depends(get_current_user)) -> dict:
    if PostRepo.get(post_id) is None:
        raise ApiError(ErrorCode.NOT_FOUND, "动态不存在")
    content = body.content.strip()
    if not content:
        raise ApiError(ErrorCode.VALIDATION, "回复内容不能为空")
    _text_guard(user, content)
    parent_id, reply_to_user_id = None, None
    if body.parent_id is not None:
        target = PostRepo.get_reply(body.parent_id)
        if target is None or target["post_id"] != post_id:
            raise ApiError(ErrorCode.NOT_FOUND, "被回复的回复不存在")
        # 只允许一层楼中楼：树挂载点一律为一级回复；被回复者按实际目标记录
        parent_id = target["parent_id"] if target["parent_id"] is not None else target["id"]
        reply_to_user_id = target["user_id"]
    rid = PostRepo.add_reply(post_id, user["id"], content, parent_id, reply_to_user_id)
    return ok({"id": rid})


@router.post("/replies/{reply_id}/like", summary="点赞回复（再点取消；每用户每条限 1 次）")
async def like_reply(reply_id: int, user: dict = Depends(get_current_user)) -> dict:
    if PostRepo.get_reply(reply_id) is None:
        raise ApiError(ErrorCode.NOT_FOUND, "回复不存在")
    res = PostRepo.toggle_reply_like(reply_id, user["id"])
    return ok({"id": reply_id, **res})


@router.get("/hot", summary="热门问答排行（赞 + 收藏聚合）")
async def hot_questions(user: dict = Depends(get_current_user), limit: int = Query(10, ge=1, le=50)) -> dict:
    """按「点👍次数 + 被收藏次数」聚合，返回 Top N 问题。"""
    rows = get_conn().execute(
        "SELECT q, SUM(n) AS score FROM ("
        "  SELECT question AS q, COUNT(*) AS n FROM feedback WHERE rating = 1 GROUP BY question "
        "  UNION ALL "
        "  SELECT question AS q, COUNT(*) AS n FROM favorites GROUP BY question"
        ") t GROUP BY q ORDER BY score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return ok({"hot": [{"question": r["q"], "score": r["score"]} for r in rows]})


# ==================== 内容纠错 ====================


class CorrectionCreate(BaseModel):
    doc_ref: str | None = Field(default=None, max_length=200, description="引用文档/原文片段标识")
    original_text: str | None = Field(default=None, max_length=2000)
    corrected_text: str = Field(min_length=1, max_length=2000)
    reason: str | None = Field(default=None, max_length=500)


@router.post("/corrections", summary="提交内容纠错")
async def create_correction(body: CorrectionCreate, user: dict = Depends(get_current_user)) -> dict:
    _text_guard(user, (body.corrected_text or "") + (body.original_text or "") + (body.reason or ""))
    cid = CorrectionRepo.create(
        user["id"], body.doc_ref, body.original_text, body.corrected_text.strip(), body.reason
    )
    return ok({"id": cid, "status": "pending"})


@router.get("/corrections", summary="我的纠错记录")
async def list_corrections(
    user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    return ok({"corrections": CorrectionRepo.list_user(user["id"], limit=limit, offset=offset)})


# ==================== 消息通知 ====================


class NotificationRead(BaseModel):
    ids: list[int] | None = None  # None = 全部已读


@router.get("/notifications", summary="消息通知列表")
async def list_notifications(
    user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    return ok({
        "unread": NotificationRepo.unread_count(user["id"]),
        "notifications": NotificationRepo.list_user(user["id"], limit=limit, offset=offset),
    })


@router.post("/notifications/read", summary="标记已读（ids 为空则全部）")
async def mark_notifications_read(
    body: NotificationRead,
    user: dict = Depends(get_current_user),
) -> dict:
    updated = NotificationRepo.mark_read(user["id"], body.ids)
    return ok({"updated": updated})


# ==================== 图片上传（动态配图） ====================


@router.post("/uploads", summary="上传图片（动态配图，单张 ≤2MB）")
async def upload_image(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> dict:
    filename = file.filename or "unnamed"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in IMAGE_EXTS:
        raise ApiError(ErrorCode.VALIDATION, f"仅支持图片格式 {sorted(IMAGE_EXTS)}")
    data = await file.read()
    if len(data) > _UPLOAD_IMAGE_LIMIT:
        raise ApiError(ErrorCode.VALIDATION, "图片不能超过 2MB")
    if not data:
        raise ApiError(ErrorCode.VALIDATION, "图片内容为空")
    uploads_dir = Path(get_settings().user_uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(8)}.{ext}"
    (uploads_dir / name).write_bytes(data)
    # 挂载根即 posts 目录：/uploads/{name}（原始文档不在该目录，不会被暴露）
    return ok({"path": f"/uploads/{name}"})
