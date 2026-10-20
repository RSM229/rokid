"""Step 3 前滚迁移：角色与权限矩阵（2 张新表 + 播种）。

对应清单：``docs/Step3-逐行改动清单-2026-10-11.md``

本步的 schema 变更**只有新增两张表**，不改动任何既有表：

    roles              角色元数据（key 锁定为代码枚举，不可增删）
    role_permissions   角色 → 权限 的映射行（矩阵存储）

因此没有 `ALTER`、没有回填、不需要停机。做法与 Step 4 的「显式前滚」保持
一致，好处是**部署前能先 dry-run 看清将发生什么**，而不是依赖
``init_db()`` 在启动时顺手把表建出来（那种"静默建表"在生产上不好审计）。

播种（``role_permissions`` 写入代码默认矩阵）**仅在 ``roles`` 表为空时执行**：
幂等，不会覆盖管理员在界面上调过的配置。

用法（只读预演）::

    python scripts/migrate_step3_roles.py --dry-run

用法（真执行）::

    python scripts/migrate_step3_roles.py --yes
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import pkgutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, inspect, select  # noqa: E402

import app.models as _M  # noqa: E402
from app.db import Base, async_session, engine  # noqa: E402

# 全量导入模型，保证 Base.metadata 完整（缺模型会让 create_all 因外键找不到表而报错）
for _m in pkgutil.iter_modules(_M.__path__):
    importlib.import_module(f"app.models.{_m.name}")

NEW_TABLES = ("roles", "role_permissions")


async def inspect_state() -> dict:
    """只读体检：两张新表是否已存在、各自行数、权限目录大小。"""
    from app.models.permission_catalog import ALL_PERMISSION_KEYS

    async with engine.begin() as conn:

        def _tables(sync_conn) -> dict[str, bool]:
            names = set(inspect(sync_conn).get_table_names())
            return {name: name in names for name in NEW_TABLES}

        exists = await conn.run_sync(_tables)

    counts: dict[str, int] = {}
    if all(exists.values()):
        from app.models.role_permission import Role, RolePermission

        async with async_session() as db:
            counts["roles"] = int(
                (await db.execute(select(func.count()).select_from(Role))).scalar() or 0
            )
            counts["role_permissions"] = int(
                (await db.execute(select(func.count()).select_from(RolePermission))).scalar() or 0
            )

    return {
        "exists": exists,
        "counts": counts,
        "catalog_size": len(ALL_PERMISSION_KEYS),
    }


async def migrate() -> dict:
    """建缺失的表 + 播种（幂等）。"""
    from app.services.role_seed import seed_roles_and_permissions

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    result = await seed_roles_and_permissions()

    # 播种后预热矩阵缓存，避免首次请求走代码兜底
    from app.services.permissions import load_matrix

    async with async_session() as db:
        await load_matrix(db)
    return result


def report(state: dict) -> None:
    print("【体检】")
    for name in NEW_TABLES:
        mark = "已存在" if state["exists"][name] else "缺失（将创建）"
        print(f"  - {name:<18} {mark}")
    if state["counts"]:
        print(f"  - roles 行数            {state['counts'].get('roles')}")
        print(f"  - role_permissions 行数 {state['counts'].get('role_permissions')}")
    print(f"  - 权限目录条目          {state['catalog_size']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Step 3 角色权限矩阵迁移")
    parser.add_argument("--dry-run", action="store_true", help="只体检，不写库")
    parser.add_argument("--yes", action="store_true", help="确认执行")
    args = parser.parse_args()

    state = await inspect_state()
    report(state)

    if args.dry_run or not args.yes:
        if not args.dry_run:
            print("\n未加 --yes，仅预演。加 --yes 才会真正执行。")
        return

    print("\n【执行】")
    result = await migrate()
    if result.get("seeded"):
        print(
            f"  ✔ 已播种：角色 {result['roles']} 个、权限映射 {result['permissions']} 条"
        )
    else:
        print(f"  ✔ 已播种过（roles 现有 {result['roles']} 行），未改动")
    if result.get("dropped"):
        print(f"  ⚠ 丢弃的目录外权限键：{result['dropped']}")
    state = await inspect_state()
    print("\n【执行后】")
    report(state)


if __name__ == "__main__":
    asyncio.run(main())
