"""规范向量块模型 — 知识库语义检索"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class RegulationChunk(Base):
    __tablename__ = "regulation_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
