"""规范版本管理模型 - 追踪规范文档的版本、生效/失效日期与状态"""
from datetime import datetime, date
from sqlalchemy import String, Integer, DateTime, Date, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class RegulationVersion(Base):
    __tablename__ = "regulation_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_name: Mapped[str] = mapped_column(String(256))
    filename: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(64), default="省统表")
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    file_path: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
