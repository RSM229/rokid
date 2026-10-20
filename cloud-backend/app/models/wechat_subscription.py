"""微信小程序订阅通知模型 - 记录用户订阅状态

作者: cloud-backend
创建日期: 2026-07-18
"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class WechatSubscription(Base):
    """微信小程序订阅状态：记录某用户对某模板消息的订阅"""

    __tablename__ = "wechat_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "template_id", name="uq_wechat_subscription_user_template"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="用户ID")
    openid: Mapped[str] = mapped_column(String(128), index=True, comment="微信 openid")
    template_id: Mapped[str] = mapped_column(String(128), index=True, comment="模板消息ID")
    subscribed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="订阅时间")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否有效")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
