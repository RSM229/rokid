"""推送服务 - WebSocket 实时推送 + 数据库持久化 + 微信订阅消息"""
import json
import logging
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# 内存队列保留作为快速缓存（在线用户即时轮询），同时持久化到数据库
_user_queues: dict[int, list[dict]] = defaultdict(list)


async def _persist_notification(
    user_id: int,
    title: str,
    body: str,
    event_type: str,
    payload: dict[str, Any],
    ts: float,
):
    """将通知持久化到数据库"""
    try:
        from app.db import async_session
        from app.models.notification import Notification
        async with async_session() as db:
            record = Notification(
                user_id=user_id,
                title=title,
                body=body,
                event_type=event_type,
                payload=json.dumps(payload, ensure_ascii=False),
                ts=ts,
            )
            db.add(record)
            await db.commit()
    except Exception as exc:
        logger.debug(f"[notification] 持久化失败（不影响推送）：{exc}")


async def push_to_glasses(user_id: int, title: str, body: str, payload: dict[str, Any] | None = None) -> bool:
    """推送通知到指定用户的眼镜端 / Web 后台

    同时写入内存队列、数据库和 WebSocket。
    """
    ts = time.time()
    msg = {"title": title, "body": body, "payload": payload or {}, "ts": ts}

    # 内存队列（快速缓存，保留最近 50 条）
    _user_queues[user_id].append(msg)
    _user_queues[user_id] = _user_queues[user_id][-50:]

    # 数据库持久化
    await _persist_notification(user_id, title, body, "notification", payload or {}, ts)

    logger.info(f"[notification] -> user#{user_id}: {title}")

    # WebSocket 实时推送
    try:
        from app.routers.ws import broadcast_to_user
        await broadcast_to_user(user_id, {
            "event": "notification",
            "data": {"title": title, "body": body, "payload": payload or {}, "ts": ts},
        })
    except Exception as exc:
        logger.debug(f"[notification] WebSocket 推送失败（不影响队列）：{exc}")

    return True


async def push_realtime(user_id: int, event_type: str, data: dict[str, Any]) -> bool:
    """实时推送：同时写入内存队列、数据库与 WebSocket 推送

    保证：
    1. 离线用户可通过轮询队列/数据库拉取历史通知
    2. 在线 WebSocket 连接即时收到事件
    """
    ts = time.time()
    msg = {"title": event_type, "body": "", "payload": {"event": event_type, "data": data}, "ts": ts}

    _user_queues[user_id].append(msg)
    _user_queues[user_id] = _user_queues[user_id][-50:]

    # 数据库持久化
    await _persist_notification(user_id, event_type, "", event_type, data, ts)

    logger.info(f"[notification realtime] -> user#{user_id}: {event_type}")

    try:
        from app.routers.ws import broadcast_to_user
        await broadcast_to_user(user_id, {"event": event_type, "data": data, "ts": ts})
    except Exception as exc:
        logger.debug(f"[notification realtime] WebSocket 推送失败：{exc}")

    return True


def get_user_notifications(user_id: int, since_ts: float | None = None) -> list[dict]:
    """获取用户通知（内存队列快速轮询方式）"""
    msgs = _user_queues.get(user_id, [])
    if since_ts is not None:
        return [m for m in msgs if m["ts"] > since_ts]
    return list(msgs)


async def get_user_notifications_db(user_id: int, since_ts: float | None = None, limit: int = 50) -> list[dict]:
    """从数据库获取用户通知（支持离线消息补偿）"""
    try:
        from sqlalchemy import select
        from app.db import async_session
        from app.models.notification import Notification

        stmt = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.ts.desc())
            .limit(limit)
        )
        if since_ts is not None:
            stmt = stmt.where(Notification.ts > since_ts)

        async with async_session() as db:
            result = await db.execute(stmt)
            records = result.scalars().all()

        return [
            {
                "title": r.title,
                "body": r.body,
                "payload": json.loads(r.payload) if r.payload else {},
                "ts": r.ts,
                "is_read": r.is_read,
            }
            for r in reversed(records)
        ]
    except Exception as exc:
        logger.debug(f"[notification] 从数据库获取通知失败：{exc}")
        return get_user_notifications(user_id, since_ts)


def clear_user_notifications(user_id: int) -> int:
    """清空用户内存通知队列"""
    n = len(_user_queues.get(user_id, []))
    _user_queues[user_id] = []
    return n


async def clear_user_notifications_db(user_id: int) -> int:
    """清空用户通知（数据库标记已读）"""
    try:
        from sqlalchemy import update
        from app.db import async_session
        from app.models.notification import Notification

        async with async_session() as db:
            result = await db.execute(
                update(Notification)
                .where(Notification.user_id == user_id, Notification.is_read.is_(False))
                .values(is_read=True)
            )
            await db.commit()
            return result.rowcount
    except Exception as exc:
        logger.debug(f"[notification] 标记已读失败：{exc}")
        return 0


async def push_to_wechat(
    db,
    user_id: int,
    template_key: str,
    data: dict,
    page: str | None = None,
) -> bool:
    """推送微信服务通知到指定用户"""
    try:
        from app.config import settings
        from app.services.wechat_notice import send_to_user, is_user_subscribed

        template_id = settings.wechat_template_ids.get(template_key, "")
        if not template_id:
            return False

        subscribed = await is_user_subscribed(db, user_id, template_id)
        if not subscribed:
            logger.debug(f"[notification wechat] user#{user_id} 未订阅 {template_key}，跳过")
            return False

        result = await send_to_user(db, user_id, template_id, data, page)
        if result.get("errcode", 0) == 0:
            logger.info(f"[notification wechat] -> user#{user_id}: {template_key}")
            return True
        logger.debug(f"[notification wechat] 发送失败 user#{user_id}: {result}")
        return False
    except Exception as exc:
        logger.debug(f"[notification wechat] 推送异常 user#{user_id}: {exc}")
        return False
