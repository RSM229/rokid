"""跨项目查看授权（2026-10-20 新增）。

场景：管理员（org_role=admin，即项目负责人）默认**只能查看自己的项目**；
超管可单独授权某个管理员查看另一个管理员的项目。授权 = 一条本表记录，
与 ``project_members`` 解耦（避免把"查看授权"误当成"项目成员/岗位"）。

可见性由 ``app.routers.project`` 的 ``_granted_project_ids`` 并入项目列表与切换判断。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProjectViewGrant(Base):
    __tablename__ = "project_view_grants"
    __table_args__ = (
        UniqueConstraint("grantee_user_id", "project_id", name="uq_view_grant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grantee_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="被授权查看的管理员"
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), index=True, comment="被授权的项目"
    )
    grantor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, comment="授权人（超管）"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
