# -*- coding: utf-8 -*-
"""复现 + 回归：周期文书批处理的 MissingGreenlet 级联崩溃。

跑法：cd cloud-backend && python -m unittest tests.test_period_batch_greenlet -v

🔴 生产实证（2026-10-26 日志，两条相隔 3ms）
--------------------------------------------
    23:59:50,528 ERROR 项目 1 周报生成失败          ← NameError 被内层 except 捕获
    23:59:50,531 WARNING [周期文书] 自动生成异常：greenlet_spawn has not been called
                     [SQL: SELECT projects... WHERE projects.id = %s] [pk_1: 12]

`pk_1: 12` 说明崩在**下一个** project(id=12) 上，且只隔 3ms → 同一轮循环内。

根因链
------
1. 内层 except（周报/日志/月报各一处）执行 `await db.rollback()`；
2. **rollback 会过期 session 内所有 ORM 实例** —— `expire_on_commit=False`
   只影响 commit，对 rollback 无效；
3. 循环进入下一个 project，`pid, pname = project.id, project.name`
   触发**同步懒加载** → 在 async 上下文里抛 MissingGreenlet；
4. 该异常逃到**外层** except，整批中断 → `last_run` 永不置位；
5. 外层 except **没有设 `sleep_for = 300`** → 用默认 60 秒重试
   → 每分钟一遍，从 23:48 刷到次日 08:00（约 8 小时 / 480 次）。

831-835 行的注释已经预见到这个坑并写了防护，但**防护位置错了**：
它缓存 pid/pname 的那一行本身，就是下一个 project 触发懒加载的地方。
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import pkgutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import Base

# 🔴 必须把 app.models 下所有模块都导入，Base.metadata 才完整；
# 否则 create_all 会因外键引用未注册的表而 NoReferencedTableError。
import app.models  # noqa: E402

for _mod in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module("app.models." + _mod.name)

from app.models.project_member import ProjectMember  # noqa: E402
from app.models.user import Project, User  # noqa: E402
from app.services import period_report_service as prs  # noqa: E402

_TMP_ROOT = Path("D:/rokid/.planning/record-field-audit-20261012")


class PeriodBatchGreenletTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=_TMP_ROOT)
        self.old_export, self.old_upload = settings.export_dir, settings.upload_dir
        settings.export_dir = Path(self.tmp.name) / "exports"
        settings.upload_dir = Path(self.tmp.name) / "uploads"
        settings.export_dir.mkdir(parents=True)
        settings.upload_dir.mkdir(parents=True)

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # 🔴 必须用生产同款 async_session（共享内存库），批处理内部靠它开 session
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

        # 两个项目（id 递增），都建成员，确保都能进生成流程
        async with self.factory() as db:
            for i in (1, 12):
                p = Project(name=f"项目{i}", code=f"P{i}")
                db.add(p)
                await db.flush()
                u = User(username=f"u{i}", real_name=f"监理{i}",
                         password_hash="x", role="inspector", project_id=p.id)
                db.add(u)
                await db.flush()
                db.add(ProjectMember(user_id=u.id, project_id=p.id, role="inspector"))
            await db.commit()
        async with self.factory() as db:
            from sqlalchemy import select

            rows = (await db.execute(select(Project).order_by(Project.id))).scalars().all()
            self.project_ids = [p.id for p in rows]

    async def asyncTearDown(self):
        await self.engine.dispose()
        settings.export_dir, settings.upload_dir = self.old_export, self.old_upload
        self.tmp.cleanup()

    async def test_rollback_in_one_project_does_not_kill_the_whole_batch(self):
        """🔴 核心回归：一个项目失败并 rollback 后，后续项目必须仍能处理完。

        改前：第 2 个 project 读 project.id 触发同步懒加载 → MissingGreenlet
              → 整批中断，report["projects"] 记了 2 但两个都没处理完。
        改后：两个项目都被尝试，失败的那个记进 errors，另一个正常完成。

        目标日必须选**周日**：只有 day.weekday()==6 才会走周报分支
        generate_aggregate_doc，才能复现「周报环节抛 NameError → rollback」的
        生产场景（2026-10-26 是周一，不触发周报，errors 会空 → 测不到点子上）。
        """
        day = date(2026, 10, 25)   # 周日 → 触发周报分支
        attempted: list[int] = []

        async def fake_supervision_log(db, project_id, target_date, **kw):
            attempted.append(project_id)
            return {"doc_id": 9000 + project_id, "skipped": False}

        async def fake_aggregate(db, project_id, period, ref=None, **kw):
            # 让第一个项目在「日报之后」的周报环节崩，模拟生产 NameError
            if project_id == self.project_ids[0]:
                raise NameError("name 'ex' is not defined")
            return {"doc_id": 8000 + project_id, "skipped": False}

        with patch.object(prs, "async_session", self.factory), \
             patch.object(prs, "generate_supervision_log", fake_supervision_log), \
             patch.object(prs, "generate_aggregate_doc", fake_aggregate), \
             patch("app.services.weather.daily_weather", _fake_weather):
            report = await prs.generate_daily_batch(target_date=day, force=True)

        self.assertEqual(
            attempted, self.project_ids,
            f"批处理在处理第一个项目失败后中断了：只尝试了 {attempted}，"
            f"应为 {self.project_ids}（rollback 过期 ORM 实例 → MissingGreenlet 级联）",
        )
        # 失败要如实记进 errors，不能被静默吞掉
        self.assertTrue(report["errors"], "失败的项目没记进 report['errors']")
        self.assertTrue(
            any(str(self.project_ids[0]) in e for e in report["errors"]),
            f"errors 里找不到失败项目 {self.project_ids[0]}：{report['errors']}",
        )
        # 第二个项目的日报应正常产出
        self.assertTrue(report["created"], f"成功的项目没进 created：{report}")

    async def test_error_detail_never_masked_by_missing_greenlet(self):
        """原始异常不能被 MissingGreenlet 盖掉（831-835 注释里明确担心的事）。"""
        day = date(2026, 10, 26)

        async def boom(db, project_id, target_date, **kw):
            raise NameError("name 'ex' is not defined")

        with patch.object(prs, "async_session", self.factory), \
             patch.object(prs, "generate_supervision_log", boom), \
             patch("app.services.weather.daily_weather", _fake_weather):
            report = await prs.generate_daily_batch(target_date=day, force=True)

        joined = " ".join(report["errors"])
        self.assertNotIn("greenlet", joined.lower(),
                         f"真正的 NameError 被 MissingGreenlet 掩盖了：{joined[:300]}")
        self.assertEqual(len(report["errors"]), len(self.project_ids),
                         f"每个项目都该有一条错误记录，实际 {len(report['errors'])}")


async def _fake_weather(project, target_date, extras=None):
    return {"weather": "晴", "temp_high": "30", "temp_low": "22",
            "weather_source": "mock", "weather_missing": []}


if __name__ == "__main__":
    unittest.main(verbosity=2)
