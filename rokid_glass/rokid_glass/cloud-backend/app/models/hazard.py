"""隐患识别模型"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base
import enum


class HazardStatus(str, enum.Enum):
    """隐患整改状态"""
    REPORTED = "已提报"
    NOTIFIED = "已通知整改"
    RECTIFYING = "整改中"
    RECHECK_PENDING = "待复查"
    CLOSED = "已闭环"
    REJECTED = "驳回整改"


class Hazard(Base):
    __tablename__ = "hazards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32), default=HazardStatus.REPORTED.value)

    # AI识别结果
    risk_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    top1_risk: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    uncertainty: Mapped[str | None] = mapped_column(Text, nullable=True)
    recheck_suggestion: Mapped[list] = mapped_column(JSON, default=list)
    scene_related: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 关联影像
    image_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    rectification_image_path: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 自动匹配的规范条款（JSON 字符串）
    matched_regulations_json: Mapped[str | None] = mapped_column(Text, nullable=True, comment="自动匹配的规范条款（JSON）")

    # 关联文书
    rectification_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)

    # P2.4 三表联动：监理通知单 / 整改通知 / 整改回复 关联文书
    # （rectify_doc_id 与历史 rectification_doc_id 并存，历史记录自动复用旧列）
    notify_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True, comment="监理通知单文书ID")
    rectify_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True, comment="安全隐患整改通知文书ID")
    reply_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True, comment="安全隐患整改回复单文书ID")

    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    section: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
