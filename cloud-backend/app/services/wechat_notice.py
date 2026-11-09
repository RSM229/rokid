"""微信小程序订阅消息服务 - 调用 subscribeMessage.send 推送服务通知

作者: cloud-backend
创建日期: 2026-07-18
"""
import logging
import time
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.models.miniprogram import WechatUserBinding
from app.models.wechat_subscription import WechatSubscription
from app.services.subscribe_pages import normalize_subscribe_page

logger = logging.getLogger(__name__)

# access_token 内存缓存：{(appid, secret): (token, expire_ts)}
_access_token_cache: dict[tuple, tuple[str, float]] = {}


async def get_access_token() -> str:
    """获取微信 access_token（带缓存，2 小时过期前刷新）

    依赖 appid 与 appsecret。若未配置则返回空串并跳过实际调用。
    """
    appid = settings.wechat_app_id
    secret = settings.wechat_app_secret
    if not appid or not secret:
        logger.debug("[wechat_notice] appid/app_secret 未配置，跳过 access_token 获取")
        return ""

    cache_key = (appid, secret)
    cached = _access_token_cache.get(cache_key)
    now = time.time()
    # 提前 5 分钟刷新
    if cached and cached[1] > now + 300:
        return cached[0]

    url = f"{settings.wechat_api_base}/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": appid,
        "secret": secret,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[wechat_notice] 获取 access_token 失败: {exc}")
        return ""

    token = data.get("access_token")
    expires_in = data.get("expires_in", 7200)
    if not token:
        logger.warning(f"[wechat_notice] access_token 响应无 token: {data}")
        return ""

    _access_token_cache[cache_key] = (token, now + float(expires_in))
    return token


async def send_subscribe_message(
    openid: str,
    template_id: str,
    data: dict,
    page: Optional[str] = None,
    miniprogram_state: str = "formal",
) -> dict:
    """调用微信 subscribeMessage.send API 发送订阅消息

    Args:
        openid: 接收人 openid
        template_id: 模板ID
        data: 模板字段数据，如 {"thing1": {"value": "xxx"}, "time2": {"value": "2026-07-18"}}
        page: 点击跳转的小程序页面（须在 subscribe_pages.SUBSCRIBE_MESSAGE_PAGES 白名单内；
              非法路径会被丢弃，避免微信整条返回错误导致通知收不到）
        miniprogram_state: formal / trial / developer

    Returns:
        微信 API 响应 dict
    """
    if not openid or not template_id:
        logger.debug("[wechat_notice] openid 或 template_id 为空，跳过发送")
        return {"errcode": -1, "errmsg": "openid/template_id 为空"}

    access_token = await get_access_token()
    if not access_token:
        return {"errcode": -1, "errmsg": "access_token 获取失败"}

    url = f"{settings.wechat_api_base}/cgi-bin/message/subscribe/send"
    params = {"access_token": access_token}
    payload = {
        "touser": openid,
        "template_id": template_id,
        "data": data,
        "miniprogram_state": miniprogram_state,
    }
    safe_page = normalize_subscribe_page(page)
    if safe_page:
        payload["page"] = safe_page

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, params=params, json=payload)
            resp.raise_for_status()
            result = resp.json()
    except Exception as exc:
        logger.warning(f"[wechat_notice] 发送订阅消息失败: {exc}")
        return {"errcode": -1, "errmsg": str(exc)}

    if result.get("errcode", 0) != 0:
        logger.warning(f"[wechat_notice] 微信返回错误: {result}")
    return result


async def get_user_openid(db: AsyncSession, user_id: int) -> Optional[str]:
    """根据用户 ID 查询绑定的 openid"""
    result = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.user_id == user_id)
    )
    binding = result.scalar_one_or_none()
    return binding.openid if binding else None


async def send_to_user(
    db: AsyncSession,
    user_id: int,
    template_id: str,
    data: dict,
    page: Optional[str] = None,
) -> dict:
    """根据用户 ID 查 openid 后发送订阅消息

    若用户未绑定微信或未配置 template_id，返回跳过信息，不抛异常。
    """
    if not template_id:
        return {"errcode": -1, "errmsg": "template_id 未配置"}

    openid = await get_user_openid(db, user_id)
    if not openid:
        logger.debug(f"[wechat_notice] 用户 {user_id} 未绑定微信，跳过推送")
        return {"errcode": -1, "errmsg": "用户未绑定微信"}

    return await send_subscribe_message(openid, template_id, data, page)


async def is_user_subscribed(
    db: AsyncSession, user_id: int, template_id: str
) -> bool:
    """检查用户是否订阅了某模板消息"""
    result = await db.execute(
        select(WechatSubscription).where(
            WechatSubscription.user_id == user_id,
            WechatSubscription.template_id == template_id,
            WechatSubscription.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None
