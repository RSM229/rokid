"""微信小程序身份、统一媒体与音视频会话模型。"""
from datetime import datetime
import enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class WechatUserBinding(Base):
    __tablename__ = "wechat_user_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    openid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    unionid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # 微信平台不向小程序提供真实微信号（wxid）；这里存的是**用户在绑定/完善资料
    # 时确认的微信昵称与头像**（官方「头像昵称填写能力」采集），供管理端识别
    # 绑定者身份。可空 —— 存量绑定在用户下次完善前为 NULL。
    wx_nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wx_avatar_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    user: Mapped["User"] = relationship()


class WechatLoginCodeUse(Base):
    """只保存一次性 code 的哈希，阻止本服务内重放。"""

    __tablename__ = "wechat_login_code_uses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class WechatBindingAudit(Base):
    __tablename__ = "wechat_binding_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    openid_hash: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MediaType(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class MediaRecord(Base):
    __tablename__ = "media_records"
    __table_args__ = (
        UniqueConstraint("uploader_id", "idempotency_key", name="uq_media_uploader_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media_type: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(32), default="miniprogram")
    original_path: Mapped[str] = mapped_column(String(512))
    processed_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    business_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    section: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    weather: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
