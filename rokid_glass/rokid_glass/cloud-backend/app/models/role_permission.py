"""角色与角色权限矩阵（Step 3 · 权限可配）。

两张表的分工：

    ``roles``
        角色**元数据**：key / 中文名 / 是否公司级 / 排序 / 启用 / 权限版本。
        角色 key **锁定**为代码里的 ``UserRole`` 枚举 ∪ ``ORG_ROLES`` —— 界面
        只能改中文名、排序、启用与权限，**不允许新增或删除角色 key**。
        这一条是硬约束：`project_members.role` 存的就是 key，全后端几十处
        逻辑都在拿枚举值做比较，DB 里凭空多出一个 key 会变成"幽灵角色"，
        既不报错也不生效。

    ``role_permissions``
        角色 → 权限的映射行。权限键必须落在 ``permission_catalog`` 目录内。

**权限版本**：任何一次权限或角色变更，都会把 ``roles.perm_version`` 全表 +1，
客户端取 ``MAX(perm_version)`` 作为「权限配置版本号」。前端拿它比对本地快照，
就能在登录态下即时刷新菜单，而不必重新登录（设计说明 §8.4「权限即时生效」）。

**为什么权限要用独立表而不是 JSON 列**：矩阵是「按权限反查角色」
（``roles_having_permission``）的热路径 —— 派单、通知投递都要用，
行存 + ``(role_key, permission_key)`` 唯一索引比每次解析 JSON 更直接，
也便于在库里审计「谁在什么时候改了这一格」。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Role(Base):
    """角色元数据。key 与代码枚举一一对应，不可在界面增删。"""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, comment="角色 key（= UserRole.value / ORG_ROLES）"
    )
    label: Mapped[str] = mapped_column(String(64), comment="中文展示名（可改，仅影响展示）")
    is_org_level: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", comment="是否公司级角色"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", comment="界面展示顺序"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", comment="停用后不可再被指派（不影响存量）"
    )
    # 全局权限版本：任何变更把**全表** +1，取 MAX 即当前版本。
    # 单调递增，前端只需比对整数；不用时间戳是为了避免时钟回拨与同秒歧义。
    perm_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", comment="权限配置版本（全表 +1，取 MAX）"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class RolePermission(Base):
    """角色 → 权限 的映射行（矩阵的存储形态）。"""

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_key", "permission_key", name="uq_role_permission"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_key: Mapped[str] = mapped_column(String(32), index=True, comment="角色 key")
    permission_key: Mapped[str] = mapped_column(String(64), comment="权限 key（须在权限目录内）")
    updated_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="最后修改者的 user_id"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
