"""跨项目查看授权（2026-10-20 新增；2026-10-28 改为「按管理员授权」）。

场景：管理员（org_role=admin，即项目负责人）默认**只能查看自己的项目**；
超管可单独授权某个管理员查看**另一个管理员名下的全部项目**。

授权粒度从「单项目」升级为「管理员」：一条本表记录表示
``grantee_user_id`` 可查看 ``grantor_admin_id``（源管理员）名下的所有项目。
可见项目在查询时由 ``app.routers.project._granted_project_ids`` 按源管理员的
项目成员关系展开，与 ``project_members`` 解耦（避免把"查看授权"误当成
"项目成员/岗位"）。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProjectViewGrant(Base):
    __tablename__ = "project_view_grants"
    __table_args__ = (
        UniqueConstraint("grantee_user_id", "grantor_admin_id", name="uq_view_grant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grantee_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="被授权查看的管理员"
    )
    grantor_admin_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, comment="被共享其项目的管理员（源管理员）"
    )
    grantor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, comment="授权人（超管）"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
