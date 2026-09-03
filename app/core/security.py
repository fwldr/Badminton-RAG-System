"""安全原语：密码哈希 + 签名会话令牌（纯 stdlib，无第三方依赖）。

- 密码：PBKDF2-HMAC-SHA256 加盐哈希，格式 `pbkdf2_sha256$迭代数$盐hex$哈希hex`。
- 令牌：HMAC-SHA256 签名的 Bearer token，形如 `base64url(payload).签名hex`，
  payload 含 ``sub``（用户 id）/``role``/``exp``（过期时间戳）。
  用法与 JWT 兼容（`Authorization: Bearer <token>`），但省去 python-jose/PyJWT 依赖，
  与本项目 `hmac.compare_digest` 的管理密钥风格一致。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_ITERATIONS = 200_000  # PBKDF2 迭代数（demo 用；生产可调高并换密码库）


def hash_password(password: str) -> str:
    """生成加盐哈希。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    )
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """校验密码（恒定时间比较哈希摘要）。"""
    try:
        algo, iterations, salt, expected = hashed.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_token(payload: dict, secret: str, ttl: int) -> str:
    """创建签名令牌：写入 exp，返回 `payload.b64.签名hex`。"""
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = _b64url_encode(raw)
    sig = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def decode_token(token: str, secret: str) -> dict | None:
    """校验签名与过期时间；无效返回 None。"""
    try:
        encoded, sig = token.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
