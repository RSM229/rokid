"""Step 4 前滚迁移：审批字段 + 邀请码企业归属（共 4 列 + 1 索引）。

对应清单：docs/Step4-逐行改动清单-2026-10-10.md §1.1

**2026-10-10 生产只读预检实测**：
  - ``invite_codes.org_id``                    ❌ 缺（A-1 唯一新增字段）
  - ``users.approved_by/approved_at/reject_reason`` ❌ 缺
    （Step 1 只把 ``audit_status`` 落到了库里，这三个审批列当时漏执行；
      代码模型已有，缺列会导致**批准接口 500**，故本轮必须补）
  - ``users.audit_status`` ✅ 存在，默认 ``approved``，存量 5 账号全 approved → 登录回归安全
  - ``users.org_id`` / ``org_role`` ✅ 存在

全部为 **nullable / 有默认值** 的列 → 安全 ADD COLUMN，无需停机、无需回填。
（``nullable`` 的列理论上 ``init_db()`` 重启时也会自动补，但显式前滚更可控，
且能保证索引存在。）

用法（只读预演）::

    python scripts/migrate_step4_invite_org.py --dry-run

用法（真执行）::

    python scripts/migrate_step4_invite_org.py --yes
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
        "invite_codes", "org_id",
        "invite_codes ADD COLUMN org_id INT NULL "
        "COMMENT '企业归属（Step 4 A-1：码管企业，审批管项目）'",
        "邀请码企业归属",
    ),
    (
        "users", "approved_by",
        "users ADD COLUMN approved_by INT NULL COMMENT '审批人 user_id'",
        "审批人",
    ),
    (
        "users", "approved_at",
        "users ADD COLUMN approved_at DATETIME NULL COMMENT '审批时间'",
        "审批时间",
    ),
    (
        "users", "reject_reason",
        "users ADD COLUMN reject_reason VARCHAR(256) NULL COMMENT '拒绝理由'",
        "拒绝理由",
    ),
]

INDEXES: list[tuple[str, str, str]] = [
    ("invite_codes", "ix_invite_codes_org_id", "invite_codes ADD INDEX ix_invite_codes_org_id (org_id)"),
]


async def _col_exists(conn, table: str, column: str) -> bool:
    row = (await conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table, "c": column})).scalar()
    return bool(row)


async def _index_exists(conn, table: str, index: str) -> bool:
    row = (await conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
    ), {"t": table, "i": index})).scalar()
    return bool(row)


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
        for table, idx, alter_sql in INDEXES:
            exists = await _index_exists(conn, table, idx)
            print(f"  {'✅' if exists else '❌'} 索引 {idx}")
            if not exists:
                todo.append(f"ALTER TABLE {alter_sql}")

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
        for table, idx, _ in INDEXES:
            ok = await _index_exists(conn, table, idx)
            print(f"  {'✅' if ok else '❌'} 索引 {idx}")


if __name__ == "__main__":
    asyncio.run(main())
