"""第 3 项硬证据：SSE 客户端断开后后台轮次仍跑完并落任务状态。

旧实现的 ``event_stream`` finally 会 ``task.cancel()``，用户「说一句就走」
结果直接丢失。这里直接调用 ``agent_chat_stream``（不经 FastAPI 依赖注入），
消费第一条 meta 事件后立刻 aclose 模拟断流，再断言任务终态为 done。

SQLite ``:memory:`` 用 StaticPool 共享单连接：并发的「读 session」退出时
rollback 会把同一连接上「写 session」的提交一并回滚（MySQL 不会）。所以测试
侧用 ``SerialSession`` 把会话串行化，模拟真实数据库的事务隔离。
"""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_task import AgentAsyncTask
from app.routers import agent as agent_router
from app.services import agent_task_registry as reg


class FakeRunSession:
    """替代后台 runner 的独立 DB 会话；只需 get(User, id)。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model, pk):
        return SimpleNamespace(id=pk)


class SerialSession:
    """测试专用：同一时刻只允许一个 session 持有 SQLite 共享连接的事务。"""

    def __init__(self, maker, lock: asyncio.Lock):
        self._maker = maker
        self._lock = lock
        self._db = None

    async def __aenter__(self):
        await self._lock.acquire()
        self._db = self._maker()
        await self._db.__aenter__()
        return self._db

    async def __aexit__(self, *args):
        try:
            return await self._db.__aexit__(*args)
        finally:
            self._db = None
            self._lock.release()


class TestStreamDetach(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda c: AgentAsyncTask.__table__.create(c, checkfirst=True)
            )
        self.lock = asyncio.Lock()

        async def slow_chat(*_args, **_kwargs):
            await asyncio.sleep(0.12)
            return {"kind": "action", "reply": "已记录：巡视"}

        self.mock_chat = AsyncMock(side_effect=slow_chat)
        self.patches = [
            patch.object(
                reg, "async_session",
                lambda: SerialSession(self.Session, self.lock),
            ),
            patch.object(agent_router, "async_session", lambda: FakeRunSession()),
            patch.object(agent_router, "agent_chat", self.mock_chat),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.engine.dispose()

    async def _wait_terminal(self, user_id: int = 7):
        for _ in range(40):
            items = await reg.list_tasks(user_id, 5)
            if items and items[0]["status"] in ("done", "failed", "timed_out"):
                return items[0]
            await asyncio.sleep(0.02)
        return None

    async def test_disconnect_keeps_runner_alive(self):
        req = agent_router.AgentChatRequest(message="记一下", conversation_id="c1")
        resp = await agent_router.agent_chat_stream(
            req, user=SimpleNamespace(id=7, project_id=1), db=None,
        )
        gen = resp.body_iterator
        first = await gen.__anext__()  # meta 事件（含 task_key）
        self.assertIn("task_key", first)
        await gen.aclose()             # 🔴 客户端断开：旧实现会在这里 cancel runner

        item = await self._wait_terminal()
        self.assertIsNotNone(item, "后台任务未进入终态")
        self.mock_chat.assert_awaited_once()
        self.assertEqual(item["status"], "done", item)
        self.assertEqual(item["result"], {"kind": "action", "reply": "已记录：巡视"})

    async def test_normal_stream_also_records_done(self):
        req = agent_router.AgentChatRequest(message="记一下", conversation_id="c2")
        resp = await agent_router.agent_chat_stream(
            req, user=SimpleNamespace(id=7, project_id=1), db=None,
        )
        chunks = [chunk async for chunk in resp.body_iterator]
        self.assertTrue(any(b"done" in (c.encode() if isinstance(c, str) else c)
                            for c in chunks), chunks)
        item = await self._wait_terminal()
        self.assertIsNotNone(item)
        self.assertEqual(item["status"], "done")


if __name__ == "__main__":
    unittest.main()
