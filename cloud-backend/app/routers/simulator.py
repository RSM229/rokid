"""眼镜端模拟器路由 — WebSocket 实时通信 + 指令下发 API

模拟器通过 WebSocket 连接后端，接收后端下发的指令。
同时提供 HTTP API 用于测试和调试。
"""
import json
import asyncio
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
import logging

from app.db import async_session, get_db
from app.models.user import User
from app.routers.auth import get_current_user, get_user_from_token, require_permission

router = APIRouter()
logger = logging.getLogger(__name__)

# 全局 WebSocket 连接管理
class SimulatorConnectionManager:
    """模拟器 WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.connection_info: dict[WebSocket, dict] = {}

    async def connect(self, websocket: WebSocket, already_accepted: bool = False):
        if not already_accepted:
            await websocket.accept()
        self.active_connections.append(websocket)
        self.connection_info[websocket] = {
            "connected_at": datetime.now(),
            "device_id": f"simulator_{len(self.active_connections)}",
        }
        logger.info(f"模拟器已连接，当前连接数: {len(self.active_connections)}")
        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": "模拟器已连接到后端",
            "timestamp": datetime.now().isoformat(),
        }, ensure_ascii=False))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if websocket in self.connection_info:
            del self.connection_info[websocket]
        logger.info(f"模拟器已断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """向所有连接的模拟器广播消息"""
        text = json.dumps(message, ensure_ascii=False)
        disconnected = []
        for ws in self.active_connections:
            try:
                await ws.send_text(text)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def send_command(self, command: dict):
        """下发指令到模拟器"""
        await self.broadcast({
            "type": "command",
            "data": command,
            "timestamp": datetime.now().isoformat(),
        })

    def get_connection_count(self) -> int:
        return len(self.active_connections)


simulator_manager = SimulatorConnectionManager()


# === WebSocket 端点 ===

@router.websocket("/ws/glasses-simulator")
async def glasses_simulator_ws(websocket: WebSocket):
    """眼镜端模拟器 WebSocket 端点

    连接后可接收以下类型的消息：
    - command: 后端下发的指令
    - status_update: 状态更新通知

    模拟器可上报：
    - key_event: 按键事件
    - photo_taken: 拍照完成
    - status: 状态上报
    """
    await websocket.accept()
    try:
        raw_auth = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        auth_message = json.loads(raw_auth)
        if auth_message.get("type") != "auth" or not auth_message.get("token"):
            raise HTTPException(status_code=401, detail="Missing WebSocket auth")
        async with async_session() as db:
            await get_user_from_token(auth_message["token"], db)
    except (HTTPException, asyncio.TimeoutError, json.JSONDecodeError):
        await websocket.close(code=4401, reason="Unauthorized")
        return
    await simulator_manager.connect(websocket, already_accepted=True)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "key_event":
                    logger.info(f"模拟器按键事件: {msg.get('key_code')}")
                    # 广播按键事件（前端可监听）
                    await simulator_manager.broadcast({
                        "type": "key_event_from_glasses",
                        "key_code": msg.get("key_code"),
                        "key_action": msg.get("key_action"),
                        "timestamp": datetime.now().isoformat(),
                    })

                elif msg_type == "photo_taken":
                    logger.info(f"模拟器拍照完成: {msg.get('filename', '')}")
                    await simulator_manager.broadcast({
                        "type": "photo_taken_from_glasses",
                        "filename": msg.get("filename"),
                        "image_data": msg.get("image_data"),
                        "timestamp": datetime.now().isoformat(),
                    })

                elif msg_type == "status":
                    logger.info(f"模拟器状态上报: {msg.get('status')}")
                    if websocket in simulator_manager.connection_info:
                        simulator_manager.connection_info[websocket]["status"] = msg.get("status")

                elif msg_type == "voice_result":
                    logger.info(f"语音识别结果: {msg.get('text')}")
                    await simulator_manager.broadcast({
                        "type": "voice_result_from_glasses",
                        "text": msg.get("text"),
                        "timestamp": datetime.now().isoformat(),
                    })

            except json.JSONDecodeError:
                logger.warning(f"模拟器消息解析失败: {data}")

    except WebSocketDisconnect:
        simulator_manager.disconnect(websocket)
        logger.info("模拟器 WebSocket 断开")


# === HTTP API ===

class SendCommandRequest(BaseModel):
    action: str  # speak / speak_urgent / show_hazard / show_guide / vibrate / start_inspection / stop_inspection / show_rectification / show_recheck / show_document / clear_display
    text: Optional[str] = None
    data: Optional[dict] = None


class PhotoRequest(BaseModel):
    width: int = 1920
    height: int = 1080
    quality: int = 90


@router.get("/simulator/status")
async def simulator_status(user: User = Depends(get_current_user)):
    """获取模拟器连接状态"""
    return {
        "connected": simulator_manager.get_connection_count() > 0,
        "connection_count": simulator_manager.get_connection_count(),
        "connections": [
            {
                "device_id": info.get("device_id"),
                "connected_at": info.get("connected_at").isoformat() if info.get("connected_at") else None,
                "status": info.get("status", "idle"),
            }
            for info in simulator_manager.connection_info.values()
        ],
    }


@router.post("/simulator/send-command")
async def send_command_to_simulator(
    req: SendCommandRequest,
    user: User = Depends(get_current_user),
):
    """向模拟器下发指令（模拟后端 → 眼镜端通信）

    支持的 action：
    - speak: 语音播报（需提供 text）
    - speak_urgent: 紧急播报+震动（需提供 text）
    - show_hazard: 展示隐患识别结果（需提供 data）
    - show_guide: 展示验收指引（需提供 data）
    - show_rectification: 展示整改通知单（需提供 data）
    - show_recheck: 展示复查结果（需提供 data）
    - show_document: 展示文书信息（需提供 data）
    - vibrate: 震动提示
    - start_inspection: 启动巡检
    - stop_inspection: 停止巡检
    - clear_display: 清屏
    """
    if simulator_manager.get_connection_count() == 0:
        raise HTTPException(status_code=503, detail="无模拟器连接，请先打开模拟器页面")

    command = {"action": req.action}
    if req.text:
        command["text"] = req.text
    if req.data:
        command["data"] = req.data

    await simulator_manager.send_command(command)

    return {
        "success": True,
        "command": command,
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/simulator/take-photo")
async def simulator_take_photo(
    req: PhotoRequest,
    user: User = Depends(get_current_user),
):
    """触发模拟器拍照"""
    if simulator_manager.get_connection_count() == 0:
        raise HTTPException(status_code=503, detail="无模拟器连接")

    await simulator_manager.send_command({
        "action": "take_photo",
        "width": req.width,
        "height": req.height,
        "quality": req.quality,
    })

    return {
        "success": True,
        "message": "拍照指令已发送到模拟器",
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/simulator/start-audio")
async def simulator_start_audio(user: User = Depends(get_current_user)):
    """启动模拟器音频流（模拟语音采集）"""
    if simulator_manager.get_connection_count() == 0:
        raise HTTPException(status_code=503, detail="无模拟器连接")

    await simulator_manager.send_command({"action": "start_audio"})
    return {"success": True, "message": "音频流已启动"}


@router.post("/simulator/stop-audio")
async def simulator_stop_audio(user: User = Depends(get_current_user)):
    """停止模拟器音频流"""
    if simulator_manager.get_connection_count() == 0:
        raise HTTPException(status_code=503, detail="无模拟器连接")

    await simulator_manager.send_command({"action": "stop_audio"})
    return {"success": True, "message": "音频流已停止"}


@router.post("/simulator/broadcast")
async def broadcast_to_simulator(
    req: SendCommandRequest,
    user: User = Depends(require_permission("settings")),
):
    """广播指令到模拟器（仅系统设置权限，用于受控测试）"""
    if simulator_manager.get_connection_count() == 0:
        raise HTTPException(status_code=503, detail="无模拟器连接")

    command = {"action": req.action}
    if req.text:
        command["text"] = req.text
    if req.data:
        command["data"] = req.data

    await simulator_manager.send_command(command)

    return {
        "success": True,
        "command": command,
        "timestamp": datetime.now().isoformat(),
    }
