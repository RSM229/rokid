"""Stable one-to-one mapping between a project and its archive knowledge base."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProjectArchiveLibrary(Base):
    __tablename__ = "project_archive_libraries"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_project_archive_project"),
        UniqueConstraint("kb_id", name="uq_project_archive_kb"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
