"""用户认证路由：注册 / 登录 / 微信小程序登录 / 当前用户 / 资料与偏好（双角色：用户 user / 管理员 admin）。

- ``POST /auth/register`` — 注册普通用户（固定 role=user；管理员由种子或后台分配）。
- ``POST /auth/login`` — 用户名+密码登录，返回 Bearer token 与用户信息。
- ``POST /auth/wechat`` — 微信小程序登录（code2session → openid 绑定/建号，签发同一 token）。
- ``GET /auth/me`` — 返回当前登录用户信息（需 Bearer token）。
- ``PATCH /auth/profile`` — 更新个人资料与偏好（头像/昵称/性别/水平/球拍/语气/引用开关）。
"""

from __future__ import annotations

import re
from typing import Literal

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, rate_limit
from app.api.errors import ApiError, ErrorCode, ok
from app.core.config import get_settings
from app.core.security import create_token, hash_password, verify_password
from app.db.repos import NotificationRepo, UserRepo, user_to_public

router = APIRouter(prefix="/auth", tags=["auth"])

# 用户名：2-32 位字母/数字/下划线/中文
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fa5]{2,32}$")

# 微信 code2session 端点（单测用 monkeypatch 替换 wx_code2session，不触网）
WX_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


class RegisterRequest(BaseModel):
    """POST /auth/register 请求体。"""

    username: str = Field(min_length=2, max_length=32, description="用户名")
    password: str = Field(min_length=6, max_length=64, description="密码（≥6 位）")
    nickname: str | None = Field(default=None, max_length=32, description="昵称（默认取用户名）")


class LoginRequest(BaseModel):
    """POST /auth/login 请求体。"""

    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=64)


class ProfilePatch(BaseModel):
    """PATCH /auth/profile 请求体（全字段可选，None 不修改）。"""

    nickname: str | None = Field(default=None, min_length=1, max_length=32)
    gender: Literal["男", "女", "保密"] | None = None
    level: Literal["新手", "进阶", "专业"] | None = None
    racket_model: str | None = Field(default=None, max_length=64)
    # 头像：emoji 字符（≤8 字）或 /uploads/xxx 图片路径（chooseAvatar 上传后）
    avatar: str | None = Field(default=None, max_length=255)
    pref_style: Literal["simple", "detailed"] | None = None
    pref_show_sources: bool | None = None


class WechatLoginRequest(BaseModel):
    """POST /auth/wechat 请求体（code 为 wx.login 临时凭证，不含任何用户信息）。"""

    code: str = Field(min_length=1, max_length=64, description="wx.login 返回的临时 code")
    nickname: str | None = Field(default=None, max_length=32, description="新用户昵称（可选）")


class WechatPhoneRequest(BaseModel):
    """POST /auth/wechat/phone 请求体（code 为 getPhoneNumber 返回的临时 code）。"""

    code: str = Field(min_length=1, max_length=64)


class UnbindRequest(BaseModel):
    """POST /auth/unbind 请求体：解绑微信登录（wechat）或手机号（phone）。"""

    type: Literal["wechat", "phone"]


def wx_code2session(code: str) -> dict:
    """调用微信 code2session 换取 openid（独立函数：测试 monkeypatch 注入，不触网）。"""
    settings = get_settings()
    resp = httpx.get(
        WX_CODE2SESSION_URL,
        params={
            "appid": settings.wx_appid,
            "secret": settings.wx_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    return resp.json()


def wx_get_phone_number(code: str) -> str | None:
    """调用微信手机号快速验证换取 purePhoneNumber（独立函数：测试注入，不触网）。"""
    from app.security import wx_sec

    token = wx_sec._get_access_token()
    if not token:
        raise ApiError(ErrorCode.INTERNAL, "微信手机号服务未配置或授权失败")
    data = wx_sec._wx_post_json(  # noqa: SLF001 - 模块内共用（对外仍走 check_text 等公开函数）
        f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={token}",
        {"code": code},
    )
    if data.get("errcode", 0) != 0:
        raise ApiError(ErrorCode.UNAUTHORIZED, f"手机号获取失败：{data.get('errmsg', 'code 无效')}")
    phone_info = data.get("phone_info") or {}
    return phone_info.get("purePhoneNumber")


def _issue_token(user: dict) -> str:
    settings = get_settings()
    return create_token(
        {"sub": user["id"], "username": user["username"], "role": user["role"]},
        settings.auth_token_secret,
        settings.auth_token_ttl,
    )


@router.post("/register", summary="注册（默认角色 user）")
async def register(req: RegisterRequest, _: None = Depends(rate_limit())) -> dict:
    """注册普通用户并自动登录（返回 token）。"""
    if not USERNAME_RE.match(req.username):
        raise ApiError(ErrorCode.VALIDATION, "用户名需为 2-32 位字母/数字/下划线/中文")
    if UserRepo.get_by_username(req.username) is not None:
        raise ApiError(ErrorCode.CONFLICT, "用户名已被占用")
    uid = UserRepo.create(req.username, hash_password(req.password), role="user", nickname=req.nickname or req.username)
    user = UserRepo.get_by_id(uid)
    try:
        NotificationRepo.create(
            uid, "system", "欢迎加入羽问 🏸",
            "登录后可体验：历史对话记录、收藏夹、知识库发现、动态与纠错反馈。",
        )
    except Exception:
        pass  # 欢迎通知失败不影响注册
    return ok({"token": _issue_token(user), "user": user_to_public(user)})


@router.post("/login", summary="登录（返回 token + 用户信息）")
async def login(req: LoginRequest, _: None = Depends(rate_limit())) -> dict:
    """校验用户名密码，签发 Bearer token。"""
    user = UserRepo.get_by_username(req.username)
    if user is None or not verify_password(req.password, user["password_hash"]):
        raise ApiError(ErrorCode.UNAUTHORIZED, "用户名或密码错误")
    if not user["is_active"]:
        raise ApiError(ErrorCode.FORBIDDEN, "账号已被禁用")
    UserRepo.update_last_active(user["id"])
    return ok({"token": _issue_token(user), "user": user_to_public(user)})


@router.post("/wechat", summary="微信小程序登录（code2session → 绑定/建号）")
async def wechat_login(req: WechatLoginRequest, _: None = Depends(rate_limit())) -> dict:
    """微信小程序一键登录：服务端用 code 换 openid，同 openid 复用账号，否则自动建号。

    返回与 /auth/login 相同的 {token, user}，并附 is_new 供前端展示「欢迎」。
    未配置 WX_APPID/WX_SECRET 时返回 500（便于开发环境显式感知）。
    """
    settings = get_settings()
    if not settings.wx_appid or not settings.wx_secret:
        raise ApiError(ErrorCode.INTERNAL, "微信登录未配置（WX_APPID / WX_SECRET）")
    try:
        data = wx_code2session(req.code)
    except Exception:
        raise ApiError(ErrorCode.INTERNAL, "微信登录服务不可用")
    if data.get("errcode") or not data.get("openid"):
        raise ApiError(ErrorCode.UNAUTHORIZED, f"微信登录失败：{data.get('errmsg', 'code 无效')}")

    openid = data["openid"]
    user = UserRepo.get_by_openid(openid)
    is_new = user is None
    if is_new:
        uid = UserRepo.create_wx(openid, req.nickname)
        user = UserRepo.get_by_id(uid)
        try:
            NotificationRepo.create(
                uid, "system", "欢迎加入羽问 🏸",
                "微信登录成功。历史对话、收藏夹、知识库发现、动态与纠错均可使用。",
            )
        except Exception:
            pass  # 欢迎通知失败不影响登录
    if not user["is_active"]:
        raise ApiError(ErrorCode.FORBIDDEN, "账号已被禁用")
    UserRepo.update_last_active(user["id"])
    return ok({"token": _issue_token(user), "user": user_to_public(user), "is_new": is_new})


@router.post("/wechat/phone", summary="绑定微信手机号（手机号快速验证）")
async def wechat_bind_phone(
    req: WechatPhoneRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """getPhoneNumber 返回 code → 服务端换手机号 → 绑定当前账号。

    若手机号已绑定其他账号 → 409（不自动合并，避免误合并）；成功返回 {phone_bound, user}。
    """
    phone = wx_get_phone_number(req.code)
    existing = UserRepo.get_by_phone(phone)
    if existing is not None and existing["id"] != user["id"]:
        raise ApiError(ErrorCode.CONFLICT, "该手机号已绑定其他账号")
    UserRepo.bind_phone(user["id"], phone)
    updated = UserRepo.get_by_id(user["id"])
    return ok({"phone_bound": True, "user": user_to_public(updated)})


@router.post("/unbind", summary="解绑微信登录或手机号")
async def unbind(
    body: UnbindRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """解绑后：wechat 类型需重新用微信登录（且该账号可能无法再登录）；
    phone 类型解除手机号关联。返回最新用户。"""
    if body.type == "wechat":
        UserRepo.bind_openid(user["id"], None)
    else:
        UserRepo.bind_phone(user["id"], None)
    updated = UserRepo.get_by_id(user["id"])
    return ok(user_to_public(updated))


@router.get("/me", summary="当前登录用户")
async def me(user: dict = Depends(get_current_user)) -> dict:
    """返回当前登录用户信息（含 role，前端据此路由到用户端/管理端）。"""
    return ok(user_to_public(user))


@router.patch("/profile", summary="更新个人资料与偏好")
async def update_profile(
    body: ProfilePatch,
    user: dict = Depends(get_current_user),
) -> dict:
    """更新头像/昵称/性别/打球水平/常用球拍/回答语气/引用开关（个性化推荐数据来源）。"""
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if "pref_show_sources" in fields and isinstance(fields["pref_show_sources"], bool):
        fields["pref_show_sources"] = 1 if fields["pref_show_sources"] else 0
    updated = UserRepo.update_profile(user["id"], fields)
    return ok(user_to_public(updated))
