"""WebSocket 实时推送路由 - 多端实时同步

作者: cloud-backend
创建日期: 2026-07-18

实现：
- WS /api/ws/{user_id} - WebSocket 连接，JWT 鉴权（query 参数 token）
- 维护 Dict[int, Set[WebSocket]] 连接池
- 提供 broadcast_to_user / broadcast_to_project 工具函数
- 服务端主动 ping + 超时断开（心跳保活）
"""
import asyncio
import json
import logging
import time
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select

from app.db import async_session
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

# 心跳配置
PING_INTERVAL = 30  # 每 30 秒发送一次 ping
PING_TIMEOUT = 90   # 90 秒未收到任何消息则断开（允许 3 次漏检）


# 连接池：user_id -> Set[WebSocket]
_user_connections: Dict[int, Set[WebSocket]] = {}

# 项目连接映射：project_id -> Set[user_id]（用于 broadcast_to_project）
_project_users: Dict[int, Set[int]] = {}


def _register_connection(user_id: int, project_id: int | None, ws: WebSocket) -> None:
    """注册连接到连接池"""
    if user_id not in _user_connections:
        _user_connections[user_id] = set()
    _user_connections[user_id].add(ws)
    if project_id is not None:
        # 一个用户同一时刻只有一个当前项目（user.project_id 为单值上下文），
        # 故先把它从其它项目的映射里摘掉，避免切换项目后仍收到旧项目推送。
        for pid in list(_project_users):
            if pid == project_id:
                continue
            stale = _project_users.get(pid)
            if stale is not None:
                stale.discard(user_id)
                if not stale:
                    _project_users.pop(pid, None)
        _project_users.setdefault(project_id, set()).add(user_id)


def _unregister_connection(user_id: int, project_id: int | None, ws: WebSocket) -> None:
    """从连接池移除连接"""
    conns = _user_connections.get(user_id)
    if conns:
        conns.discard(ws)
        if not conns:
            _user_connections.pop(user_id, None)
    # 该用户已无任何在线连接 → 从所有项目映射中摘除。
    # 原实现一律保留（注释称"可能还有其他连接"），但"是否还有连接"由
    # _user_connections 可直接判定：不摘除会让已下线/退出项目的人继续收到
    # broadcast_to_project 推送（跨项目信息泄露），且 _project_users 只增不减。
    if user_id not in _user_connections:
        for pid in list(_project_users):
            users = _project_users.get(pid)
            if users is None:
                continue
            users.discard(user_id)
            if not users:
                _project_users.pop(pid, None)


async def _send_ws(ws: WebSocket, message: dict) -> bool:
    """安全发送 WebSocket 消息，返回是否成功"""
    try:
        await ws.send_text(json.dumps(message, ensure_ascii=False))
        return True
    except Exception as exc:
        logger.debug(f"WebSocket 发送失败：{exc}")
        return False


async def broadcast_to_user(user_id: int, message: dict) -> int:
    """向指定用户的所有 WebSocket 连接推送消息

    Returns:
        成功推送的连接数
    """
    conns = _user_connections.get(user_id)
    if not conns:
        return 0
    success = 0
    dead: list[WebSocket] = []
    for ws in list(conns):
        if await _send_ws(ws, message):
            success += 1
        else:
            dead.append(ws)
    for ws in dead:
        conns.discard(ws)
    return success


async def broadcast_to_project(project_id: int, event_type: str, data: dict) -> int:
    """向项目内所有在线用户推送事件

    Args:
        project_id: 项目ID
        event_type: 事件类型
        data: 事件数据

    Returns:
        成功推送的连接数
    """
    user_ids = _project_users.get(project_id, set())
    if not user_ids:
        # 主动查询项目内所有用户并尝试推送（覆盖尚未建立 project_users 映射的场景）
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(User.id).where(User.project_id == project_id, User.is_active.is_(True))
                )
                user_ids = set(result.scalars().all())
        except Exception as exc:
            logger.warning(f"查询项目用户失败：{exc}")
            return 0

    message = {
        "event": event_type,
        "project_id": project_id,
        "data": data,
    }
    success = 0
    for uid in list(user_ids):
        success += await broadcast_to_user(uid, message)
    return success


@router.websocket("/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: int,
    token: str = Query(...),
):
    """WebSocket 连接，JWT 鉴权（query 参数 token）

    连接 URL: /api/ws/{user_id}?token=<JWT>

    鉴权流程：
    1. 从 query 参数读取 token
    2. 解码 JWT 并校验 sub（用户ID）与连接的 user_id 一致
    3. 接受连接并注册到连接池
    """
    # JWT 鉴权
    from app.routers.auth import decode_access_token, get_user_from_token
    try:
        async with async_session() as db:
            user = await get_user_from_token(token, db)
    except Exception as exc:
        logger.info(f"WebSocket 鉴权失败：{exc}")
        await websocket.close(code=4401)
        return

    # 校验连接的 user_id 与 token 中的用户一致
    if user.id != user_id:
        logger.info(f"WebSocket user_id 不匹配：token={user.id}, path={user_id}")
        await websocket.close(code=4403)
        return

    await websocket.accept()
    _register_connection(user.id, user.project_id, websocket)
    logger.info(f"WebSocket 已连接：user#{user.id}, project={user.project_id}")

    # 发送欢迎消息
    await _send_ws(websocket, {
        "event": "connected",
        "data": {"user_id": user.id, "message": "WebSocket 连接已建立"},
    })

    # 心跳保活：服务端定时 ping + 超时断开
    async def _heartbeat_loop():
        """定时发送 ping，检测客户端存活"""
        while True:
            await asyncio.sleep(PING_INTERVAL)
            try:
                await websocket.send_json({"event": "ping", "data": {"ts": time.time()}})
            except Exception:
                break

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    last_msg_time = time.time()

    try:
        while True:
            # 设置超时：超过 PING_TIMEOUT 未收到任何消息则断开
            text = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=PING_TIMEOUT,
            )
            last_msg_time = time.time()
            # 处理客户端消息（心跳回包或业务消息）
            try:
                msg = json.loads(text)
                if msg.get("type") == "ping":
                    await _send_ws(websocket, {"event": "pong", "data": {}})
                elif msg.get("type") == "pong":
                    # 客户端回复了服务端 ping，确认存活
                    pass
            except (json.JSONDecodeError, TypeError):
                pass
    except asyncio.TimeoutError:
        logger.info(f"WebSocket 心跳超时断开：user#{user.id}（{PING_TIMEOUT}s 无消息）")
        try:
            await websocket.close(code=4408, reason="heartbeat timeout")
        except Exception:
            pass
    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开：user#{user.id}")
    except Exception as exc:
        logger.info(f"WebSocket 异常断开：user#{user.id}, {exc}")
    finally:
        heartbeat_task.cancel()
        _unregister_connection(user.id, user.project_id, websocket)
