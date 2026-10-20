"""巡检会话模型 - 云端 AI 巡检闭环

作者: cloud-backend
创建日期: 2026-07-18
"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base
import enum


class InspectionStatus(str, enum.Enum):
    """巡检会话状态"""
    ACTIVE = "active"      # 进行中
    STOPPED = "stopped"    # 已停止


class InspectionSession(Base):
    """云端巡检会话：记录一次巡检任务的采集与识别统计"""

    __tablename__ = "inspection_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True, comment="项目ID")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="巡检人ID")
    status: Mapped[str] = mapped_column(
        String(16), default=InspectionStatus.ACTIVE.value, index=True, comment="active/stopped"
    )
    frequency_seconds: Mapped[int] = mapped_column(Integer, default=5, comment="采集频率（秒）")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="开始时间")
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="停止时间")
    total_captures: Mapped[int] = mapped_column(Integer, default=0, comment="累计采集数")
    total_hazards: Mapped[int] = mapped_column(Integer, default=0, comment="累计命中隐患数")
    last_capture_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近采集时间")
    last_hazard_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近命中隐患时间")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
