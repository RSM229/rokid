import unittest
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_draft import AgentConversationMemory, AgentConversationMessage
from app.services import agent_tools


class AgentConversationHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(AgentConversationMemory.__table__.create)
            await conn.run_sync(AgentConversationMessage.__table__.create)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_history_is_persisted_and_isolated_by_user(self):
        async with self.session_factory() as db:
            await agent_tools.record_conversation_message(
                db, 11, "wx-100", "巡检今天的工作", project_id=1,
            )
            await agent_tools.append_conversation_response(db, 11, "wx-100", {
                "kind": "chat", "reply": "已整理", "cards": [], "actions": [],
            }, project_id=1)
            await agent_tools.record_conversation_message(
                db, 11, "wx-100", "补充西区", project_id=1,
            )
            await agent_tools.append_conversation_response(db, 11, "wx-100", {
                "kind": "chat", "reply": "已补充", "cards": [], "actions": [],
            }, project_id=1)
            await agent_tools.record_conversation_message(
                db, 22, "wx-200", "另一个账号", project_id=1,
            )
            db.add(AgentConversationMemory(
                user_id=11, conversation_id="wx-legacy", project_id=None,
                messages_json='[{"text":"以前的分段记录","at":"2026-10-20T09:00:00"}]',
                updated_at=datetime.now() - timedelta(days=1),
            ))
            await db.commit()

            summaries = await agent_tools.list_conversation_summaries(db, 11, project_id=1)
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["conversation_id"], "wx-100")
            self.assertEqual(summaries[0]["title"], "巡检今天的工作")
            self.assertEqual(summaries[0]["message_count"], 4)

            detail = await agent_tools.get_conversation_detail(db, 11, "wx-100", project_id=1)
            self.assertIsNotNone(detail)
            self.assertEqual([item["role"] for item in detail["messages"]],
                             ["user", "assistant", "user", "assistant"])
            self.assertEqual(detail["messages"][1]["response"]["reply"], "已整理")
            self.assertIsNone(
                await agent_tools.get_conversation_detail(db, 22, "wx-100", project_id=1)
            )
            # 无项目上下文的旧数据只在 project_id=None 时可见
            legacy = await agent_tools.list_conversation_summaries(db, 11, project_id=None)
            self.assertEqual([item["conversation_id"] for item in legacy], ["wx-legacy"])

            self.assertTrue(await agent_tools.delete_conversation(db, 11, "wx-100", project_id=1))
            self.assertIsNone(await agent_tools.get_conversation_detail(db, 11, "wx-100", project_id=1))
            self.assertIsNotNone(await agent_tools.get_conversation_detail(db, 22, "wx-200", project_id=1))

    async def test_conversations_are_isolated_between_projects(self):
        """同一用户的会话按项目隔离：切项目看不到、也不能删另一个项目的会话。"""
        async with self.session_factory() as db:
            await agent_tools.record_conversation_message(
                db, 11, "wx-p1", "项目一的对话", project_id=1,
            )
            await agent_tools.append_conversation_response(
                db, 11, "wx-p1", {"kind": "chat", "reply": "项目一回复"}, project_id=1,
            )
            await agent_tools.record_conversation_message(
                db, 11, "wx-p2", "项目二的对话", project_id=2,
            )
            await agent_tools.append_conversation_response(
                db, 11, "wx-p2", {"kind": "chat", "reply": "项目二回复"}, project_id=2,
            )
            await db.commit()

            p1 = await agent_tools.list_conversation_summaries(db, 11, project_id=1)
            self.assertEqual([item["conversation_id"] for item in p1], ["wx-p1"])
            p2 = await agent_tools.list_conversation_summaries(db, 11, project_id=2)
            self.assertEqual([item["conversation_id"] for item in p2], ["wx-p2"])
            self.assertIsNone(
                await agent_tools.get_conversation_detail(db, 11, "wx-p2", project_id=1)
            )
            # 删除必须带项目，跨项目删不到
            self.assertFalse(await agent_tools.delete_conversation(db, 11, "wx-p2", project_id=1))
            self.assertIsNotNone(
                await agent_tools.get_conversation_detail(db, 11, "wx-p2", project_id=2)
            )

    async def test_memory_trail_is_also_scoped_to_project(self):
        """模型上下文原话表同样按项目隔离，避免切项目后串上下文。"""
        async with self.session_factory() as db:
            await agent_tools.record_conversation_message(
                db, 11, "wx-p1", "项目一原话", project_id=1,
            )
            await agent_tools.record_conversation_message(
                db, 11, "wx-p2", "项目二原话", project_id=2,
            )
            await db.commit()
            p1 = await agent_tools.list_conversation_summaries(db, 11, project_id=1)
            self.assertEqual([item["conversation_id"] for item in p1], ["wx-p1"])

    async def test_images_are_persisted_for_display_but_not_model_context(self):
        """图片 URL 只进展示历史（历史回看能看到照片），不进模型上下文。"""
        from sqlalchemy import select

        async with self.session_factory() as db:
            await agent_tools.record_conversation_message(
                db, 33, "wx-photo", "记录今天的工作",
                images=["/api/media/12/file", "", "http://x/y.jpg"], project_id=3,
            )
            # 只有图没有文字也要能落库（用户只发照片不说话）
            await agent_tools.record_conversation_message(
                db, 33, "wx-photo", "", images=["/api/media/13/file"], project_id=3,
            )
            await db.commit()

            detail = await agent_tools.get_conversation_detail(db, 33, "wx-photo", project_id=3)
            self.assertIsNotNone(detail)
            self.assertEqual(detail["messages"][0]["images"], ["/api/media/12/file", "http://x/y.jpg"])
            self.assertEqual(detail["messages"][0]["text"], "记录今天的工作")
            self.assertEqual(detail["messages"][1]["images"], ["/api/media/13/file"])
            self.assertEqual(detail["messages"][1]["text"], "")

            memory = (await db.execute(select(AgentConversationMemory).where(
                AgentConversationMemory.user_id == 33,
                AgentConversationMemory.conversation_id == "wx-photo",
                AgentConversationMemory.project_id == 3,
            ))).scalars().first()
            self.assertIsNotNone(memory)
            trail = __import__("json").loads(memory.messages_json or "[]")
            self.assertTrue(all("images" not in item for item in trail))

    async def test_legacy_null_project_conversation_is_adopted_on_first_write(self):
        """老数据 project_id=NULL：首次在当前项目续聊时整条会话认领到当前项目。"""
        async with self.session_factory() as db:
            db.add(AgentConversationMemory(
                user_id=11, conversation_id="wx-adopt", project_id=None,
                messages_json='[{"text":"旧项目原话","at":"2026-10-20T09:00:00"}]',
            ))
            db.add(AgentConversationMessage(
                user_id=11, conversation_id="wx-adopt", project_id=None,
                role="user", content_json='{"role":"user","text":"旧项目原话"}',
            ))
            await db.commit()

            history = await agent_tools.record_conversation_message(
                db, 11, "wx-adopt", "新项目继续", project_id=5,
            )
            self.assertEqual(history[0]["text"], "旧项目原话")
            detail = await agent_tools.get_conversation_detail(db, 11, "wx-adopt", project_id=5)
            self.assertIsNotNone(detail)
            self.assertEqual([item["text"] for item in detail["messages"]],
                             ["旧项目原话", "新项目继续"])
            # 已被认领：其他项目不可见
            self.assertIsNone(
                await agent_tools.get_conversation_detail(db, 11, "wx-adopt", project_id=6)
            )

    async def test_legacy_memory_is_visible_when_message_table_is_empty(self):
        async with self.session_factory() as db:
            db.add(AgentConversationMemory(
                user_id=11, conversation_id="wx-legacy-only", project_id=None,
                messages_json='[{"text":"旧版本对话仍在服务器","at":"2026-10-21T09:00:00"}]',
                updated_at=datetime.now(),
            ))
            await db.commit()

            summaries = await agent_tools.list_conversation_summaries(db, 11, project_id=None)
            self.assertEqual([item["conversation_id"] for item in summaries], ["wx-legacy-only"])
            detail = await agent_tools.get_conversation_detail(db, 11, "wx-legacy-only", project_id=None)
            self.assertIsNotNone(detail)
            self.assertEqual(detail["messages"][0]["text"], "旧版本对话仍在服务器")


if __name__ == "__main__":
    unittest.main()
