# -*- coding: utf-8 -*-
"""复现 + 回归：批量导入账号在「某行失败 rollback」后崩溃。

跑法：cd cloud-backend && python -m unittest tests.test_batch_import_rollback -v

🔴 缺陷与周期文书那个同构（2026-10-27 同一天发现）
--------------------------------------------------
`POST /api/auth/users/batch-import` 的 docstring 承诺：
    「逐行独立事务：某行失败不影响其它行，返回逐行明细」

实际实现（auth.py）：
    for item in rows:
        ...
        project_id = manager.project_id          # ← 每行都重读 ORM 属性
        matched[0].id / p.name / p.code          # ← all_projects 里的 ORM 实例
        try:
            db.add(account); await db.flush(); await db.commit()
        except Exception:
            await db.rollback()                  # ← 过期 session 内**全部** ORM 实例
            continue
        # 下一轮再读 manager.project_id → 同步懒加载 → MissingGreenlet

`manager` 来自 `Depends(require_permission(...))` → `get_current_user(db)` →
`get_user_from_token(token, db)`，与端点的 `db` 是**同一个 session**
（FastAPI 每请求缓存 Depends(get_db)），所以 rollback 必然过期 `manager`；
`expire_on_commit=False` 只影响 commit，对 rollback 无效。

后果：第 1 行失败后，第 2 行不是「如实记录失败原因」，而是整个请求 500，
且真正的失败原因被 MissingGreenlet 盖掉 —— 与 docstring 承诺相反。
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import Base

import app.models  # noqa: E402

for _mod in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module("app.models." + _mod.name)

from app.models.project import Project  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402


def _row(line: int, name: str, username: str, project: str = "") -> dict:
    return {
        "line": line, "real_name": name, "username": username, "phone": "",
        "role": "监理员", "org_role": "", "project": project, "password": "",
    }


class _FakeUploadFile:
    """只被 file.read()/file.filename 用到，内容会被 _parse_import_rows 替身忽略。"""
    filename = "import.xlsx"

    async def read(self) -> bytes:
        return b"stub"


class BatchImportRollbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.factory() as db:
            p = Project(name="回归项目", code="R1")
            db.add(p)
            await db.flush()
            self.project_id = p.id
            self.manager = User(username="boss", real_name="管理员",
                                password_hash="x", role="inspector",
                                project_id=p.id, org_role="")
            db.add(self.manager)
            await db.commit()
            self.manager_id = self.manager.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _run_batch(self, rows, commit_side_effect=None):
        async with self.factory() as db:
            manager = await db.get(User, self.manager_id)
            real_commit = db.commit
            calls = {"n": 0}

            async def flaky_commit():
                calls["n"] += 1
                if commit_side_effect and calls["n"] <= len(commit_side_effect):
                    exc = commit_side_effect[calls["n"] - 1]
                    if exc is not None:
                        raise exc
                return await real_commit()

            with patch.object(auth_router, "_parse_import_rows", return_value=rows), \
                 patch.object(db, "commit", flaky_commit):
                return await auth_router.batch_import_users(
                    file=_FakeUploadFile(),
                    default_password="Passw0rd!",
                    dry_run=False,
                    manager=manager,
                    db=db,
                )

    async def test_second_row_still_processed_after_first_row_rollback(self):
        """🔴 核心：第 1 行写库失败 rollback 后，第 2 行必须仍被正常处理。

        改前：第 2 行读 manager.project_id 触发同步懒加载 → MissingGreenlet
              从端点抛出，整个导入请求 500，rows 明细全丢。
        改后：第 1 行记 error、第 2 行记 ok，明细完整返回。
        """
        rows = [_row(2, "张三", "zhangsan"), _row(3, "李四", "lisi")]
        first_fail = IntegrityError("INSERT", {}, Exception("duplicate key"))

        try:
            report = await self._run_batch(rows, commit_side_effect=[first_fail, None])
        except Exception as exc:  # pragma: no cover - 改前会走到这里
            self.fail(
                f"第 1 行失败后整个请求崩了（第 2 行没被处理）：{type(exc).__name__}: {exc}\n"
                "→ 典型是 rollback 过期 ORM 实例后读 manager.project_id 触发 MissingGreenlet"
            )

        self.assertEqual(report["total"], 2)
        self.assertEqual(report["failed"], 1, f"第 1 行应记失败：{report['rows']}")
        self.assertEqual(report["created"], 1, f"第 2 行应成功创建：{report['rows']}")
        statuses = {r["username"]: r["status"] for r in report["rows"]}
        self.assertEqual(statuses.get("zhangsan"), "error", statuses)
        self.assertEqual(statuses.get("lisi"), "ok", statuses)
        # 真正的失败原因不能被 greenlet 盖掉
        detail = next(r["detail"] for r in report["rows"] if r["username"] == "zhangsan")
        self.assertNotIn("greenlet", detail.lower(), f"失败原因被掩盖：{detail}")

    async def test_project_column_match_survives_earlier_rollback(self):
        """🔴 all_projects 里的 ORM 实例（p.name/p.code/matched[0].id）同样会被
        rollback 过期；填了「所属项目」列的行在先前某行失败后也必须能匹配。"""
        rows = [_row(2, "张三", "zhangsan", "回归项目"),
                _row(3, "李四", "lisi", "回归项目")]
        first_fail = IntegrityError("INSERT", {}, Exception("duplicate key"))

        try:
            report = await self._run_batch(rows, commit_side_effect=[first_fail, None])
        except Exception as exc:  # pragma: no cover
            self.fail(f"填了项目列的批量导入在 rollback 后崩了：{type(exc).__name__}: {exc}")

        self.assertEqual(report["created"], 1, report["rows"])
        self.assertEqual(report["failed"], 1, report["rows"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
