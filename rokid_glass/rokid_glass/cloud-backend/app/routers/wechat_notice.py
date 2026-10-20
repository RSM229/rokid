"""微信服务通知路由 - 管理员发送服务通知、用户订阅通知

作者: cloud-backend
创建日期: 2026-07-18
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import User
from app.models.wechat_subscription import WechatSubscription
from app.routers.auth import get_current_user, require_permission
from app.services.wechat_notice import (
    send_to_user,
    get_user_openid,
)

router = APIRouter()


class SendNoticeRequest(BaseModel):
    """管理员发送服务通知请求"""
    user_id: int
    template_id: str
    data: dict
    page: Optional[str] = None


class SubscribeRequest(BaseModel):
    """用户订阅通知请求"""
    template_id: str
    openid: Optional[str] = None  # 可选，未提供时从 WechatUserBinding 查询


@router.post("/notice/send")
async def send_notice(
    req: SendNoticeRequest,
    user: User = Depends(require_permission("settings")),
    db: AsyncSession = Depends(get_db),
):
    """发送微信服务通知（管理员调用）"""
    result = await send_to_user(db, req.user_id, req.template_id, req.data, req.page)
    if result.get("errcode", 0) != 0:
        raise HTTPException(
            status_code=500,
            detail=f"发送失败: {result.get('errmsg', '未知错误')}",
        )
    return {"detail": "通知已发送", "result": result}


@router.post("/notice/subscribe")
async def subscribe_notice(
    req: SubscribeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户订阅通知（记录订阅状态）

    前端在调用 wx.requestSubscribeMessage 获得用户授权后调用此接口记录订阅。
    """
    if not req.template_id:
        raise HTTPException(status_code=400, detail="template_id 不能为空")

    # openid 未提供时从绑定记录查询
    openid = req.openid
    if not openid:
        openid = await get_user_openid(db, user.id)
    if not openid:
        raise HTTPException(status_code=400, detail="用户未绑定微信账号，无法记录订阅")

    # 查询是否已存在订阅记录
    result = await db.execute(
        select(WechatSubscription).where(
            WechatSubscription.user_id == user.id,
            WechatSubscription.template_id == req.template_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.openid = openid
        existing.is_active = True
        existing.subscribed_at = datetime.now()
        existing.updated_at = datetime.now()
    else:
        sub = WechatSubscription(
            user_id=user.id,
            openid=openid,
            template_id=req.template_id,
            is_active=True,
            subscribed_at=datetime.now(),
        )
        db.add(sub)
    await db.commit()
    return {"detail": "订阅已记录"}


@router.get("/notice/templates")
async def list_templates(
    user: User = Depends(get_current_user),
):
    """获取当前配置的微信服务通知模板ID列表"""
    from app.config import settings
    return {"templates": settings.wechat_template_ids}


@router.get("/notice/status")
async def get_subscription_status(
    template_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户的订阅状态"""
    query = select(WechatSubscription).where(
        WechatSubscription.user_id == user.id,
        WechatSubscription.is_active.is_(True),
    )
    if template_id:
        query = query.where(WechatSubscription.template_id == template_id)
    result = await db.execute(query)
    items = result.scalars().all()
    openid = await get_user_openid(db, user.id)
    return {
        "openid_bound": bool(openid),
        "subscriptions": [
            {
                "template_id": s.template_id,
                "is_subscribed": s.is_active,
                "subscribed_at": s.subscribed_at.isoformat() if s.subscribed_at else None,
            }
            for s in items
        ],
    }
