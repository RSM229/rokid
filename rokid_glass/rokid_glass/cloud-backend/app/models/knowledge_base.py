"""个人/共享知识库模型 — 多知识库 + 成员权限 + 文档。

对标 ima 的「个人知识库 / 共享知识库」：
- 一个用户可以创建多个知识库；
- 共享知识库通过成员角色（owner/admin/editor/viewer）协作；
- 文档归属到具体知识库，问答与检索按库隔离。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base

# 知识库成员角色（权限由高到低）
KB_ROLES = ("owner", "admin", "editor", "viewer")
KB_ROLE_LEVEL = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # personal=个人知识库；shared=共享知识库
    kb_type: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    # 共享知识库的加入邀请码（可轮换）
    invite_code: Mapped[Optional[str]] = mapped_column(
        String(32), unique=True, nullable=True, index=True
    )
    # 系统内置知识库（如“规范知识库”），所有用户可见，文档由系统维护
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), index=True
    )
    # 是否发布到知识库广场（公开可发现、可订阅加入）
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeBaseMember(Base):
    __tablename__ = "knowledge_base_members"
    __table_args__ = (UniqueConstraint("kb_id", "user_id", name="uq_kb_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    filename: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
