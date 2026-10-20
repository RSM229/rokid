"""工作记录模型 — 巡视/验收/见证/旁站"""
from datetime import datetime, date
from sqlalchemy import Date, String, Integer, DateTime, ForeignKey, Text, JSON, Boolean, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base
import enum


class WorkRecordType(str, enum.Enum):
    """工作记录类型"""
    INSPECTION = "巡视"
    ACCEPTANCE = "验收"
    WITNESS = "见证"
    STATION = "旁站"


class WorkRecordStatus(str, enum.Enum):
    """工作记录状态"""
    DRAFT = "草稿"
    IN_PROGRESS = "进行中"
    SUBMITTED = "已提交"
    APPROVED = "已审批"
    REJECTED = "已驳回"
    ARCHIVED = "已归档"


class WorkRecord(Base):
    __tablename__ = "work_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_type: Mapped[str] = mapped_column(
        String(16), comment="巡视/验收/见证/旁站"
    )
    status: Mapped[str] = mapped_column(
        String(16), default=WorkRecordStatus.DRAFT.value, comment="草稿/进行中/已提交/已审批/已驳回/已归档"
    )

    # 基本信息
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    section: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="施工区域")
    location: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="具体位置")
    work_content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="施工内容")
    weather: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="天气")

    record_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="现场业务日期")
    worker_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="巡视施工人数")
    inspection_start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inspection_end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    station_process: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="旁站关键工序")
    acceptance_participants: Mapped[str | None] = mapped_column(Text, nullable=True, comment="实际参验单位及人员")
    # 巡视专用
    inspection_photos: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="巡视照片列表（含水印路径）")
    inspection_items: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="巡检环节及照片对应关系")
    inspection_remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="巡视备注")
    problems: Mapped[str] = mapped_column(Text, default="", nullable=True, comment="发现的问题（GD-B-214/212）")
    treatment: Mapped[str] = mapped_column(Text, default="", nullable=True, comment="处理意见及措施（GD-B-214/212/213）")

    # 验收专用
    acceptance_guide_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="引用的验收指引关键词")
    acceptance_result: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="验收结果：合格/不合格/待整改")
    acceptance_basis: Mapped[str] = mapped_column(Text, default="", nullable=True, comment="检验依据（GD-B-213）")
    generated_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True, comment="生成的省统表文书ID")

    # 见证专用
    witness_material_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="材料名称")
    witness_material_spec: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="规格型号")
    witness_quantity: Mapped[str] = mapped_column(String(32), default="", nullable=True, comment="取样数量（GD-B-219）")
    witness_unit: Mapped[str] = mapped_column(String(16), default="", nullable=True, comment="计量单位（GD-B-219）")
    witness_cert_no: Mapped[str] = mapped_column(String(64), default="", nullable=True, comment="合格证/检测报告编号（GD-B-219）")
    witness_accept_result: Mapped[str] = mapped_column(String(16), default="", nullable=True, comment="进场验收结论（GD-B-219）")
    witness_photos: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="见证拍照列表")
    witness_report_path: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="见证报告文件路径")

    # 旁站专用
    station_project_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="旁站工程名称")
    station_location: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="旁站部位")
    station_start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="开始时间")
    station_end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="结束时间")
    station_content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="旁站情况描述")
    station_supervision_status: Mapped[str] = mapped_column(Text, default="", nullable=True, comment="旁站监理情况（GD-B-212）")
    station_conclusion: Mapped[str | None] = mapped_column(Text, nullable=True, comment="旁站结论")

    # 通用
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="扩展字段，存储完整表单数据")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
    viewed: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已查看")

    # 审批流（P1-3）
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="驳回原因")
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="审批人ID")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="审批时间")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    creator: Mapped["User"] = relationship(foreign_keys=[creator_id])
