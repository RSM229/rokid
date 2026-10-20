"""工程签证模型（P3.5 现场签证管理，穗建通上架路线图）。

记录现场签证申请与审批流转，status 状态机：
  草稿 / 已驳回 → (submit) → 待审批 → (review) → 已批准 / 已驳回
仅 草稿/已驳回 状态允许修改与删除。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Visa(Base):
    __tablename__ = "visas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(256), default="工程签证")
    visa_no: Mapped[str] = mapped_column(String(64), default="")  # 签证编号
    apply_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    reason: Mapped[str] = mapped_column(Text, default="")  # 签证原因
    content: Mapped[str] = mapped_column(Text, default="")  # 签证内容
    amount: Mapped[float] = mapped_column(Float, default=0.0)  # 金额（元）
    status: Mapped[str] = mapped_column(String(16), default="草稿")  # 草稿/待审批/已批准/已驳回
    approver: Mapped[str] = mapped_column(String(128), default="")  # 审批人
    approve_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    approve_opinion: Mapped[str] = mapped_column(Text, default="")  # 审批意见
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
