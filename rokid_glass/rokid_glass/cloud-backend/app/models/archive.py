"""台账归档模型 - 批量台账归档记录

作者: cloud-backend
创建日期: 2026-07-18

P3.7 追加：ArchiveRecord（资料收发记录）、IssueTicket（问题发单）。
"""
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Integer, DateTime, Date, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db import Base


class Archive(Base):
    """台账归档记录：批量导出多份台账后打包归档"""

    __tablename__ = "ledger_archives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True, comment="项目ID")
    name: Mapped[str] = mapped_column(String(128), comment="归档名称")
    file_path: Mapped[str] = mapped_column(String(512), comment="归档包相对路径")
    ledger_types_json: Mapped[list] = mapped_column(JSON, comment="归档的台账类型列表")
    date_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="起始日期")
    date_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="截止日期")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="创建人ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ArchiveRecord(Base):
    """资料收发记录（P3.7）：direction ∈ 收 / 发"""

    __tablename__ = "archive_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    direction: Mapped[str] = mapped_column(String(4), default="收")  # 收 / 发
    doc_name: Mapped[str] = mapped_column(String(256), default="")  # 资料名称
    doc_no: Mapped[str] = mapped_column(String(64), default="")  # 编号
    unit: Mapped[str] = mapped_column(String(128), default="")  # 对方单位
    trans_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    handler: Mapped[str] = mapped_column(String(64), default="")  # 经办人
    remark: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class IssueTicket(Base):
    """问题发单（P3.7）：issue_type ∈ 质量/安全/进度/其他；status ∈ 待处理/处理中/已关闭"""

    __tablename__ = "issue_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(256), default="")
    issue_type: Mapped[str] = mapped_column(String(16), default="质量")  # 质量/安全/进度/其他
    part: Mapped[str] = mapped_column(String(256), default="")  # 部位
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="待处理")  # 待处理/处理中/已关闭
    deadline: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
