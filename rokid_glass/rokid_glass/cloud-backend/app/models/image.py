"""影像记录模型 — 统一管理三端上传的图片"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class ImageRecord(Base):
    __tablename__ = "image_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(32))  # glasses / mobile / web
    original_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    watermarked_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    section: Mapped[str | None] = mapped_column(String(128), nullable=True)
    weather: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hazard_id: Mapped[int | None] = mapped_column(ForeignKey("hazards.id"), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
