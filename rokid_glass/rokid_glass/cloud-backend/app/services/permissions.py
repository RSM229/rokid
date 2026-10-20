"""权限合成（Step 2 核心）—— 两级角色 + 岗位集合。

设计依据：`docs/用户体系结构设计说明-2026-10-09.md` §7.6 / §7.8.2

权限 = **公司级权限（跨项目）** ∪ **当前项目岗位集合的权限（随上下文变化）**

    org_perms     = ROLE_PERMISSIONS[user.org_role]            # 公司级
    project_perms = ⋃ ROLE_PERMISSIONS[c] for c in 成员的全部岗位  # 项目级
    perms         = org_perms | project_perms

修复的核心缺陷（跨项目角色串权）：
    旧逻辑读全局 `users.role`，于是"A 项目总监 / B 项目监理员"的人在 B 项目
    也拿到总监权限。改为读 `project_members` 后，切到 B 即自然降级。

**为什么放在 services 而不放进模型**：合成需要异步查询成员表，
放进 `User` 会让模型层依赖 ORM 会话；独立函数也便于零依赖单测。

**Step 3 起矩阵可配置**：本模块同时是「取权限矩阵」的唯一入口。
矩阵默认取代码常量，启动时/变更后由 DB 覆盖，DB 不可用则回退代码常量 ——
所有取权限的函数（含被 ``task.py`` 直接同步调用的 ``permissions_for_roles``）
都改读 :func:`get_matrix`，签名保持同步，因此调用点零改动。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_member import ProjectMember
from app.models.role_permission import Role, RolePermission
from app.models.user import ROLE_PERMISSIONS, User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step 3：权限矩阵的进程内缓存
# ---------------------------------------------------------------------------
# 权限是鉴权热路径（``get_current_user`` 每个请求都要合成一次），不能每次
# 都去查两张表，所以读路径走内存、写路径负责刷新。
#
# 🔴 前提：生产是**单进程** uvicorn（``--reload`` 单 worker）。若将来改成
# 多 worker，本缓存会各进程独立 —— 届时要么把版本号存 DB 并在每次请求比对
# （``roles.perm_version`` 已经为此准备好），要么换成 Redis 之类。
#
# ``None`` 表示「尚未从 DB 加载」或「DB 里还没播种」，此时回退代码常量。
_matrix_cache: dict[str, list[str]] | None = None

# 权限配置版本（``MAX(roles.perm_version)``）。跟着矩阵一起加载，
# 由 HTTP 中间件写进响应头 —— 前端据此在登录态下即时刷新菜单，不必重登。
# 放内存是因为它要在**每个响应**上写头，不能每条请求都查库。
_version_cache: int = 0


def get_matrix() -> dict[str, list[str]]:
    """当前生效的角色权限矩阵（同步，供热路径调用）。

    未加载 / DB 表为空 / 读取失败时返回代码常量 :data:`ROLE_PERMISSIONS`。
    """
    return _matrix_cache if _matrix_cache is not None else ROLE_PERMISSIONS


def get_cached_perm_version() -> int:
    """当前权限版本（同步，供中间件写响应头）。"""
    return _version_cache


def invalidate_matrix() -> None:
    """丢弃缓存，下一次启动/加载时重新从 DB 取（变更接口调用）。"""
    global _matrix_cache
    _matrix_cache = None


async def load_matrix(db: AsyncSession) -> dict[str, list[str]]:
    """从 DB 加载矩阵并覆盖缓存；失败时保持原状（回退代码常量）。

    判定「是否已播种」看的是 ``roles`` 表而不是 ``role_permissions``：
    后者可能被合法地清空（某角色权限全取消），若据此判定"未播种"，
    就会在管理员**故意清空**权限时把代码种子又灌回去 —— 配置怎么改都改不掉。
    """
    global _matrix_cache, _version_cache
    try:
        role_keys = (await db.execute(select(Role.key))).scalars().all()
        if not role_keys:
            _matrix_cache = None
            _version_cache = 0
            return ROLE_PERMISSIONS
        rows = (
            await db.execute(
                select(RolePermission.role_key, RolePermission.permission_key)
            )
        ).all()
        from sqlalchemy import func

        version = (await db.execute(select(func.max(Role.perm_version)))).scalar()
    except Exception:  # noqa: BLE001 —— 兜底优先，任何异常都不该让全站失去权限
        logger.exception("权限矩阵加载失败，回退代码常量")
        return get_matrix()

    matrix: dict[str, list[str]] = {key: [] for key in role_keys}
    for role_key, permission_key in rows:
        matrix.setdefault(role_key, []).append(permission_key)
    _matrix_cache = matrix
    _version_cache = int(version or 0)
    return matrix


async def current_perm_version(db: AsyncSession) -> int:
    """当前权限配置版本（``MAX(roles.perm_version)``）。

    任何权限/角色变更都会把全表 +1，因此该值单调递增。
    客户端拿它比对本地快照，即可知道"权限被改过，去刷新一下"。
    """
    from sqlalchemy import func

    try:
        value = (await db.execute(select(func.max(Role.perm_version)))).scalar()
    except Exception:  # noqa: BLE001
        logger.exception("读取权限版本失败")
        return 0
    return int(value or 0)


async def bump_perm_version(db: AsyncSession) -> int:
    """把权限版本全表 +1 并返回新值（变更接口在同一事务里调用）。"""
    from sqlalchemy import func, update

    await db.execute(update(Role).values(perm_version=Role.perm_version + 1))
    value = (await db.execute(select(func.max(Role.perm_version)))).scalar()
    return int(value or 0)


def permissions_for_roles(codes: list[str] | tuple[str, ...] | set[str]) -> set[str]:
    """一组岗位 key → 权限并集（纯函数，便于单测）。"""
    matrix = get_matrix()
    perms: set[str] = set()
    for code in codes:
        if code:
            perms |= set(matrix.get(code, []))
    return perms


def org_permissions(org_role: str | None) -> set[str]:
    """公司级权限。``org_role`` 不在权限表内则返回空集。"""
    if not org_role:
        return set()
    return set(get_matrix().get(org_role, []))


def roles_having_permission(permission: str) -> list[str]:
    """反查：哪些**岗位**持有该权限 key（纯函数）。

    用来替换 ``ROLE_PERMISSIONS.items()`` 里现写的 ``"task_receive" in perms``
    这类反向遍历 —— 集中一处，口径不会漂移。

    注意：只返回**项目级**岗位（``UserRole`` 枚举内的 key），
    公司级 ``super_admin``/``admin`` 不是"项目内可被指派的人"，
    混进来会让派单列表出现管理员。
    """
    from app.models.user import UserRole

    project_codes = {member.value for member in UserRole}
    matrix = get_matrix()
    return [
        code
        for code, perms in matrix.items()
        if code in project_codes and permission in perms
    ]


def project_role_codes() -> list[str]:
    """全部**项目级**岗位 key（不含公司级）。"""
    from app.models.user import UserRole

    return [member.value for member in UserRole]


async def member_role_codes(
    db: AsyncSession, user_id: int, project_id: int | None
) -> list[str]:
    """用户在某项目的全部岗位（主岗 + 附加岗）；无项目/非成员返回空列表。

    有效成员 = ``left_at IS NULL``（Step 5 才改软删除，此处已按有效成员过滤，
    物理删除时代该条件恒真，不会改变现有行为）。
    """
    if project_id is None:
        return []
    result = await db.execute(
        select(ProjectMember.role, ProjectMember.extra_roles).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.left_at.is_(None),
        )
    )
    codes: list[str] = []
    for primary, extra in result.all():
        if primary and primary not in codes:
            codes.append(primary)
        for item in extra or []:
            if isinstance(item, str) and item and item not in codes:
                codes.append(item)
    return codes


async def effective_permissions(user: User, db: AsyncSession) -> list[str]:
    """公司级 ∪ 当前项目岗位集合 → 排序后的权限列表。

    超管返回全量（与 Step 1 行为一致）；其余按公式合成。
    """
    if user.is_super_admin():
        from app.models.user import ALL_PERMISSIONS

        return list(ALL_PERMISSIONS)

    perms = org_permissions(user.org_role)
    perms |= permissions_for_roles(
        await member_role_codes(db, user.id, user.project_id)
    )
    return sorted(perms)


async def attach_effective_permissions(user: User, db: AsyncSession) -> User:
    """把合成结果挂到 ``user.effective_permissions``（供同步 ``has_permission`` 读取）。

    这是设计 §7.6「低改动量实现」的落点：只在 ``get_current_user`` 调一次，
    ``has_permission`` 的 19 处调用点无需改动。
    """
    setattr(user, "effective_permissions", await effective_permissions(user, db))
    return user


def member_has_role(member: ProjectMember, codes: list[str] | tuple[str, ...] | set[str]) -> bool:
    """成员是否持有所给岗位之一（主岗或附加岗）。纯函数，便于单测。

    用于替换「``User.role in (...)`` + ``User.project_id == ...``」这类
    **全局单值角色**投递判据（设计 §7.8.4 连带三处）。
    """
    held = set(member.role_codes())
    return any(code in held for code in codes if code)


async def project_members_with_permission(
    db: AsyncSession, project_id: int | None, permission: str
) -> list[tuple[ProjectMember, User]]:
    """项目内**持有某权限**的有效成员（成员行, 用户对象）列表。

    用于替换 ``select(User).where(User.project_id == pid, User.role.in_(...))``
    这类"按全局单值角色查人"的写法：岗位改从成员表取（主岗 ∪ 附加岗），
    归属以 ``left_at IS NULL`` 为准。
    """
    if project_id is None:
        return []
    wanted = set(roles_having_permission(permission))
    if not wanted:
        return []
    result = await db.execute(
        select(User, ProjectMember)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.left_at.is_(None),
            User.is_active.is_(True),
        )
        .order_by(User.id)
    )
    out: list[tuple[ProjectMember, User]] = []
    for user, member in result.all():
        if set(member.role_codes()) & wanted:
            out.append((member, user))
    return out


async def member_user_ids_with_roles(
    db: AsyncSession, project_id: int | None, codes: list[str]
) -> list[int]:
    """项目中持有任一岗位的**有效成员** user_id 列表（通知/待办投递用）。

    与旧写法（``User.project_id == project_id AND User.role IN (...)``）的区别：
      - 归属来自 ``project_members``，所以「人已转场、上下文却仍挂在本项目」
        的脏数据不会再被误投；跨项目兼职者也能正确命中。
      - 岗位取主岗 ∪ 附加岗，"一人多岗"不再漏发。
      - 同时受 ``users.is_active`` 约束（停用账号不推送）。
    """
    if project_id is None or not codes:
        return []
    result = await db.execute(
        select(ProjectMember.user_id, ProjectMember.role, ProjectMember.extra_roles)
        .join(User, User.id == ProjectMember.user_id)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.left_at.is_(None),
            User.is_active.is_(True),
        )
    )
    wanted = set(codes)
    user_ids: list[int] = []
    for row_user_id, primary, extra in result.all():
        held: set[str] = set()
        if primary:
            held.add(primary)
        for item in extra or []:
            if isinstance(item, str) and item:
                held.add(item)
        if held & wanted and row_user_id not in user_ids:
            user_ids.append(row_user_id)
    return user_ids
