"""角色与权限矩阵的初始化播种（Step 3）。

**幂等**：只在 ``roles`` 表**完全为空**时写入，已有数据一律不动 ——
管理员在界面上调过的权限不能被重启覆盖回去。

播种内容来自代码常量（``UserRole`` / ``ORG_ROLES`` / ``ROLE_LABELS`` /
``ROLE_PERMISSIONS``），因此这一步同时是「代码 → DB」的一次性搬运：
搬运完成后，DB 就是运行时真源（读路径见 ``permissions.get_matrix``）。
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import async_session
from app.models.permission_catalog import ALL_PERMISSION_KEYS
from app.models.role_permission import Role, RolePermission
from app.models.user import (
    ORG_ROLE_ADMIN,
    ORG_ROLE_SUPER_ADMIN,
    ROLE_LABELS,
    ROLE_PERMISSIONS,
    UserRole,
)

logger = logging.getLogger(__name__)


async def seed_roles_and_permissions() -> dict:
    """roles 表为空时按代码常量播种角色与权限矩阵。

    返回 ``{"seeded": bool, "roles": int, "permissions": int, "dropped": list}``。
    """
    async with async_session() as db:
        existing = (await db.execute(select(func.count()).select_from(Role))).scalar() or 0
        if existing:
            return {"seeded": False, "roles": int(existing), "permissions": 0, "dropped": []}

        known = set(ALL_PERMISSION_KEYS)
        dropped: list[str] = []
        sort_order = 0

        # 项目级岗位（顺序按 UserRole 枚举，界面列头顺序稳定可预期）
        for member in UserRole:
            db.add(
                Role(
                    key=member.value,
                    label=ROLE_LABELS.get(member.value, member.value),
                    is_org_level=False,
                    sort_order=sort_order,
                    enabled=True,
                )
            )
            sort_order += 1

        # 公司级角色（放最后，界面上与项目级岗位视觉区隔）
        for code in (ORG_ROLE_SUPER_ADMIN, ORG_ROLE_ADMIN):
            db.add(
                Role(
                    key=code,
                    label=ROLE_LABELS.get(code, code),
                    is_org_level=True,
                    sort_order=sort_order,
                    enabled=True,
                )
            )
            sort_order += 1

        perm_count = 0
        for role_key, perms in ROLE_PERMISSIONS.items():
            for permission_key in perms:
                if permission_key not in known:
                    # 目录是权威清单：种子里的键若不在目录内，说明两边漂移了。
                    # 这里丢弃并告警（而不是入库造出界面勾不上的幽灵权限）。
                    dropped.append(f"{role_key}:{permission_key}")
                    continue
                db.add(RolePermission(role_key=role_key, permission_key=permission_key))
                perm_count += 1

        await db.commit()
        if dropped:
            logger.warning("播种时丢弃了目录外的权限键：%s", ", ".join(sorted(set(dropped))))
        logger.info(
            "角色权限已播种：角色 %d 个、权限映射 %d 条", sort_order, perm_count
        )
        return {
            "seeded": True,
            "roles": sort_order,
            "permissions": perm_count,
            "dropped": sorted(set(dropped)),
        }


async def reset_role_permissions_to_default() -> dict:
    """把矩阵恢复为代码默认（界面「恢复默认」按钮）。

    只重建 ``role_permissions``，``roles`` 表的中文名/排序保持不变；
    同时把权限版本 +1，让前端感知到变更。
    """
    from app.services.permissions import bump_perm_version

    async with async_session() as db:
        role_keys = (await db.execute(select(Role.key))).scalars().all()
        if not role_keys:
            return {"reset": False, "reason": "尚未播种"}

        existing = (await db.execute(select(RolePermission))).scalars().all()
        for row in existing:
            await db.delete(row)
        await db.flush()

        known = set(ALL_PERMISSION_KEYS)
        perm_count = 0
        for role_key, perms in ROLE_PERMISSIONS.items():
            if role_key not in role_keys:
                continue
            for permission_key in perms:
                if permission_key in known:
                    db.add(
                        RolePermission(role_key=role_key, permission_key=permission_key)
                    )
                    perm_count += 1
        version = await bump_perm_version(db)
        await db.commit()
        return {"reset": True, "permissions": perm_count, "perm_version": version}
