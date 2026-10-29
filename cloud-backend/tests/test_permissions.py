# -*- coding: utf-8 -*-
"""权限合成（services/permissions.py）回归测试。

此前该模块 **零测试覆盖**，而它位于鉴权热路径（``get_current_user`` 每请求
合成一次），一次口径漂移就是全站越权或全员失权。本文件固化三件事：

1. 矩阵兜底：DB 未加载时回退代码常量，``invalidate_matrix`` 能复位；
2. 合成公式：``公司级 ∪ 当前项目岗位集合``，空/未知角色不产生权限；
3. 反查只含**项目级**岗位（``roles_having_permission`` 不混入 super_admin/admin）；
4. 端到端：同一人在 A 项目是总监、B 项目是监理员，切换后权限**降级**
   （这正是 permissions.py 文档头声称修复的「跨项目角色串权」）。

跑法：``cd cloud-backend && python -m unittest tests.test_permissions -v``
"""
from __future__ import annotations

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.organization import Organization  # noqa: F401 —— 注册 organizations 表
from app.models.project_member import ProjectMember
from app.models.role_permission import Role, RolePermission
from app.models.user import (
    ROLE_PERMISSIONS,
    User,
    UserRole,
)
from app.services.permissions import (
    effective_permissions,
    get_matrix,
    invalidate_matrix,
    member_has_role,
    member_role_codes,
    org_permissions,
    permissions_for_roles,
    roles_having_permission,
)


class TestMatrixFallback(unittest.TestCase):
    """矩阵读路径：无 DB 覆盖时回退代码常量。"""

    def setUp(self) -> None:
        invalidate_matrix()

    def tearDown(self) -> None:
        invalidate_matrix()

    def test_falls_back_to_code_constants(self):
        """未加载矩阵时 get_matrix() 必须是代码常量（否则全站失权）。"""
        self.assertIs(get_matrix(), ROLE_PERMISSIONS)

    def test_invalidate_resets_cache(self):
        """invalidate 后必须回到代码常量，不能残留上一次的 DB 快照。"""
        import app.services.permissions as perm_mod

        perm_mod._matrix_cache = {"inspector": ["work_record"]}
        self.assertEqual(get_matrix(), {"inspector": ["work_record"]})
        invalidate_matrix()
        self.assertIs(get_matrix(), ROLE_PERMISSIONS)

    def test_super_admin_constant_is_not_used_for_authz(self):
        """超管走 is_super_admin() 短路拿全量，代码常量里的 super_admin 只是种子。

        这里锁住「两端口径」：常量里 super_admin 项数 < 目录全量，
        实际鉴权不能依赖常量里那几项。
        """
        from app.models.permission_catalog import ALL_PERMISSION_KEYS

        self.assertLess(
            len(ROLE_PERMISSIONS["super_admin"]), len(ALL_PERMISSION_KEYS),
            "super_admin 代码常量不应等于全量目录 —— 若相等说明有人把短路逻辑"
            "改成了读常量，超管会在目录新增键后失去新权限",
        )


class TestComposition(unittest.TestCase):
    """合成公式：公司级 ∪ 项目级岗位集合。"""

    def setUp(self) -> None:
        invalidate_matrix()

    def tearDown(self) -> None:
        invalidate_matrix()

    def test_union_of_multiple_roles(self):
        """一人多岗取并集，不能只取主岗。"""
        chief = set(ROLE_PERMISSIONS[UserRole.CHIEF_SUPERVISOR.value])
        witness = set(ROLE_PERMISSIONS[UserRole.WITNESS.value])
        union = permissions_for_roles(
            [UserRole.CHIEF_SUPERVISOR.value, UserRole.WITNESS.value]
        )
        self.assertEqual(union, chief | witness)
        # 见证岗独有的键必须进来（证明不是只取第一个岗位）
        only_witness = witness - chief
        if only_witness:
            self.assertTrue(only_witness <= union)

    def test_empty_codes_yield_empty_set(self):
        self.assertEqual(permissions_for_roles([]), set())

    def test_unknown_role_is_ignored(self):
        """未知岗位 key 不得抛错、也不得凭空产生权限。"""
        self.assertEqual(permissions_for_roles(["不存在的岗位"]), set())
        self.assertEqual(permissions_for_roles(["", None]), set())  # type: ignore[list-item]

    def test_org_permissions_none_is_empty(self):
        """公司级角色为 NULL（纯项目级人员）时没有任何公司级权限。"""
        self.assertEqual(org_permissions(None), set())
        self.assertEqual(org_permissions(""), set())

    def test_org_permissions_unknown_role_is_empty(self):
        self.assertEqual(org_permissions("不存在的公司角色"), set())

    def test_org_permissions_returns_matrix_values(self):
        self.assertEqual(
            set(org_permissions("admin")), set(ROLE_PERMISSIONS["admin"])
        )


class TestRolesHavingPermission(unittest.TestCase):
    """反查口径：只返回项目级岗位。"""

    def setUp(self) -> None:
        invalidate_matrix()

    def tearDown(self) -> None:
        invalidate_matrix()

    def test_project_level_permission_never_includes_org_roles(self):
        """work_record 只有项目级岗位持有；super_admin/admin 不得出现在结果里。

        混进来会让「派单/通知候选人」列表出现公司级管理员。
        """
        roles = roles_having_permission("work_record")
        self.assertTrue(roles, "work_record 应至少有一个项目级岗位持有")
        project_codes = {m.value for m in UserRole}
        self.assertTrue(set(roles) <= project_codes)
        self.assertNotIn("super_admin", roles)
        self.assertNotIn("admin", roles)

    def test_org_only_permission_yields_empty(self):
        """仅公司级可持有的键，反查项目级岗位必须是空 —— 这正是设计意图。

        如果哪天返回了项目级岗位，说明 org_level 闸门被绕过。
        """
        from app.models.permission_catalog import ORG_ONLY_PERMISSION_KEYS

        for key in ORG_ONLY_PERMISSION_KEYS:
            self.assertEqual(
                roles_having_permission(key), [],
                f"{key} 是 org_level 权限，不应被任何项目级岗位持有",
            )

    def test_unknown_permission_yields_empty(self):
        self.assertEqual(roles_having_permission("不存在的权限"), [])


class TestMemberHasRole(unittest.TestCase):
    """成员岗位判定：主岗 + 附加岗。"""

    def test_primary_role(self):
        member = ProjectMember(role=UserRole.INSPECTOR.value, extra_roles=[])
        self.assertTrue(member_has_role(member, [UserRole.INSPECTOR.value]))

    def test_extra_role_counts(self):
        """附加岗必须算持岗 —— 旧代码只判主岗，一人多岗会漏发通知。"""
        member = ProjectMember(
            role=UserRole.INSPECTOR.value, extra_roles=[UserRole.WITNESS.value]
        )
        self.assertTrue(member_has_role(member, [UserRole.WITNESS.value]))

    def test_negative_case(self):
        member = ProjectMember(role=UserRole.INSPECTOR.value, extra_roles=[])
        self.assertFalse(member_has_role(member, [UserRole.CHIEF_SUPERVISOR.value]))

    def test_empty_codes_never_matches(self):
        member = ProjectMember(role=UserRole.INSPECTOR.value, extra_roles=[])
        self.assertFalse(member_has_role(member, []))
        self.assertFalse(member_has_role(member, ["", None]))  # type: ignore[list-item]


class TestCrossProjectDowngrade(unittest.TestCase):
    """端到端：同一人在不同项目的权限必须随上下文变化（跨项目串权回归）。

    permissions.py 文档头声称的「修复的核心缺陷」：
        A 项目总监 / B 项目监理员 的人，在 B 项目不应拿到总监权限。
    """

    @classmethod
    def setUpClass(cls) -> None:
        import asyncio

        cls.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        cls.Session = async_sessionmaker(cls.engine, expire_on_commit=False)

        async def prepare() -> None:
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(prepare())

    @classmethod
    def tearDownClass(cls) -> None:
        import asyncio

        asyncio.run(cls.engine.dispose())

    def setUp(self) -> None:
        invalidate_matrix()
        import asyncio

        from sqlalchemy import delete

        async def seed() -> None:
            async with self.Session() as db:
                # 每个用例独立数据：先清空（顺序沿外键依赖倒序）
                for model in (ProjectMember, User, RolePermission, Role):
                    await db.execute(delete(model))
                await db.commit()

                db.add_all([
                    # roles 表播种：判定「已播种」看这张表
                    Role(key=UserRole.CHIEF_SUPERVISOR.value, label="总监",
                         is_org_level=False, sort_order=1, perm_version=1),
                    Role(key=UserRole.INSPECTOR.value, label="监理员",
                         is_org_level=False, sort_order=2, perm_version=1),
                    Role(key="admin", label="管理员", is_org_level=True,
                         sort_order=3, perm_version=1),
                    # 矩阵按两岗的代码常量播种
                    *[
                        RolePermission(role_key=UserRole.CHIEF_SUPERVISOR.value,
                                       permission_key=key)
                        for key in ROLE_PERMISSIONS[UserRole.CHIEF_SUPERVISOR.value]
                    ],
                    *[
                        RolePermission(role_key=UserRole.INSPECTOR.value,
                                       permission_key=key)
                        for key in ROLE_PERMISSIONS[UserRole.INSPECTOR.value]
                    ],
                ])
                user = User(
                    username="dual_role_tester", password_hash="x",
                    real_name="双岗测试", role=UserRole.INSPECTOR.value,
                    org_role=None,
                )
                db.add(user)
                await db.flush()
                # 同一人：项目 1 总监，项目 2 监理员
                db.add_all([
                    ProjectMember(project_id=1, user_id=user.id,
                                  role=UserRole.CHIEF_SUPERVISOR.value),
                    ProjectMember(project_id=2, user_id=user.id,
                                  role=UserRole.INSPECTOR.value),
                ])
                await db.commit()
                self.user_id = user.id

        asyncio.run(seed())

    def _perms(self, project_id: int) -> set[str]:
        import asyncio

        async def run() -> set[str]:
            async with self.Session() as db:
                from app.services.permissions import load_matrix

                await load_matrix(db)
                user = await db.get(User, self.user_id)
                user.project_id = project_id
                return set(await effective_permissions(user, db))

        return asyncio.run(run())

    def test_chief_role_in_project_a(self):
        perms = self._perms(1)
        self.assertIn("task_assign", perms, "项目 1 是总监，应有任务派发权")

    def test_inspector_role_in_project_b_is_downgraded(self):
        """🔴 核心回归：切到 B 项目后必须失去总监专属权限。"""
        perms = self._perms(2)
        self.assertNotIn(
            "task_assign", perms,
            "项目 2 只是监理员，不得保留项目 1 的总监权限（跨项目串权）",
        )

    def test_non_member_gets_no_project_perms(self):
        """非成员（project_id 指向不存在的项目）只留公司级权限。"""
        perms = self._perms(999)
        self.assertEqual(perms, set(), "非成员不应有任何项目级权限（org_role 为 NULL）")

    def test_member_role_codes_collects_extra_roles(self):
        import asyncio

        async def run() -> list[str]:
            async with self.Session() as db:
                return await member_role_codes(db, self.user_id, 1)

        codes = asyncio.run(run())
        self.assertEqual(codes, [UserRole.CHIEF_SUPERVISOR.value])


if __name__ == "__main__":
    unittest.main(verbosity=2)
