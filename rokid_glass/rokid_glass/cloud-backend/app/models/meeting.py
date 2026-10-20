"""会议纪要模型（P3.2 工地例会纪要 + 议题跟踪，穗建通上架路线图）。

对应省统表 GD-B-217（会议纪要），topics 以 JSON 存储议题/待办列表：
  [{topic, conclusion, owner, deadline, status}]
  status ∈ 待办 / 进行中 / 已完成（议题跟踪用）
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class MeetingMinute(Base):
    __tablename__ = "meeting_minutes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(256), default="工地例会纪要")
    meeting_type: Mapped[str] = mapped_column(String(32), default="例会")
    meeting_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    location: Mapped[str] = mapped_column(String(256), default="")
    host: Mapped[str] = mapped_column(String(128), default="")
    attendees: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual / ai
    form_code: Mapped[str] = mapped_column(String(64), default="gd_b_217_huiyi")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
