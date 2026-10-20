"""安全审计 —— 越权明确拒绝并写入审计（对标保利 AI 监理通"每次请求的安全链"）。

当前只记录项目边界的两类事件：
- ``project_switch``        切换项目成功
- ``project_switch_denied`` 越权切换被拒绝（403）

后续如需覆盖更多敏感操作（文书审批、导出等），直接复用本表扩展 action 即可。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SecurityAudit(Base):
    __tablename__ = "security_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(32), index=True, comment="事件类型")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="事件详情")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="来源 IP")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, index=True
    )
