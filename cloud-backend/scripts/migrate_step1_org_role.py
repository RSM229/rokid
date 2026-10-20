"""Step 1 前滚迁移：企业锚点 + 公司级角色 + 审计状态 + 角色 key 化。

对应清单：docs/Step1-逐行改动清单-2026-10-10.md §4.1

执行顺序**不可打乱**（§4.1 明确）：
    ① 建默认企业
    ② 回填 org_id      ← 必须在 ⑥ 之前，否则复合唯一退化成「允许重复」
    ③ admin -> org_role='super_admin'
    ④ 存量账号 audit_status='approved'
    ⑤ 三表角色中文 -> 英文 key
    ⑥ 唯一约束改造（DROP + ADD）

用法（**只读预演，不改数据**）::

    python scripts/migrate_step1_org_role.py --dry-run

用法（真执行）::

    python scripts/migrate_step1_org_role.py --yes

⚠️ 生产执行前必须：mysqldump 全量备份 + 停机窗口（⑥ 要 ALTER TABLE）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

DEFAULT_ORG_CODE = "DEFAULT_ORG"
DEFAULT_ORG_NAME = "（默认企业）"

# 中文 -> 英文 key（与 app/models/user.py 的 UserRole 值逐字对应）
ROLE_FORWARD: dict[str, str] = {
    "总监理工程师": "chief_supervisor",
    "总监代表": "deputy_chief",
    "专业监理工程师": "special_supervisor",
    "监理员": "inspector",
}

ROLE_TABLES = ("users", "project_members", "invite_codes")


async def _scalar(conn, sql: str, **params):
    result = await conn.execute(text(sql), params)
    return result.scalar()


async def _column_exists(conn, table: str, column: str) -> bool:
    n = await _scalar(
        conn,
        """
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c
        """,
        t=table,
        c=column,
    )
    return bool(n)


async def _index_exists(conn, table: str, index_name: str) -> bool:
    n = await _scalar(
        conn,
        """
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i
        """,
        t=table,
        i=index_name,
    )
    return bool(n)


async def preflight(conn) -> list[str]:
    """前置检查，返回问题列表（空 = 可执行）。"""
    problems: list[str] = []

    # 新列必须已由 init_db() 补齐（本脚本不负责建列）
    for table, column in (
        ("users", "org_id"),
        ("users", "org_role"),
        ("users", "audit_status"),
        ("projects", "org_id"),
        ("projects", "status"),
    ):
        if not await _column_exists(conn, table, column):
            problems.append(
                f"[建列缺失] {table}.{column} 不存在 —— 请先启动一次后端让 init_db() 补齐，再跑本脚本"
            )

    if not await _column_exists(conn, "project_members", "left_at"):
        problems.append(
            "[建列缺失] project_members.left_at 不存在 —— 请先启动一次后端让 init_db() 补齐"
        )

    # 旧单列唯一索引必须还在（否则 ⑥ 的 DROP 会失败）
    # 实际索引名由 SQLAlchemy 生成（ix_<表>_<列>），不是 "username"
    if not await _index_exists(conn, "users", "ix_users_username"):
        problems.append(
            "[约束异常] users 上找不到名为 ix_users_username 的唯一索引 —— 可能已迁移过，请核对后再执行"
        )
    if not await _index_exists(conn, "projects", "code"):
        problems.append(
            "[约束异常] projects 上找不到名为 code 的唯一索引 —— 可能已迁移过，请核对后再执行"
        )

    # 复合唯一不应已存在
    if await _index_exists(conn, "users", "uq_user_org_username"):
        problems.append("[约束异常] uq_user_org_username 已存在 —— 疑似重复执行本脚本")
    if await _index_exists(conn, "projects", "uq_project_org_code"):
        problems.append("[约束异常] uq_project_org_code 已存在 —— 疑似重复执行本脚本")

    return problems


async def forward(dry_run: bool) -> int:
    async with engine.begin() as conn:
        problems = await preflight(conn)
        if problems:
            print("✗ 前置检查未通过，已中止（未改任何数据）：")
            for p in problems:
                print(f"   - {p}")
            return 2

        # ---------- 统计（回显用） ----------
        n_users = await _scalar(conn, "SELECT COUNT(*) FROM users")
        n_projects = await _scalar(conn, "SELECT COUNT(*) FROM projects")
        n_admin = await _scalar(
            conn, "SELECT COUNT(*) FROM users WHERE username = 'admin'"
        )
        print(f"现状：users={n_users}  projects={n_projects}  username='admin' 命中={n_admin}")

        role_counts: dict[str, int] = {}
        for table in ROLE_TABLES:
            rows = await conn.execute(
                text(f"SELECT role, COUNT(*) AS n FROM {table} GROUP BY role")
            )
            for role, n in rows:
                role_counts[f"{table}.{role}"] = n
        print("角色分布（前滚前）：")
        for k, v in sorted(role_counts.items()):
            print(f"   {k} = {v}")

        if dry_run:
            print("\n[dry-run] 未执行任何写入。上述为当前实际分布。")
            print("[dry-run] 预演结论：前置检查通过，可按 ①②③④⑤⑥ 顺序执行。")
            return 0

        # ---------- ① 建默认企业 ----------
        org_id = await _scalar(
            conn, "SELECT id FROM organizations WHERE code = :c", c=DEFAULT_ORG_CODE
        )
        if org_id is None:
            await conn.execute(
                text(
                    "INSERT INTO organizations (name, code, is_active, created_at) "
                    "VALUES (:n, :c, 1, NOW())"
                ),
                {"n": DEFAULT_ORG_NAME, "c": DEFAULT_ORG_CODE},
            )
            org_id = await _scalar(
                conn, "SELECT id FROM organizations WHERE code = :c", c=DEFAULT_ORG_CODE
            )
            print(f"① 已建默认企业 id={org_id}")
        else:
            print(f"① 默认企业已存在 id={org_id}（跳过）")

        # ---------- ② 回填 org_id（必须在 ⑥ 之前） ----------
        r1 = await conn.execute(
            text("UPDATE users SET org_id = :o WHERE org_id IS NULL"), {"o": org_id}
        )
        r2 = await conn.execute(
            text("UPDATE projects SET org_id = :o WHERE org_id IS NULL"), {"o": org_id}
        )
        print(f"② 回填 org_id：users {r1.rowcount} 行、projects {r2.rowcount} 行")

        # ---------- ③ 超管判据迁移 ----------
        r3 = await conn.execute(
            text(
                "UPDATE users SET org_role = 'super_admin' "
                "WHERE username = 'admin' AND (org_role IS NULL OR org_role <> 'super_admin')"
            )
        )
        print(f"③ admin -> org_role='super_admin'：{r3.rowcount} 行")

        # ---------- ④ 存量账号一律 approved（回归底线） ----------
        r4 = await conn.execute(
            text(
                "UPDATE users SET audit_status = 'approved' "
                "WHERE audit_status IS NULL OR audit_status <> 'approved'"
            )
        )
        print(f"④ audit_status='approved'：{r4.rowcount} 行")

        # ---------- ⑤ 角色中文 -> 英文 key ----------
        for table in ROLE_TABLES:
            if not await _column_exists(conn, table, "role"):
                print(f"⑤ {table}.role 不存在，跳过")
                continue
            changed = 0
            for zh, en in ROLE_FORWARD.items():
                res = await conn.execute(
                    text(f"UPDATE {table} SET role = :en WHERE role = :zh"),
                    {"en": en, "zh": zh},
                )
                changed += res.rowcount
            print(f"⑤ {table}：{changed} 行角色值已 key 化")

        # ---------- ⑥ 唯一约束改造（顺序敏感） ----------
        await conn.execute(text("ALTER TABLE users DROP INDEX ix_users_username"))
        await conn.execute(
            text("ALTER TABLE users ADD UNIQUE KEY uq_user_org_username (org_id, username)")
        )
        await conn.execute(text("ALTER TABLE projects DROP INDEX code"))
        await conn.execute(
            text("ALTER TABLE projects ADD UNIQUE KEY uq_project_org_code (org_id, code)")
        )
        print("⑥ 唯一约束已改造：users(org_id,username)、projects(org_id,code)")

    # ---------- 后置校验 ----------
    async with engine.connect() as conn:
        residual = []
        for table in ROLE_TABLES:
            rows = await conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {table} WHERE role IN "
                    "('总监理工程师','总监代表','专业监理工程师','监理员')"
                )
            )
            n = rows.scalar()
            if n:
                residual.append(f"{table}: {n}")
        null_org = (await conn.execute(text("SELECT COUNT(*) FROM users WHERE org_id IS NULL"))).scalar()
        null_audit = (
            await conn.execute(text("SELECT COUNT(*) FROM users WHERE audit_status IS NULL"))
        ).scalar()

    print("\n后置校验：")
    print(f"   中文角色残留：{'无' if not residual else ', '.join(residual)}")
    print(f"   users.org_id 为 NULL：{null_org}")
    print(f"   users.audit_status 为 NULL：{null_audit}")
    if residual or null_org or null_audit:
        print("✗ 后置校验未通过")
        return 1
    print("✓ Step 1 前滚完成")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 1 前滚迁移（企业锚点 + 角色 key 化）")
    parser.add_argument("--dry-run", action="store_true", help="只读预演，不改任何数据")
    parser.add_argument("--yes", action="store_true", help="确认真执行写入")
    args = parser.parse_args()

    if not args.dry_run and not args.yes:
        print("拒绝执行：请显式加 --dry-run（预演）或 --yes（真执行）。")
        return 2

    return asyncio.run(forward(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
