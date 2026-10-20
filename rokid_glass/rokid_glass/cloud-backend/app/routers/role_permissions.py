"""角色权限配置接口（Step 3）。

端点（挂载前缀 ``/api/permissions``）：

    GET  /matrix              读：角色 × 权限矩阵 + 权限目录 + 版本号
    PUT  /matrix/{role_key}   写：覆盖某角色的权限集合
    POST /matrix/reset        把矩阵恢复为代码默认

安全约束（设计说明 §8.4）：

1. **只有持 ``role_grant`` 的人能改**（当前仅超级管理员）。
2. **不得授予自己没有的权限** —— 非超管提交的集合里若有自己都没有的键，
   直接 403。否则任何人都能给自己加权限，配置页就成了提权工具。
3. **项目级角色不得持有公司级权限**（``ORG_ONLY_PERMISSION_KEYS``）——
   防止把 ``user_manage`` / ``project_create`` 之类挂到项目岗位上。
4. **权限键必须在目录内**（``permission_catalog``）—— 拒绝凭空造键，
   否则超管的「全量」会失真、界面会出现勾不上的幽灵权限。
5. **不允许编辑 ``super_admin``**：超管恒为全量（``is_super_admin`` 短路），
   它的矩阵行改了什么也不产生效果，放开只会让人误以为配置生效。
6. 每次变更写 ``security_audit``（谁、改哪个角色、加了什么、去掉了什么）。
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.permission_catalog import (
    ALL_PERMISSION_KEYS,
    ORG_ONLY_PERMISSION_KEYS,
    PERMISSION_CATALOG,
    PERMISSION_GROUPS,
    validate_permission_keys,
)
from app.models.role_permission import Role, RolePermission
from app.models.security_audit import SecurityAudit
from app.models.user import ORG_ROLE_SUPER_ADMIN, User
from app.routers.auth import require_permission
from app.services.permissions import (
    bump_perm_version,
    current_perm_version,
    get_matrix,
    load_matrix,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class RolePermissionsUpdate(BaseModel):
    """整集合覆盖，不是增量 —— 界面是勾选框，提交的即最终状态。"""

    permissions: list[str] = Field(default_factory=list)
    remark: str | None = None


async def _load_roles(db: AsyncSession) -> list[Role]:
    result = await db.execute(select(Role).order_by(Role.sort_order, Role.id))
    return list(result.scalars().all())


@router.get("/matrix")
async def read_matrix(
    user: User = Depends(require_permission("role_grant")),
    db: AsyncSession = Depends(get_db),
):
    """权限矩阵全景（供配置页渲染）。"""
    roles = await _load_roles(db)
    matrix = get_matrix()
    version = await current_perm_version(db)

    role_items = [
        {
            "key": role.key,
            "label": role.label,
            "is_org_level": bool(role.is_org_level),
            "sort_order": role.sort_order,
            "enabled": bool(role.enabled),
            "editable": role.key != ORG_ROLE_SUPER_ADMIN,
            # 超管恒为**目录全量**（与 is_super_admin 短路同口径）。不取 DB 矩阵：
            # 否则目录新增权限键后，这里会比超管真实权限少一项，接口即失真。
            "permissions": (
                sorted(ALL_PERMISSION_KEYS)
                if role.key == ORG_ROLE_SUPER_ADMIN
                else sorted(matrix.get(role.key, []))
            ),
        }
        for role in roles
    ]
    return {
        "roles": role_items,
        "groups": list(PERMISSION_GROUPS),
        "permissions": [
            {
                "key": info.key,
                "label": info.label,
                "group": info.group,
                "org_level": info.org_level,
                "hint": info.hint,
            }
            for info in PERMISSION_CATALOG
        ],
        "perm_version": version,
        # 操作者自己的权限：前端据此灰掉"你自己都没有的权限"，
        # 与后端第 2 条校验同口径，避免"点了保存才报 403"。
        "my_permissions": sorted(user.permission_set()),
        "org_only_permissions": sorted(ORG_ONLY_PERMISSION_KEYS),
    }


@router.put("/matrix/{role_key}")
async def update_role_permissions(
    role_key: str,
    req: RolePermissionsUpdate,
    user: User = Depends(require_permission("role_grant")),
    db: AsyncSession = Depends(get_db),
):
    """覆盖某角色的权限集合。"""
    role = (
        await db.execute(select(Role).where(Role.key == role_key))
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {role_key}")
    if role_key == ORG_ROLE_SUPER_ADMIN:
        raise HTTPException(
            status_code=400,
            detail="超级管理员恒为全量权限，无需也禁止配置（改了不生效，只会误导）",
        )

    # 约束 4：权限键必须在目录内
    unknown = validate_permission_keys(req.permissions)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"权限键不在目录内: {', '.join(sorted(unknown))}",
        )

    wanted = sorted(set(req.permissions))

    # 约束 3：项目级角色不得持有公司级权限
    if not role.is_org_level:
        illegal = sorted(set(wanted) & ORG_ONLY_PERMISSION_KEYS)
        if illegal:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{role.label}是项目级岗位，不能持有公司级权限: "
                    f"{', '.join(illegal)}"
                ),
            )

    # 约束 2：不得授予自己没有的权限（超管天然全量，豁免）
    if not user.is_super_admin():
        extra = sorted(set(wanted) - set(user.permission_set()))
        if extra:
            raise HTTPException(
                status_code=403,
                detail=f"不能授予自己没有的权限: {', '.join(extra)}",
            )

    existing = (
        await db.execute(
            select(RolePermission).where(RolePermission.role_key == role_key)
        )
    ).scalars().all()
    current = {row.permission_key for row in existing}
    target = set(wanted)
    added = sorted(target - current)
    removed = sorted(current - target)

    if not added and not removed:
        return {
            "updated": False,
            "role_key": role_key,
            "permissions": wanted,
            "perm_version": await current_perm_version(db),
            "message": "无变化",
        }

    for row in existing:
        if row.permission_key in removed:
            await db.delete(row)
    for permission_key in added:
        db.add(
            RolePermission(
                role_key=role_key,
                permission_key=permission_key,
                updated_by=user.id,
            )
        )

    version = await bump_perm_version(db)
    db.add(
        SecurityAudit(
            user_id=user.id,
            username=user.username,
            action="role_permission_update",
            detail=json.dumps(
                {
                    "role": role_key,
                    "added": added,
                    "removed": removed,
                    "remark": req.remark or "",
                    "perm_version": version,
                },
                ensure_ascii=False,
            ),
        )
    )
    await db.commit()

    # 变更后立刻刷新本进程缓存，保证"改完下一个请求就生效"
    await load_matrix(db)
    logger.info(
        "角色权限更新：%s +%s -%s（version=%s）", role_key, added, removed, version
    )
    return {
        "updated": True,
        "role_key": role_key,
        "permissions": wanted,
        "added": added,
        "removed": removed,
        "perm_version": version,
    }


@router.post("/matrix/reset")
async def reset_matrix(
    user: User = Depends(require_permission("role_grant")),
    db: AsyncSession = Depends(get_db),
):
    """把矩阵恢复为代码默认值（界面「恢复默认」）。

    只重建权限映射；角色中文名/排序不动（那些是展示层的调整，不属于"默认值"）。
    """
    from app.services.role_seed import reset_role_permissions_to_default

    result = await reset_role_permissions_to_default()
    if not result.get("reset"):
        raise HTTPException(status_code=400, detail=result.get("reason", "重置失败"))
    db.add(
        SecurityAudit(
            user_id=user.id,
            username=user.username,
            action="role_permission_reset",
            detail=json.dumps(
                {"permissions": result.get("permissions"), "perm_version": result.get("perm_version")},
                ensure_ascii=False,
            ),
        )
    )
    await db.commit()
    await load_matrix(db)
    return result
