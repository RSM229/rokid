"""电子签章模型 - 监理文书 PDF 电子签名嵌入

作者: cloud-backend
创建日期: 2026-07-18
"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class Signature(Base):
    """电子签章：保存用户的签章图片与密码哈希，用于文书 PDF 签署"""

    __tablename__ = "signatures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, comment="签章归属用户")
    name: Mapped[str] = mapped_column(String(64), comment="签章名称（如：总监章/个人章）")
    role: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="签章角色（总监/专监等）")
    image_path: Mapped[str] = mapped_column(String(256), comment="签章图片相对路径（/uploads/...）")
    password_hash: Mapped[str] = mapped_column(String(256), comment="签章使用密码哈希")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
