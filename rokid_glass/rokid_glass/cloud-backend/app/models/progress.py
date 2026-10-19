"""进度记录模型（P3.4 施工进度记录，穗建通上架路线图）。

按日记录各施工部位的计划/实际完成百分比，供进度跟踪与看板展示。
record_date 为字符串 YYYY-MM-DD（与 meeting 一致，便于直接比较与排序）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class ProgressRecord(Base):
    __tablename__ = "progress_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    record_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    part: Mapped[str] = mapped_column(String(256), default="")  # 施工部位
    plan_percent: Mapped[int] = mapped_column(Integer, default=0)  # 计划完成 %
    actual_percent: Mapped[int] = mapped_column(Integer, default=0)  # 实际完成 %
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
