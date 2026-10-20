"""验收流程模板模型 - PC 端维护验收流程模板，替代内置硬编码

作者: cloud-backend
创建日期: 2026-07-18
"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base
import enum


class AcceptanceType(str, enum.Enum):
    """验收类型枚举"""
    REBAR = "钢筋"
    CONCRETE = "混凝土"
    FORMWORK = "模板"
    SCAFFOLD = "脚手架"
    DEEP_PIT = "深基坑"
    WATERPROOF = "防水"
    ELECTRICAL = "电气"
    HELMET = "安全帽"
    EDGE_OPENING = "临边洞口"
    TEMP_POWER = "临时用电"
    OTHER = "其他"


class AcceptanceTemplate(Base):
    """验收流程模板：存储某验收类型的标准流程步骤、要点、常见违规与规范依据"""

    __tablename__ = "acceptance_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    acceptance_type: Mapped[str] = mapped_column(
        String(32), index=True, comment="钢筋/混凝土/模板/脚手架/深基坑/防水/电气/安全帽/临边洞口/临时用电/其他"
    )
    title: Mapped[str] = mapped_column(String(128), comment="模板标题")
    steps_json: Mapped[list] = mapped_column(JSON, comment="流程步骤列表")
    key_points_json: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="验收要点列表")
    common_violations_json: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="常见违规列表")
    regulation_references_json: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="规范依据列表")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, comment="是否启用")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="创建人ID")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
