"""派单模型 — 总监/总代派单给工程师/监理员"""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base
import enum


class TaskStatus(str, enum.Enum):
    """派单状态"""
    PENDING = "待接单"          # 已派发，等待接收人确认
    ACCEPTED = "已接单"         # 接收人接受任务
    REJECTED = "已拒单"         # 接收人拒绝任务
    IN_PROGRESS = "进行中"      # 任务执行中
    COMPLETED = "已完成"        # 任务完成
    CANCELLED = "已取消"        # 发起人取消


class TaskPriority(str, enum.Enum):
    """优先级"""
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    URGENT = "紧急"


class TaskCategory(str, enum.Enum):
    """派单类别（用户后续可扩展）"""
    INSPECTION = "巡视检查"
    ACCEPTANCE = "验收管理"
    WITNESS = "见证取样"
    STATION = "旁站监督"
    HAZARD = "隐患处理"
    DOCUMENT = "文书处理"
    RECTIFY = "整改跟踪"
    OTHER = "其他事务"


class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), comment="任务标题")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="任务描述/内容")
    status: Mapped[str] = mapped_column(
        String(16), default=TaskStatus.PENDING.value,
        comment="待接单/已接单/已拒单/进行中/已完成/已取消"
    )
    priority: Mapped[str] = mapped_column(String(8), default=TaskPriority.MEDIUM.value, comment="优先级")
    category: Mapped[str] = mapped_column(
        String(32), default=TaskCategory.OTHER.value, comment="派单类别"
    )

    # 人员关系
    assigner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), comment="派单人ID（总监/总代）")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, comment="被派人ID（工程师/监理员）")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)

    # 关联工作记录
    work_record_id: Mapped[int | None] = mapped_column(ForeignKey("work_records.id"), nullable=True, comment="关联的工作记录")

    # 时间节点
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="截止时间")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="接单时间")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="开始时间")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="完成时间")
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True, comment="拒单原因")
    completion_remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="完成备注/回执")

    # 位置信息
    location: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="任务地点")
    section: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="施工区域")

    # 附件与多媒体（JSON 数组，存 OSS / MinIO URL）
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True, comment="附件URL列表（JSON）")

    # 提醒与通知
    notify_glasses: Mapped[bool] = mapped_column(default=True, comment="是否同步推送到眼镜端")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    assigner: Mapped["User"] = relationship(foreign_keys=[assigner_id])
    assignee: Mapped["User | None"] = relationship(foreign_keys=[assignee_id])
