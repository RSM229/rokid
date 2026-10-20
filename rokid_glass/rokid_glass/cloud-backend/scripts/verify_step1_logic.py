"""Step 1 纯逻辑验证（无需 sqlalchemy / 不连库）。

用轻量 stub 顶替 sqlalchemy，使 ``app/models/user.py`` 可被导入，
从而验证**纯逻辑部分**：角色枚举、权限表、可叠加/排他划分、
is_super_admin / is_org_admin / permission_set / has_permission。

这不能替代服务器上的真实接口联调，但能在零依赖环境下确认
「角色 key 化后权限值未变」这一最关键的回归点。

运行：python scripts/verify_step1_logic.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  —— {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def install_sqlalchemy_stub() -> None:
    """用 stub 顶替 sqlalchemy 与 app.db，让模型模块可导入。

    关键：直接 stub ``app.db``（模型只需要它的 ``Base``），
    从而绕开 ``app.config`` → pydantic_settings 这条无关依赖链。
    """
    class _Base:
        """替代 DeclarativeBase：模型类继承它即可，不参与真实建表。"""
        metadata = MagicMock()

    # ---- app.db stub ----
    db_mod = types.ModuleType("app.db")
    db_mod.Base = _Base
    db_mod.engine = MagicMock()
    db_mod.async_session = MagicMock()
    db_mod.init_db = MagicMock()
    db_mod.get_db = MagicMock()
    sys.modules["app.db"] = db_mod

    # ---- sqlalchemy stub（模型文件里 import 到的名字）----
    sa = types.ModuleType("sqlalchemy")
    sa_orm = types.ModuleType("sqlalchemy.orm")

    def _mapped_column(*a, **kw):
        return MagicMock()

    class _MappedMeta:
        """支持 ``Mapped[int]`` / ``Mapped[str | None]`` 下标语法。"""

        def __getitem__(self, item):
            return MagicMock()

    _Mapped = _MappedMeta()

    class _DeclarativeBase:
        metadata = MagicMock()

    for name in ("Integer", "String", "Boolean", "DateTime", "Text", "Float",
                 "JSON", "ForeignKey", "LargeBinary", "Date", "Index",
                 "UniqueConstraint", "inspect", "text", "select", "func",
                 "and_", "or_", "desc", "asc", "cast", "case", "literal", "exists",
                 "create_async_engine"):
        setattr(sa, name, MagicMock())

    sa_orm.DeclarativeBase = _DeclarativeBase
    sa_orm.Mapped = _Mapped
    sa_orm.relationship = lambda *a, **kw: MagicMock()
    sa_orm.mapped_column = _mapped_column
    sa_orm.async_sessionmaker = MagicMock()
    sa.orm = sa_orm

    sa_engine = types.ModuleType("sqlalchemy.engine")
    sa_engine.Connection = MagicMock()
    sa_engine.Engine = MagicMock()
    sa.engine = sa_engine

    sa_ext_asyncio = types.ModuleType("sqlalchemy.ext.asyncio")
    sa_ext_asyncio.AsyncSession = MagicMock()
    sa_ext_asyncio.create_async_engine = MagicMock()
    sa_ext_asyncio.async_sessionmaker = MagicMock()

    sys.modules["sqlalchemy"] = sa
    sys.modules["sqlalchemy.orm"] = sa_orm
    sys.modules["sqlalchemy.engine"] = sa_engine
    sys.modules["sqlalchemy.ext"] = types.ModuleType("sqlalchemy.ext")
    sys.modules["sqlalchemy.ext.asyncio"] = sa_ext_asyncio


def _patch_user_init(User) -> None:
    """stub 环境下让 User(**kwargs) 直接落到实例属性上。

    真实 SQLAlchemy 的声明式基类会处理 kwargs；stub 的 Base 不会，
    这里补一个等价的最小实现（只用于纯逻辑测试）。
    """
    if getattr(User, "_stub_init_patched", False):
        return

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    User.__init__ = __init__
    User._stub_init_patched = True


def main() -> int:
    install_sqlalchemy_stub()

    from app.models.user import (
        ALL_PERMISSIONS,
        ORG_ROLE_ADMIN,
        ORG_ROLE_SUPER_ADMIN,
        ROLE_EXCLUSIVE,
        ROLE_LABELS,
        ROLE_PERMISSIONS,
        ROLE_STACKABLE,
        User,
        UserRole,
        role_label,
    )

    print("【1】角色枚举与映射完整性")
    keys = [m.value for m in UserRole]
    check("UserRole 有 7 个岗位", len(keys) == 7, f"实际 {len(keys)}: {keys}")
    check("全部为英文 key（无中文）", all(k.isascii() for k in keys))
    check("ROLE_LABELS 覆盖全部岗位", all(k in ROLE_LABELS for k in keys))
    check("ROLE_LABELS 全为中文展示名", all(not v.isascii() for v in ROLE_LABELS.values()))
    missing = [k for k in keys if k not in ROLE_PERMISSIONS]
    check("ROLE_PERMISSIONS 键覆盖全部岗位", not missing, f"缺: {missing}")

    print("\n【2】权限值未随键改名而改变（关键回归点）")
    # 基线取自 app/models/user.py 的实际值（改键前的值必须逐项保留）
    # 后续步骤的有意变更已并入基线并逐条注明：
    #   - member_manage（Step 2）：成员增删从 settings 拆出的独立 key
    #   - signature_manage（Step 3）：孤儿键收编，默认授予需签署文书的岗位
    #   - project_edit（2026-10-13）：由公司级专属改为跨两层键，总监可维护
    #     自家项目信息，故并入总监基线
    baseline = {
        "chief_supervisor": {
            "dashboard", "work_record", "inspection", "acceptance", "witness", "station",
            "hazard_ai", "knowledge_base", "kg_manage", "task_assign", "task_manage",
            "document_approve", "ledger", "settings", "user_center", "todo_center",
            "acceptance_template", "local_ai",
            "member_manage", "signature_manage", "project_edit",
        },
        "deputy_chief": {
            "dashboard", "work_record", "inspection", "acceptance", "witness", "station",
            "hazard_ai", "knowledge_base", "kg_manage", "task_assign", "task_manage",
            "document_approve", "ledger", "todo_center", "user_center",
            "acceptance_template", "local_ai",
            "signature_manage",
        },
        "special_supervisor": {
            "dashboard", "work_record", "inspection", "acceptance", "witness", "station",
            "hazard_ai", "knowledge_base", "task_receive", "ledger", "todo_center",
            "user_center", "local_ai",
            # Step 3：签章管理原为孤儿键（前端门控、后端无此权限），
            # 补进权限目录后默认授予需要签署文书的岗位。
            "signature_manage",
        },
        "inspector": {
            "dashboard", "work_record", "inspection", "hazard_ai", "knowledge_base",
            "task_receive", "todo_center", "user_center", "local_ai",
        },
    }
    for key, expect in baseline.items():
        actual = set(ROLE_PERMISSIONS.get(key, []))
        ok = actual == expect
        check(f"{key} 权限集与基线一致", ok,
              "" if ok else f"缺 {sorted(expect - actual)} / 多 {sorted(actual - expect)}")

    print("\n【3】新岗位给了最小权限集")
    for key in ("documenter", "witness", "safety_supervisor"):
        perms = ROLE_PERMISSIONS.get(key, [])
        n = len(perms)
        # 最小集：不超过 9 条，且不含高权项
        dangerous = {"settings", "user_manage", "kg_manage", "member_manage"}
        check(f"{key} 权限条目合理（≤9 且无高权项）",
              0 < n <= 9 and not (set(perms) & dangerous),
              f"{n} 条: {perms}")

    print("\n【4】可叠加 / 排他岗位划分")
    check("总监排他", "chief_supervisor" in ROLE_EXCLUSIVE)
    check("总监代表排他", "deputy_chief" in ROLE_EXCLUSIVE)
    check("专监可叠加", "special_supervisor" in ROLE_STACKABLE)
    check("资料员可叠加", "documenter" in ROLE_STACKABLE)
    check("见证员可叠加", "witness" in ROLE_STACKABLE)
    check("安全监理员可叠加", "safety_supervisor" in ROLE_STACKABLE)
    check("监理员可叠加", "inspector" in ROLE_STACKABLE)
    check("两类无交集", not (set(ROLE_STACKABLE) & set(ROLE_EXCLUSIVE)))
    check("两类并集=全部岗位",
          (set(ROLE_STACKABLE) | set(ROLE_EXCLUSIVE)) == set(keys),
          f"差集: {set(keys) - (set(ROLE_STACKABLE) | set(ROLE_EXCLUSIVE))}")

    print("\n【5】ALL_PERMISSIONS 覆盖并集")
    union = set()
    for v in ROLE_PERMISSIONS.values():
        union |= set(v)
    check("ALL_PERMISSIONS ⊇ 岗位权限并集", set(ALL_PERMISSIONS) >= union,
          f"缺: {sorted(union - set(ALL_PERMISSIONS))}")

    print("\n【6】公司级角色判据")
    _patch_user_init(User)
    u_super = User(username="admin", password_hash="x", real_name="超管",
                   role="chief_supervisor", org_role=ORG_ROLE_SUPER_ADMIN, is_active=True)
    u_mgr = User(username="mgr", password_hash="x", real_name="管理员",
                 role="special_supervisor", org_role=ORG_ROLE_ADMIN, is_active=True)
    u_plain = User(username="zhang", password_hash="x", real_name="张总监",
                   role="chief_supervisor", org_role=None, is_active=True)

    check("org_role=super_admin → is_super_admin", u_super.is_super_admin())
    check("org_role=admin → !is_super_admin", not u_mgr.is_super_admin())
    check("org_role=admin → is_org_admin", u_mgr.is_org_admin())
    check("org_role=None → !is_org_level", not u_plain.is_org_level())
    check("org_role=admin → is_org_level", u_mgr.is_org_level())

    print("\n【7】has_permission 三条路径")
    check("超管恒有权（越权探针）", u_super.has_permission("__nonexistent__"))
    check("超管持 user_manage", u_super.has_permission("user_manage"))
    check("普通总监按 ROLE_PERMISSIONS 授权", u_plain.has_permission("settings"))
    check("普通总监无 user_manage", not u_plain.has_permission("user_manage"))
    u_plain.effective_permissions = {"ledger_read"}
    check("有 effective_permissions 时优先（Step 3 预留）", u_plain.has_permission("ledger_read"))
    check("注入集外被拒（证明优先于 ROLE_PERMISSIONS）",
          not u_plain.has_permission("settings"))
    del u_plain.effective_permissions
    check("删除注入集后回退 ROLE_PERMISSIONS", u_plain.has_permission("settings"))

    print("\n【8】管理员不会白得项目级权限（Step 1 零风险的关键）")
    mgr_effective = set(ROLE_PERMISSIONS.get(u_mgr.role, []))
    # ⚠️ 原断言为「公司级角色不在 ROLE_PERMISSIONS 索引内」，Step 2 引入公司级
    # 角色后该条已**语义过时**（org_role 现在正式承载跨项目权限）。改成校验
    # 其**范围**：公司级角色只能拿公司级键，不能白得项目级业务权限。
    org_level_keys = {
        "project_create", "project_edit", "user_manage", "user_approve",
        "role_grant", "member_manage",
    }
    admin_perms = set(ROLE_PERMISSIONS.get(ORG_ROLE_ADMIN, []))
    check("公司级角色（admin）权限 ⊆ 公司级键集合",
          admin_perms <= org_level_keys,
          f"越界: {sorted(admin_perms - org_level_keys)}")
    check("admin 不持有项目级业务权限",
          not (admin_perms & {"settings", "ledger", "inspection", "acceptance",
                              "work_record", "dashboard"}),
          f"实际: {sorted(admin_perms)}")
    check("管理员的权限只来自其项目岗，不来自 org_role",
          set(u_mgr.permission_set()) == mgr_effective,
          f"实际 {sorted(u_mgr.permission_set())}")
    check("管理员不能凭 org_role 拿 settings（除非岗位本身有）",
          ("settings" in mgr_effective) == u_mgr.has_permission("settings"))

    print("\n【9】role_label 回退")
    check("已知 key → 中文", role_label("chief_supervisor") == "总监理工程师")
    check("未知 key 原样返回", role_label("zzz") == "zzz")
    check("None → 空串", role_label(None) == "")

    print("\n" + "=" * 60)
    if FAILED:
        print(f"✗ 失败 {len(FAILED)} 项：")
        for f in FAILED:
            print(f"   - {f}")
        return 1
    print("✓ 全部通过（纯逻辑层）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
