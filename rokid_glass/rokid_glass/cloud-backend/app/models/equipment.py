"""测量设备台账模型（P3.6 测量设备检定管理，穗建通上架路线图）。

登记监理用测量/试验设备的检定信息，calib_due（有效期至）加索引，
便于"临期检定提醒"查询；status ∈ 在用 / 停用 / 维修。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class MeasureDevice(Base):
    __tablename__ = "measure_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), default="")  # 设备名称
    model_no: Mapped[str] = mapped_column(String(64), default="")  # 型号
    serial_no: Mapped[str] = mapped_column(String(64), default="")  # 出厂编号
    unit: Mapped[str] = mapped_column(String(32), default="")  # 检定单位
    calib_date: Mapped[str] = mapped_column(String(10), default="")  # 检定日期 YYYY-MM-DD
    calib_due: Mapped[str] = mapped_column(String(10), default="", index=True)  # 有效期至 YYYY-MM-DD
    status: Mapped[str] = mapped_column(String(16), default="在用")  # 在用/停用/维修
    remark: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
