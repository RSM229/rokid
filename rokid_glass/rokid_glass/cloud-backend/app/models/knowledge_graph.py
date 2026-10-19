"""知识图谱模型 — 实体（Entity）与关系（Relation）"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Text, ForeignKey, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base


class KGEntity(Base):
    """知识图谱实体节点"""
    __tablename__ = "kg_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True, comment="实体名称")
    entity_type: Mapped[str] = mapped_column(String(32), index=True, comment="实体类型")
    # 实体类型：规范条款/验收流程/施工工序/材料标准/隐患类型/工程部位/监理要点
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    properties: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    source_doc: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, comment="来源文档")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 关系
    out_relations: Mapped[list["KGRelation"]] = relationship(
        "KGRelation", foreign_keys="KGRelation.source_id", back_populates="source", cascade="all, delete-orphan"
    )
    in_relations: Mapped[list["KGRelation"]] = relationship(
        "KGRelation", foreign_keys="KGRelation.target_id", back_populates="target", cascade="all, delete-orphan"
    )


class KGRelation(Base):
    """知识图谱实体关系"""
    __tablename__ = "kg_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("kg_entities.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("kg_entities.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(32), index=True, comment="关系类型")
    # 关系类型：属于/前置/后续/关联/应用于/依据
    weight: Mapped[float] = mapped_column(default=1.0, comment="关系权重")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    source: Mapped["KGEntity"] = relationship("KGEntity", foreign_keys=[source_id], back_populates="out_relations")
    target: Mapped["KGEntity"] = relationship("KGEntity", foreign_keys=[target_id], back_populates="in_relations")
