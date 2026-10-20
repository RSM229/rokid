"""整改记录模型 — 隐患整改闭环跟踪"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class RectificationRecord(Base):
    __tablename__ = "rectifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hazard_id: Mapped[int] = mapped_column(ForeignKey("hazards.id"))
    notice_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rectified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rectification_image_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    recheck_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recheck_remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    recheck_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
