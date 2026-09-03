"""API 依赖：管理鉴权 + 用户鉴权（JWT 兼容 Bearer token）+ 限流依赖工厂。"""

from __future__ import annotations

import hmac
import json

from fastapi import Depends, Request

from app.api.errors import ApiError, ErrorCode
from app.core.config import get_settings
from app.core.ratelimit import RateLimiter
from app.core.security import decode_token
from app.db.repos import UserRepo

# 进程级限流器实例（按配置初始化；单机 demo 足够）
_ask_limiter: RateLimiter | None = None
_admin_limiter: RateLimiter | None = None


def _get_ask_limiter() -> RateLimiter:
    global _ask_limiter
    if _ask_limiter is None:
        s = get_settings()
        _ask_limiter = RateLimiter(s.rate_limit_ask_capacity, s.rate_limit_ask_refill)
    return _ask_limiter


def _get_admin_limiter() -> RateLimiter:
    global _admin_limiter
    if _admin_limiter is None:
        s = get_settings()
        _admin_limiter = RateLimiter(s.rate_limit_admin_capacity, s.rate_limit_admin_refill)
    return _admin_limiter


def reset_limiters() -> None:
    """清空进程级限流器（测试隔离用：每个测试重建令牌桶，避免跨测试限流）。"""
    global _ask_limiter, _admin_limiter
    _ask_limiter = None
    _admin_limiter = None


def _client_ip(request: Request) -> str:
    """取客户端 IP（反代场景取 X-Forwarded-For 首段，demo 直接用 client.host）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_admin_key(request: Request) -> None:
    """校验 X-Admin-Key 头与配置的管理密钥一致（恒定时间比较）。

    保留用于向后兼容（旧前端/测试仍用共享密钥）；管理端优先走管理员 JWT（require_admin_access）。
    """
    settings = get_settings()
    expected = settings.admin_api_key
    if not expected:
        raise ApiError(ErrorCode.UNAUTHORIZED, "管理接口未配置 ADMIN_API_KEY")
    provided = request.headers.get("x-admin-key", "")
    if not hmac.compare_digest(provided, expected):
        raise ApiError(ErrorCode.UNAUTHORIZED, "X-Admin-Key 无效")


def _bearer_token(request: Request) -> str | None:
    """取 `Authorization: Bearer <token>` 中的令牌；无则返回 None。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def get_current_user_optional(request: Request) -> dict | None:
    """可选用户鉴权：带有效 Bearer 令牌则返回用户记录，否则返回 None（不抛错）。

    用于用户端接口（/ask、/chat、/feedback）：登录态把用户关联进来，匿名用户仍可用。
    """
    token = _bearer_token(request)
    if not token:
        return None
    settings = get_settings()
    payload = decode_token(token, settings.auth_token_secret)
    if not payload or "sub" not in payload:
        return None
    user = UserRepo.get_by_id(int(payload["sub"]))
    if not user or not user["is_active"]:
        return None
    try:
        UserRepo.update_last_active(user["id"])
    except Exception:  # 旁路：最近活跃时间写失败不影响主链路
        pass
    return user


def get_current_user(request: Request) -> dict:
    """强制用户鉴权：未带有效令牌即 401。"""
    user = get_current_user_optional(request)
    if user is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "请先登录")
    return user


def require_admin(request: Request) -> dict:
    """强制管理员鉴权：需登录且 role=admin，否则 401/403。"""
    user = get_current_user(request)
    if user["role"] != "admin":
        raise ApiError(ErrorCode.FORBIDDEN, "需要管理员权限")
    return user


# 管理端一级导航模块（对应用户和管理员的设计.md 后台模块清单；模块级权限分配用）
ADMIN_MODULES: tuple[str, ...] = ("dashboard", "kb", "rag", "review", "system")


def require_admin_module(module: str):
    """模块级权限校验（RAG 调优中心/审核等新模块端点用）。

    - 必须登录管理员（严格 require_admin 语义，旧 X-Admin-Key 不适用）；
    - users.permissions 为 NULL = 拥有全部模块；否则 JSON 数组须包含该模块名。
    """

    def dependency(request: Request) -> dict:
        user = require_admin(request)
        permissions = user.get("permissions")
        if permissions is None or permissions == "":
            return user
        try:
            granted = json.loads(permissions) if isinstance(permissions, str) else permissions
        except (TypeError, ValueError):
            granted = []
        if module not in (granted or []):
            raise ApiError(ErrorCode.FORBIDDEN, f"无权访问模块：{module}")
        return user

    return dependency


def require_admin_access(request: Request) -> dict | None:
    """管理访问（向后兼容）：有效管理员 JWT 或旧 X-Admin-Key 任一通过即可。

    新引入的管理员 JWT（require_admin）用于严格 RBAC 路径（如用户与权限管理）；
    此依赖用于既有管理/审计端点，保证旧共享密钥与新管理员账户都可用。
    """
    user = get_current_user_optional(request)
    if user is not None and user["role"] == "admin":
        return user
    settings = get_settings()
    expected = settings.admin_api_key
    if expected:
        provided = request.headers.get("x-admin-key", "")
        if hmac.compare_digest(provided, expected):
            return None
    raise ApiError(ErrorCode.UNAUTHORIZED, "需要管理员权限")


def rate_limit(capacity: int | None = None, refill: float | None = None):
    """限流依赖工厂：按 client_ip 计数，超限抛 42901。

    用法：Depends(rate_limit()) 使用配置默认值；或 Depends(rate_limit(10, 1)) 自定义。
    """
    limiter: RateLimiter | None = None

    def dependency(request: Request) -> None:
        nonlocal limiter
        if limiter is None:
            if capacity is not None and refill is not None:
                limiter = RateLimiter(capacity, refill)
            else:
                limiter = _get_ask_limiter()
        if not limiter.acquire(_client_ip(request)):
            raise ApiError(ErrorCode.RATE_LIMITED, "请求过于频繁，请稍后再试")

    return dependency


def admin_rate_limit():
    """管理接口限流依赖（独立桶）。"""
    limiter: RateLimiter | None = None

    def dependency(request: Request) -> None:
        nonlocal limiter
        if limiter is None:
            limiter = _get_admin_limiter()
        if not limiter.acquire(_client_ip(request)):
            raise ApiError(ErrorCode.RATE_LIMITED, "请求过于频繁，请稍后再试")

    return dependency
