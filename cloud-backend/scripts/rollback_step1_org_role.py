"""Step 1 回退迁移：撤销企业锚点 / 公司级角色 / 审计状态 / 角色 key 化。

对应清单：docs/Step1-逐行改动清单-2026-10-10.md §4.2

⚠️ **存在一个不可逆点**：若 `users.role`（及 `project_members.role` /
`invite_codes.role`）已出现**新岗位**英文值 ——
`documenter` / `witness` / `safety_supervisor` —— 反向 CASE 映射不到中文，
会被静默保留为英文脏数据。

因此本脚本**先检查这些值的存在性并报错中止**（清单 §4.2 明确要求），
而不是"尽力而为地回退"。要强制回退须显式加 `--force`，届时这些值会原样保留。

用法（只读预演）::

    python scripts/rollback_step1_org_role.py --dry-run

用法（真执行，遇到新岗位会中止）::

    python scripts/rollback_step1_org_role.py --yes

用法（强执行，放弃新岗位数据的一致性）::

    python scripts/rollback_step1_org_role.py --yes --force
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

# 英文 key -> 中文（与 ROLE_FORWARD 严格互逆）
ROLE_BACKWARD: dict[str, str] = {
    "chief_supervisor": "总监理工程师",
    "deputy_chief": "总监代表",
    "special_supervisor": "专业监理工程师",
    "inspector": "监理员",
}

# 立表新增但**无中文对应**的岗位 —— 反向映射的死角
NEW_ROLES = ("documenter", "witness", "safety_supervisor")

ROLE_TABLES = ("users", "project_members", "invite_codes")


async def _scalar(conn, sql: str, **params):
    result = await conn.execute(text(sql), params)
    return result.scalar()


async def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        await _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c
            """,
            t=table,
            c=column,
        )
    )


async def _index_exists(conn, table: str, index_name: str) -> bool:
    return bool(
        await _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i
            """,
            t=table,
            i=index_name,
        )
    )


async def find_blockers(conn) -> list[str]:
    """不可逆点检查：新岗位英文值 + 复合唯一下会冲突的重复数据。"""
    blockers: list[str] = []

    values_sql = "(" + ",".join(f"'{r}'" for r in NEW_ROLES) + ")"
    for table in ROLE_TABLES:
        if not await _column_exists(conn, table, "role"):
            continue
        rows = await conn.execute(
            text(f"SELECT role, COUNT(*) FROM {table} WHERE role IN {values_sql} GROUP BY role")
        )
        for role, n in rows:
            blockers.append(f"[不可逆] {table}.role='{role}' 有 {n} 行 —— 无中文对应，回退后会残留英文")

    # 复合唯一回退成单列唯一后可能撞车（同 org 内重名）
    if await _column_exists(conn, "users", "org_id"):
        dup = await conn.execute(
            text(
                "SELECT username, COUNT(*) c FROM users GROUP BY username HAVING c > 1 LIMIT 5"
            )
        )
        for username, c in dup:
            blockers.append(
                f"[冲突] users.username='{username}' 出现 {c} 次 —— 回退成单列唯一会失败"
            )
    if await _column_exists(conn, "projects", "org_id"):
        dup = await conn.execute(
            text("SELECT code, COUNT(*) c FROM projects GROUP BY code HAVING c > 1 LIMIT 5")
        )
        for code, c in dup:
            blockers.append(
                f"[冲突] projects.code='{code}' 出现 {c} 次 —— 回退成单列唯一会失败"
            )

    return blockers


async def backward(dry_run: bool, force: bool) -> int:
    async with engine.begin() as conn:
        blockers = await find_blockers(conn)
        if blockers:
            print("✗ 回退前置检查未通过：")
            for b in blockers:
                print(f"   - {b}")
            if not force:
                print("\n已中止（未改任何数据）。确认要放弃这些数据的一致性，请加 --force。")
                return 3
            print("\n⚠️ --force 已开启：上述新岗位值将**原样保留为英文**。")

        # 约束状态核对
        if not await _index_exists(conn, "users", "uq_user_org_username"):
            print("✗ users.uq_user_org_username 不存在 —— 疑似未迁移过，中止。")
            return 2
        if not await _index_exists(conn, "projects", "uq_project_org_code"):
            print("✗ projects.uq_project_org_code 不存在 —— 疑似未迁移过，中止。")
            return 2

        if dry_run:
            print("\n[dry-run] 未执行任何写入。")
            print("[dry-run] 结论：可按 ①反向角色 ②约束回滚 ③清空新字段 顺序执行。")
            return 0

        # ---------- ① 角色英文 -> 中文 ----------
        for table in ROLE_TABLES:
            if not await _column_exists(conn, table, "role"):
                continue
            changed = 0
            for en, zh in ROLE_BACKWARD.items():
                res = await conn.execute(
                    text(f"UPDATE {table} SET role = :zh WHERE role = :en"),
                    {"zh": zh, "en": en},
                )
                changed += res.rowcount
            print(f"① {table}：{changed} 行角色值已回中文")

        # ---------- ② 唯一约束回滚（先删复合、再建单列） ----------
        await conn.execute(text("ALTER TABLE users DROP INDEX uq_user_org_username"))
        await conn.execute(text("ALTER TABLE users ADD UNIQUE KEY ix_users_username (username)"))
        await conn.execute(text("ALTER TABLE projects DROP INDEX uq_project_org_code"))
        await conn.execute(text("ALTER TABLE projects ADD UNIQUE KEY code (code)"))
        print("② 唯一约束已回滚：users(username)、projects(code)")

        # ---------- ③ 清空新字段 ----------
        r = await conn.execute(
            text(
                "UPDATE users SET org_role = NULL, org_id = NULL, audit_status = 'approved'"
            )
        )
        print(f"③ 已清空 users.org_role / org_id（{r.rowcount} 行），audit_status 归位 approved")
        rp = await conn.execute(text("UPDATE projects SET org_id = NULL"))
        print(f"   已清空 projects.org_id（{rp.rowcount} 行）")

    print("\n✓ Step 1 回退完成")
    print("   提示：organizations 表与 project_members.left_at 列保留不动（无害）。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 1 回退迁移")
    parser.add_argument("--dry-run", action="store_true", help="只读预演，不改任何数据")
    parser.add_argument("--yes", action="store_true", help="确认真执行写入")
    parser.add_argument("--force", action="store_true", help="忽略不可逆点，强制回退")
    args = parser.parse_args()

    if not args.dry_run and not args.yes:
        print("拒绝执行：请显式加 --dry-run（预演）或 --yes（真执行）。")
        return 2

    return asyncio.run(backward(dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    raise SystemExit(main())
