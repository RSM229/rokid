"""Agent 异步任务状态机的单元测试（内存 SQLite，不碰真实库）。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_task import AgentAsyncTask
from app.services import agent_task_registry as reg


class TaskRegistryTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: AgentAsyncTask.__table__.create(sync_conn, checkfirst=True)
            )
        self.patcher = patch.object(reg, "async_session", self.Session)
        self.patcher.start()

    async def asyncTearDown(self):
        self.patcher.stop()
        await self.engine.dispose()


class TestTransitions(unittest.TestCase):
    def test_legal_transitions(self):
        self.assertTrue(reg.can_transition("queued", "running"))
        self.assertTrue(reg.can_transition("running", "done"))
        self.assertTrue(reg.can_transition("failed", "queued"))
        self.assertTrue(reg.can_transition("running", "timed_out"))

    def test_terminal_states_are_final(self):
        for src in ("done", "failed", "cancelled", "timed_out"):
            self.assertFalse(reg.can_transition(src, "running"), src)
        self.assertFalse(reg.can_transition("done", "queued"))

    def test_retry_only_from_failure_states(self):
        self.assertFalse(reg.can_transition("queued", "queued"))
        self.assertFalse(reg.can_transition("done", "queued"))
        for src in ("failed", "cancelled", "timed_out"):
            self.assertTrue(reg.can_transition(src, "queued"), src)


class TestLifecycle(TaskRegistryTestBase):
    async def test_full_lifecycle(self):
        key = await reg.create_task(user_id=7, request={"message": "记一下"},
                                    conversation_id="c1")
        self.assertIsNotNone(key)
        item = await reg.get_task(key, 7)
        self.assertEqual(item["status"], "queued")
        self.assertTrue(await reg.mark_running(key))
        self.assertEqual((await reg.get_task(key, 7))["status"], "running")
        self.assertTrue(await reg.finish_task(key, {"reply": "已记录"}))
        done = await reg.get_task(key, 7)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"], {"reply": "已记录"})
        # 终态不可覆盖
        self.assertFalse(await reg.finish_task(key, {"reply": "第二次"}))
        self.assertEqual((await reg.get_task(key, 7))["result"], {"reply": "已记录"})

    async def test_fail_path(self):
        key = await reg.create_task(user_id=7, request={})
        await reg.mark_running(key)
        self.assertTrue(await reg.fail_task(key, "模型超时"))
        item = await reg.get_task(key, 7)
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["error"], "模型超时")

    async def test_cancel_then_retry_returns_original_request(self):
        key = await reg.create_task(user_id=7, request={"message": "原始请求"})
        ok, reason = await reg.cancel_task(key, 7)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertEqual((await reg.get_task(key, 7))["status"], "cancelled")
        ok, reason, replay = await reg.retry_task(key, 7)
        self.assertTrue(ok)
        self.assertEqual(replay, {"message": "原始请求"})
        item = await reg.get_task(key, 7)
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["retry_count"], 1)
        self.assertIsNone(item["result"])

    async def test_cancel_finished_rejected_and_wrong_user(self):
        key = await reg.create_task(user_id=7, request={})
        await reg.mark_running(key)
        await reg.finish_task(key, {})
        ok, reason = await reg.cancel_task(key, 7)
        self.assertFalse(ok)
        self.assertEqual(reason, "already_finished")
        ok, reason = await reg.cancel_task(key, 999)
        self.assertFalse(ok)
        self.assertEqual(reason, "not_found")
        self.assertIsNone(await reg.get_task(key, 999))

    async def test_sweep_stale_marks_timed_out(self):
        key = await reg.create_task(user_id=7, request={})
        await reg.mark_running(key)
        # 把租约拨到过去，模拟进程重启后没人续租
        async with self.Session() as db:
            row = await db.get(AgentAsyncTask, 1)
            row.lease_until = datetime.now() - timedelta(seconds=5)
            await db.commit()
        keys = await reg.sweep_stale()
        self.assertIn(key, keys)
        item = await reg.get_task(key, 7)
        self.assertEqual(item["status"], "timed_out")
        self.assertIn("超时", item["error"])
        self.assertEqual(await reg.sweep_stale(), [])

    async def test_list_scoped_to_user(self):
        await reg.create_task(user_id=7, request={})
        await reg.create_task(user_id=8, request={})
        await reg.create_task(user_id=7, request={})
        items = await reg.list_tasks(7, 20)
        self.assertEqual(len(items), 2)
        self.assertEqual(len(await reg.list_tasks(8, 20)), 1)

    async def test_create_failure_degrades_without_raising(self):
        class Broken:
            def __call__(self):
                raise RuntimeError("DB down")

        with patch.object(reg, "async_session", Broken()):
            self.assertIsNone(await reg.create_task(user_id=7, request={}))

    async def test_live_task_registry(self):
        import asyncio

        async def _noop():
            await asyncio.sleep(10)

        task = asyncio.create_task(_noop())
        reg.register_live("k", task)
        self.assertTrue(reg.cancel_live("k"))
        reg.drop_live("k")
        self.assertFalse(reg.cancel_live("k"))
        task.cancel()


if __name__ == "__main__":
    unittest.main()
