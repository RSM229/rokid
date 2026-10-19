"""通知记录模型 - 持久化用户通知"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    event_type: Mapped[str] = mapped_column(String(64), default="notification")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[float] = mapped_column(Float, default=datetime.now().timestamp)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
