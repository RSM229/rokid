"""视频记录模型 - 视频水印与业务关联

作者: cloud-backend
创建日期: 2026-07-18
"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class VideoRecord(Base):
    """视频记录：保存原始视频与水印视频路径，可关联业务对象"""

    __tablename__ = "video_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    original_path: Mapped[str] = mapped_column(String(512), comment="原始视频路径")
    watermarked_path: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="水印视频路径")
    duration: Mapped[float | None] = mapped_column(Float, nullable=True, comment="时长（秒）")
    location: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="位置")
    weather: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="天气")
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="拍摄时间")
    source: Mapped[str] = mapped_column(String(32), default="miniprogram", comment="来源：glasses/mobile/web/miniprogram")
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="文件 sha256")
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True, comment="业务类型")
    business_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True, comment="业务ID")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
