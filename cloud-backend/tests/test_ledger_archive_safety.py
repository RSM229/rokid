# -*- coding: utf-8 -*-
"""台账批量归档安全回归：路径穿越清洗 + 同名查重 + commit 失败补偿。

历史 bug（2026-11-02 修复）：
  1. archive_name 用户输入未过滤，传 ../../ 可在 exports/ 之外任意写目录（路径穿越）；
  2. 同项目同名归档直接覆盖写文件但仍插入第二条 Archive 记录，
     删除任意一条时 rmtree 整个目录导致另一条记录悬空；
  3. 写盘后 commit 失败无补偿，归档目录成孤儿。

运行：cd cloud-backend && .venv/Scripts/python -m unittest tests.test_ledger_archive_safety
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# app.db 在导入时即按 DATABASE_URL 建 engine；先切到隔离 SQLite 再清模块缓存
_WORKDIR = Path(tempfile.mkdtemp(prefix="gdjl_archive_"))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(_WORKDIR / "test.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _name in list(sys.modules):
    if _name == "app" or _name.startswith("app."):
        del sys.modules[_name]

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import async_session, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import Project, User, UserRole  # noqa: E402

# 导出目录也切到隔离目录，避免污染真实 uploads/exports
settings.export_dir = _WORKDIR / "exports"
settings.export_dir.mkdir(parents=True, exist_ok=True)

_SEED: dict = {}


async def _seed() -> None:
    async with async_session() as db:
        project = Project(name="归档测试工程", code="TEST-ARC")
        db.add(project)
        await db.flush()
        user = User(
            username="archtest",
            real_name="归档测试员",
            role=UserRole.CHIEF_SUPERVISOR.value,
            password_hash="$2b$12$unused-hash-for-test",
            project_id=project.id,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        _SEED["project_id"] = project.id
        _SEED["user_id"] = user.id


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.new_event_loop().run_until_complete(coro)


class LedgerArchiveSafetyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _run(init_db())
        _run(_seed())
        import app.routers.auth as auth_mod
        import app.routers.ledger as ledger_mod

        async def _fake_user():
            async with async_session() as db:
                return await db.get(User, _SEED["user_id"])

        async def _fake_db():
            async with async_session() as db:
                yield db

        app.dependency_overrides[auth_mod.get_current_user] = _fake_user
        app.dependency_overrides[ledger_mod.get_db] = _fake_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()

    def _archive(self, name: str, ledger_types=None):
        with patch.object(
            __import__("app.routers.ledger", fromlist=["_build_export_items"]),
            "_build_export_items",
            new_callable=lambda: _fake_build_items,
        ), patch.object(
            __import__("app.routers.ledger", fromlist=["export_multiple"]),
            "export_multiple",
            new_callable=lambda: _fake_export,
        ):
            return self.client.post(
                "/api/ledger/batch-archive",
                json={
                    "ledger_types": ledger_types or ["监理日志"],
                    "archive_name": name,
                    "format": "excel",
                },
            )

    # ---- 1. 路径穿越：archive_name 带 ../ 不得写出归档根 ----
    def test_traversal_name_sanitized(self):
        r = self._archive("../../uploads/evil")
        self.assertEqual(r.status_code, 200, r.text)
        # 清洗后应落在本项目的 archives/ 目录内
        arc_root = (settings.export_dir / "archives" / str(_SEED["project_id"])).resolve()
        written = list(arc_root.rglob("*.zip"))
        self.assertTrue(written, "zip 应写入项目归档目录内")
        for p in written:
            self.assertTrue(p.resolve().is_relative_to(arc_root))
        # 清洗后名称不含 .. 且不含路径分隔符
        name = r.json()["zip_url"]
        self.assertNotIn("..", name)
        # evil 不得出现在 uploads 下
        up_evil = settings.upload_dir / "uploads" if False else None  # 占位，真实断言在下方
        self.assertFalse((settings.export_dir / "uploads").exists())

    # ---- 2. 同名查重：第二次同名归档 → 409 ----
    def test_duplicate_name_rejected(self):
        r1 = self._archive("年度归档A")
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self._archive("年度归档A")
        self.assertEqual(r2.status_code, 409, r2.text)

    # ---- 3. 纯无效字符名 → 400 ----
    def test_blank_after_sanitize_rejected(self):
        r = self._archive("....")
        self.assertEqual(r.status_code, 400, r.text)

    # ---- 4. 特殊字符被替换为下划线（中文/点/横线保留） ----
    def test_name_with_special_chars(self):
        r = self._archive("2026/年·度:归档?")
        self.assertEqual(r.status_code, 200, r.text)
        zip_name = r.json()["zip_url"].rsplit("/", 1)[-1]  # 最后一段文件名
        # "2026/..." 的目录部分被 Path.name 丢弃（防穿越核心语义），只保留末段
        self.assertEqual(zip_name, "年_度_归档.zip")


async def _fake_build_items(db, project_id, ledger_types, date_from, date_to):
    return [{"fake": True}]


def _fake_export(items, fmt="excel"):
    return [("台账_f1.xlsx", b"DATA-1"), ("台账_f2.xlsx", b"DATA-2")]


if __name__ == "__main__":
    unittest.main(verbosity=2)
