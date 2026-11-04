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
            await agent_tools.record_conversation_message(db, 11, "wx-100", "巡检今天的工作")
            await agent_tools.append_conversation_response(db, 11, "wx-100", {
                "kind": "chat", "reply": "已整理", "cards": [], "actions": [],
            })
            await agent_tools.record_conversation_message(db, 11, "wx-100", "补充西区")
            await agent_tools.append_conversation_response(db, 11, "wx-100", {
                "kind": "chat", "reply": "已补充", "cards": [], "actions": [],
            })
            await agent_tools.record_conversation_message(db, 22, "wx-200", "另一个账号")
            db.add(AgentConversationMemory(
                user_id=11, conversation_id="wx-legacy",
                messages_json='[{"text":"以前的分段记录","at":"2026-10-20T09:00:00"}]',
                updated_at=datetime.now() - timedelta(days=1),
            ))
            await db.commit()

            summaries = await agent_tools.list_conversation_summaries(db, 11)
            self.assertEqual(len(summaries), 2)
            self.assertEqual(summaries[0]["conversation_id"], "wx-100")
            self.assertEqual(summaries[0]["title"], "巡检今天的工作")
            self.assertEqual(summaries[0]["message_count"], 4)
            self.assertEqual(summaries[1]["conversation_id"], "wx-legacy")

            detail = await agent_tools.get_conversation_detail(db, 11, "wx-100")
            self.assertIsNotNone(detail)
            self.assertEqual([item["role"] for item in detail["messages"]],
                             ["user", "assistant", "user", "assistant"])
            self.assertEqual(detail["messages"][1]["response"]["reply"], "已整理")
            self.assertIsNone(await agent_tools.get_conversation_detail(db, 22, "wx-100"))

            self.assertTrue(await agent_tools.delete_conversation(db, 11, "wx-100"))
            self.assertIsNone(await agent_tools.get_conversation_detail(db, 11, "wx-100"))
            self.assertIsNotNone(await agent_tools.get_conversation_detail(db, 22, "wx-200"))

    async def test_images_are_persisted_for_display_but_not_model_context(self):
        """图片 URL 只进展示历史（历史回看能看到照片），不进模型上下文。"""
        from sqlalchemy import select

        async with self.session_factory() as db:
            await agent_tools.record_conversation_message(
                db, 33, "wx-photo", "记录今天的工作",
                images=["/api/media/12/file", "", "http://x/y.jpg"],
            )
            # 只有图没有文字也要能落库（用户只发照片不说话）
            await agent_tools.record_conversation_message(db, 33, "wx-photo", "", images=["/api/media/13/file"])
            await db.commit()

            detail = await agent_tools.get_conversation_detail(db, 33, "wx-photo")
            self.assertIsNotNone(detail)
            self.assertEqual(detail["messages"][0]["images"], ["/api/media/12/file", "http://x/y.jpg"])
            self.assertEqual(detail["messages"][0]["text"], "记录今天的工作")
            self.assertEqual(detail["messages"][1]["images"], ["/api/media/13/file"])
            self.assertEqual(detail["messages"][1]["text"], "")

            memory = (await db.execute(select(AgentConversationMemory).where(
                AgentConversationMemory.user_id == 33,
                AgentConversationMemory.conversation_id == "wx-photo",
            ))).scalars().first()
            self.assertIsNotNone(memory)
            trail = __import__("json").loads(memory.messages_json or "[]")
            self.assertTrue(all("images" not in item for item in trail))

    async def test_legacy_memory_is_visible_when_message_table_is_empty(self):
        async with self.session_factory() as db:
            db.add(AgentConversationMemory(
                user_id=11, conversation_id="wx-legacy-only",
                messages_json='[{"text":"旧版本对话仍在服务器","at":"2026-10-21T09:00:00"}]',
                updated_at=datetime.now(),
            ))
            await db.commit()

            summaries = await agent_tools.list_conversation_summaries(db, 11)
            self.assertEqual([item["conversation_id"] for item in summaries], ["wx-legacy-only"])
            detail = await agent_tools.get_conversation_detail(db, 11, "wx-legacy-only")
            self.assertIsNotNone(detail)
            self.assertEqual(detail["messages"][0]["text"], "旧版本对话仍在服务器")


if __name__ == "__main__":
    unittest.main()
