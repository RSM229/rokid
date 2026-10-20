"""Step 5 前滚迁移：项目生命周期字段 + 数据体检（1 列 + 只读统计）。

对应设计：docs/用户体系结构设计说明-2026-10-09.md §7.7④⑤ / §8.4 Step 5

**本轮唯一的 schema 变更**：
  - ``projects.completed_at`` DATETIME NULL —— 结项时间（转场留痕的终点）。

``projects.status`` / ``project_members.left_at`` 属 Step 1 已落地字段，
本脚本只做存在性复核（缺失则一并补，幂等）。

``completed_at`` 是 **nullable** 列，理论上 ``init_db()`` 重启时也会自动补
（``_reconcile_missing_columns`` 只跳过"非空且无默认值"的列）。这里显式前滚
是为了：① 可控、可预演；② 顺带输出数据体检报告，便于部署前判断风险。

数据体检（只读）：
  - 项目状态分布（active/completed/archived 各多少）
  - 成员行分布（在岗 ``left_at IS NULL`` / 已离场）
  - 有无"上下文仍停在非 active 项目"的用户（结项后不应存在）

用法（只读预演）::

    python scripts/migrate_step5_lifecycle.py --dry-run

用法（真执行）::

    python scripts/migrate_step5_lifecycle.py --yes
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

# (表, 列, 完整 ALTER 片段, 说明)
COLUMNS: list[tuple[str, str, str, str]] = [
    (
        "projects", "completed_at",
        "projects ADD COLUMN completed_at DATETIME NULL COMMENT '结项时间（NULL=未结项）'",
        "项目结项时间（Step 5 唯一新增列）",
    ),
    (
        "projects", "status",
        "projects ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active' "
        "COMMENT '项目状态: active|completed|archived'",
        "项目状态（Step 1 字段，此处仅复核）",
    ),
    (
        "project_members", "left_at",
        "project_members ADD COLUMN left_at DATETIME NULL COMMENT '离场时间（NULL=在岗）'",
        "成员离场时间（Step 1 字段，此处仅复核）",
    ),
]


async def _col_exists(conn, table: str, column: str) -> bool:
    row = (await conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table, "c": column})).scalar()
    return bool(row)


async def _health_report(conn) -> None:
    """只读数据体检（迁移前后各跑一次，用于判断现状）。"""
    print("=== 数据体检（只读） ===")

    rows = (await conn.execute(text(
        "SELECT COALESCE(status, '(NULL)') AS s, COUNT(*) AS n "
        "FROM projects GROUP BY COALESCE(status, '(NULL)')"
    ))).fetchall()
    print("  项目状态分布:", ", ".join(f"{s}={n}" for s, n in rows) or "（无项目）")

    row = (await conn.execute(text(
        "SELECT "
        "  SUM(left_at IS NULL) AS present, "
        "  SUM(left_at IS NOT NULL) AS left_, "
        "  COUNT(*) AS total FROM project_members"
    ))).fetchone()
    if row and row.total:
        print(f"  成员行：在岗 {row.present} / 已离场 {row.left_} / 合计 {row.total}")
    else:
        print("  成员行：0")

    # 上下文停在已结项项目的用户（结项动作应清空；若 >0 说明有待收口）
    row = (await conn.execute(text(
        "SELECT COUNT(*) FROM users u JOIN projects p ON p.id = u.project_id "
        "WHERE COALESCE(p.status, 'active') <> 'active'"
    ))).scalar()
    print(f"  上下文停在非进行中项目的用户：{row}（应为 0）")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的语句")
    parser.add_argument("--yes", action="store_true", help="真执行")
    args = parser.parse_args()

    async with engine.begin() as conn:
        print("=== 预检（只读） ===")
        todo: list[str] = []
        for table, col, alter_sql, label in COLUMNS:
            exists = await _col_exists(conn, table, col)
            print(f"  {'✅' if exists else '❌'} {table}.{col}  ({label})")
            if not exists:
                todo.append(f"ALTER TABLE {alter_sql}")

        print()
        await _health_report(conn)

        if not todo:
            print("\n✅ 已是目标状态，无需变更")
            return

        print(f"\n=== 待执行 {len(todo)} 条 ===")
        for s in todo:
            print("  ", s)

        if args.dry_run or not args.yes:
            print("\n（未执行。加 --yes 真执行）")
            return

        for s in todo:
            await conn.execute(text(s))
        print("\n✅ 迁移执行完成")

        print("\n=== 复核 ===")
        for table, col, _, _ in COLUMNS:
            ok = await _col_exists(conn, table, col)
            print(f"  {'✅' if ok else '❌'} {table}.{col}")
        print()
        await _health_report(conn)


if __name__ == "__main__":
    asyncio.run(main())
