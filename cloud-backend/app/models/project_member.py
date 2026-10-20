"""项目成员关系 —— 权限边界的根基。

对标保利「AI 监理通」的角色权限模型（单一企业版）：
- 超级管理员（admin）＝全平台授权项目范围；
- 总监理工程师 / 总监代表 / 专业监理工程师 / 监理员＝仅本人加入的项目。

``users.project_id`` 只表示"当前上下文"，真正的归属关系在这里：
切换项目（``POST /project/{id}/switch``）必须先过成员校验，
项目列表（``GET /project/list``）只返回成员项目。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), index=True, comment="项目"
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="成员用户"
    )
    # Step 2 起语义升级：`role` 不再是"入会快照"，而是**本项目的授权真源（主岗）**。
    # Step 1 曾注释为"入会时角色快照"，该表述已作废 —— 项目级授权读此字段。
    role: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="本项目主岗（授权真源）"
    )
    # Step 2 新增：附加岗位（可叠加岗位的多岗支持，见设计说明 7.8.3 α）。
    # 权限 = 主岗 ∪ 附加岗（7.8.2）；排他岗位（总监/总监代表）不得出现在这里。
    extra_roles: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="附加岗位列表（不含排他岗位）"
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # Step 1 新增：离场时间（转场留痕）。本轮只加字段，
    # 移出项目仍走物理删除；改为软删除属 Step 5。
    left_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="离场时间（NULL=在岗）"
    )

    def role_codes(self) -> list[str]:
        """本成员在项目内的**全部岗位**（主岗 + 附加岗）。"""
        codes: list[str] = []
        if self.role:
            codes.append(self.role)
        for item in self.extra_roles or []:
            if isinstance(item, str) and item and item not in codes:
                codes.append(item)
        return codes
