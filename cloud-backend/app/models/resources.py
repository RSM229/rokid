"""资源投入日报模型（P3.3 人机料投入记录，穗建通上架路线图）。

按日记录现场人工、机械、材料投入情况，machines/materials 以 JSON 存储：
  machines  = [{name, count}]
  materials = [{name, spec, quantity, unit}]
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class ResourceInput(Base):
    __tablename__ = "resource_inputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    record_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    labor_count: Mapped[int] = mapped_column(Integer, default=0)  # 人工数
    labor_detail: Mapped[str] = mapped_column(Text, default="")  # 工种说明
    machines: Mapped[str] = mapped_column(Text, default="[]")  # JSON [{name, count}]
    materials: Mapped[str] = mapped_column(Text, default="[]")  # JSON [{name, spec, quantity, unit}]
    remark: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
