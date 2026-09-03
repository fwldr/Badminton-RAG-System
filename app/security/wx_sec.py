"""微信开放能力接入：内容安全（msgSecCheck v2）+ 订阅消息（subscribe/send）。

设计原则（与项目其他外部依赖一致）：
- **配置门控**：WX_APPID/WX_SECRET（内容安全）或 WX_SUBSCRIBE_TEMPLATE_ID 未配置时返回 None（跳过），
  绝不阻断主流程——开发/测试环境零依赖；
- **可注入**：`_wx_get` / `_wx_post_json` / `_get_access_token` 为模块级函数，测试 monkeypatch 替换，不触网；
- access_token 进程内缓存（过期前 60s 刷新），失败降级 None。
"""

from __future__ import annotations

import logging
import time

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_token_cache: dict = {"token": "", "expire_at": 0.0}


def _wx_get(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, timeout=10)
    return resp.json()


def _wx_post_json(url: str, payload: dict) -> dict:
    resp = httpx.post(url, json=payload, timeout=10)
    return resp.json()


def _get_access_token() -> str | None:
    """获取小程序 access_token（进程内缓存；失败返回 None）。"""
    settings = get_settings()
    if not (settings.wx_appid and settings.wx_secret):
        return None
    now = time.time()
    if _token_cache.get("expire_at", 0) - 60 > now:
        return _token_cache["token"]
    try:
        data = _wx_get(
            "https://api.weixin.qq.com/cgi-bin/token",
            {
                "grant_type": "client_credential",
                "appid": settings.wx_appid,
                "secret": settings.wx_secret,
            },
        )
    except Exception:
        logger.warning("获取微信 access_token 失败（网络异常）", exc_info=True)
        return None
    token = data.get("access_token")
    if not token:
        logger.warning("获取微信 access_token 失败：%s", data.get("errmsg"))
        return None
    _token_cache["token"] = token
    _token_cache["expire_at"] = now + float(data.get("expires_in", 7200))
    return token


def check_text(text: str, openid: str | None = None) -> bool | None:
    """微信内容安全校验（msgSecCheck v2，文本）。

    返回：True=通过；False=违规；None=未开通或检查失败（放行，不阻断）。
    scope=1（资料/评论/动态等用户提交内容）。
    """
    settings = get_settings()
    if not (settings.wx_appid and settings.wx_secret):
        return None
    token = _get_access_token()
    if not token:
        return None
    try:
        data = _wx_post_json(
            f"https://api.weixin.qq.com/wxa/msg_sec_check?access_token={token}",
            {
                "version": 2,
                "scene": 1,
                "openid": openid or "",
                "content": (text or "")[:2500],
            },
        )
    except Exception:
        logger.warning("msgSecCheck 调用异常，跳过检查", exc_info=True)
        return None
    if data.get("errcode", 0) != 0:
        logger.warning("msgSecCheck 返回错误：%s", data.get("errmsg"))
        return None
    suggest = (data.get("result") or {}).get("suggest", "pass")
    return suggest != "risky"


def send_subscribe_notice(
    openid: str,
    template_id: str,
    page: str,
    data: dict,
) -> bool | None:
    """发送订阅消息。返回 True=已发送；None=未配置/未开启；False=发送失败（不抛出）。"""
    if not template_id:
        return None
    token = _get_access_token()
    if not token:
        return None
    try:
        resp = _wx_post_json(
            f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}",
            {"touser": openid, "template_id": template_id, "page": page, "data": data},
        )
    except Exception:
        logger.warning("订阅消息发送异常", exc_info=True)
        return False
    if resp.get("errcode", 0) != 0:
        logger.warning("订阅消息发送失败：%s", resp.get("errmsg"))
        return False
    return True
