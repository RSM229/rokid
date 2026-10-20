"""监理文书模型"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base
import enum


class DocType(str, enum.Enum):
    """文书类型"""
    SIDE_STATION = "旁站记录"
    SUPERVISION_LOG = "监理日志"
    NOTICE_QUALITY = "监理工程师通知单（质量类）"
    RECTIFICATION = "安全隐患整改通知单"
    RECTIFY_REPLY = "安全隐患整改回复单"
    INSPECTION = "巡视记录"
    ACCEPTANCE = "验收记录"
    WITNESS_SAMPLING = "见证取样记录"
    WEEKLY = "监理周报"
    MONTHLY = "监理月报"


class DocStatus(str, enum.Enum):
    DRAFT = "草稿"
    PENDING_APPROVAL = "待审批"
    APPROVED = "已审批"
    ARCHIVED = "已归档"
    REJECTED = "已驳回"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_type: Mapped[str] = mapped_column(String(64))
    doc_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32), default=DocStatus.DRAFT.value)
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    attached_images: Mapped[list] = mapped_column(JSON, default=list)
    # 省统表模板标识（form_registry.form_code），记录/汇总生成的文书据此渲染
    form_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="省统表模板标识")
    # Word 模板渲染产物路径（/exports/xxx.docx）；pdf_path 兼容历史 PDF 产物
    docx_path: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="docx 模板渲染路径")
    pdf_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # 电子签章相关字段
    signed_pdf_path: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="已签章 PDF 路径")
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="签署时间")
    signed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="签署人ID")
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    weather: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
