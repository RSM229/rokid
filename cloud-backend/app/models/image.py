"""影像记录模型 — 统一管理三端上传的图片。

2026-11-09 起承载《工程影像资料分类标准》的归类结果（8 类互斥 + X 二级类）。
归类逻辑在 app/services/image_classification.py，本模型只存结论，不判类。
归档遵循单一主体原则：一张照片只进一个一级类目录，其余属性用标签表达。
"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class ImageRecord(Base):
    __tablename__ = "image_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(32))  # glasses / mobile / web
    original_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    watermarked_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    section: Mapped[str | None] = mapped_column(String(128), nullable=True)
    weather: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hazard_id: Mapped[int | None] = mapped_column(ForeignKey("hazards.id"), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    # —— 工程影像资料分类（2026-11-09）——
    # 全部 nullable：历史影像未归类时为 NULL，本地 SQLite 由 _reconcile_missing_columns 自动补列。
    category_code: Mapped[str | None] = mapped_column(String(8), nullable=True)      # P/W/Y/M/X/H/C/O
    subcategory: Mapped[str | None] = mapped_column(String(8), nullable=True)       # X1~X5
    labels: Mapped[str | None] = mapped_column(String(255), nullable=True)          # 顿号分隔，不参与目录归属
    archive_dir: Mapped[str | None] = mapped_column(String(64), nullable=True)      # 01_旁站 ... 08_其他
    archive_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 按命名规范重命名
    related_doc_no: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 关联书面资料编号
    classification_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # pending/confirmed/needs_review
    classification_note: Mapped[str | None] = mapped_column(Text, nullable=True)    # 归类理由 / 待确认说明
