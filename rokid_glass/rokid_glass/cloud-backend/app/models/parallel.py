"""平行检验模型（P3.1 平行检验记录，穗建通上架路线图）。

对应省统表 GD-B-213（平行检验记录），监理对承包单位自检合格的工序
独立进行检验并给出结论，conclusion ∈ 合格 / 不合格。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class ParallelInspection(Base):
    __tablename__ = "parallel_inspections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    inspect_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    part: Mapped[str] = mapped_column(String(256), default="")  # 检验部位
    item: Mapped[str] = mapped_column(String(256), default="")  # 检验项目
    method: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    basis: Mapped[str] = mapped_column(Text, default="")  # 检验依据
    result_detail: Mapped[str] = mapped_column(Text, default="")  # 检验结果描述
    conclusion: Mapped[str] = mapped_column(String(8), default="合格")  # 合格 / 不合格
    treatment: Mapped[str] = mapped_column(Text, default="")  # 处理意见
    form_code: Mapped[str] = mapped_column(String(64), default="gd_b_213_pingxing")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
