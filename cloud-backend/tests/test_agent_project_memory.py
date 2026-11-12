"""项目规约 + 个人记忆作用域的服务层测试。

覆盖 2026-11-09 的分层设计：
- 项目规约按 project_id 隔离，跨项目不可见；
- 总监/总监代表可维护，普通成员只能提交 pending 建议；
- 审批通过后全员可读，驳回后不再注入；
- 个人记忆支持「全局」与「仅当前项目」两种绑定，跨项目不串。
"""
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_draft import (
    AgentProjectMemory,
    AgentProjectMemoryAudit,
    AgentUserPreference,
)
from app.models.project_member import ProjectMember
from app.services import agent_preferences, agent_project_memories


class ProjectMemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(AgentProjectMemory.__table__.create)
            await conn.run_sync(AgentProjectMemoryAudit.__table__.create)
            await conn.run_sync(AgentUserPreference.__table__.create)
            await conn.run_sync(ProjectMember.__table__.create)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _session(self):
        return self.session_factory()

    async def _add_member(self, db, project_id, user_id, role):
        db.add(ProjectMember(project_id=project_id, user_id=user_id, role=role))
        await db.commit()

    async def test_project_memory_is_isolated_by_project(self):
        async with await self._session() as db:
            await agent_project_memories.create_memory(
                db, 1, "A 项目整改必须 48 小时闭环",
                actor_id=10, actor_roles=["chief_supervisor"],
            )
            await agent_project_memories.create_memory(
                db, 2, "B 项目旁站结论统一写「符合要求」",
                actor_id=20, actor_roles=["chief_supervisor"],
            )
            await db.commit()

            p1 = await agent_project_memories.list_active_memories(db, 1)
            p2 = await agent_project_memories.list_active_memories(db, 2)
            self.assertEqual([row.content for row in p1], ["A 项目整改必须 48 小时闭环"])
            self.assertEqual([row.content for row in p2], ["B 项目旁站结论统一写「符合要求」"])

    async def test_duplicate_content_is_reused_not_stacked(self):
        async with await self._session() as db:
            first = await agent_project_memories.create_memory(
                db, 1, "整改回复必须附前后对比照片",
                actor_id=10, actor_roles=["chief_supervisor"],
            )
            second = await agent_project_memories.create_memory(
                db, 1, "整改回复必须附前后对比照片",
                actor_id=10, actor_roles=["chief_supervisor"],
            )
            await db.commit()
            self.assertEqual(first.id, second.id)
            self.assertEqual(
                len(await agent_project_memories.list_active_memories(db, 1)), 1,
            )

    async def test_member_suggestion_needs_approval_and_is_not_injected(self):
        async with await self._session() as db:
            await self._add_member(db, 1, 10, "chief_supervisor")
            await self._add_member(db, 1, 12, "inspector")

            suggestion = await agent_project_memories.create_memory(
                db, 1, "日志天气统一以现场实测为准",
                actor_id=12, actor_roles=["inspector"],
                status=agent_project_memories.STATUS_PENDING,
                source="member_suggested",
            )
            await db.commit()
            self.assertEqual(suggestion.status, "pending")
            self.assertEqual(await agent_project_memories.list_active_memories(db, 1), [])
            pending = await agent_project_memories.list_pending_memories(db, 1)
            self.assertEqual([row.content for row in pending], ["日志天气统一以现场实测为准"])

            approved = await agent_project_memories.approve_memory(
                db, 1, suggestion.id, approve=True, approver_id=10,
            )
            await db.commit()
            self.assertEqual(approved.status, "active")
            active = await agent_project_memories.list_active_memories(db, 1)
            self.assertEqual([row.content for row in active], ["日志天气统一以现场实测为准"])

    async def test_rejected_suggestion_is_not_injected(self):
        async with await self._session() as db:
            suggestion = await agent_project_memories.create_memory(
                db, 1, "这条建议会被驳回",
                actor_id=12, actor_roles=["inspector"],
                status="pending", source="member_suggested",
            )
            await db.commit()
            await agent_project_memories.approve_memory(
                db, 1, suggestion.id, approve=False, approver_id=10,
            )
            await db.commit()
            self.assertEqual(await agent_project_memories.list_active_memories(db, 1), [])
            self.assertEqual(await agent_project_memories.list_pending_memories(db, 1), [])

    async def test_active_cap_blocks_prompt_growth(self):
        async with await self._session() as db:
            for index in range(agent_project_memories.MAX_ACTIVE_PROJECT_MEMORIES):
                await agent_project_memories.create_memory(
                    db, 1, f"规则 {index}", actor_id=10,
                    actor_roles=["chief_supervisor"],
                )
            await db.commit()
            with self.assertRaises(ValueError):
                await agent_project_memories.create_memory(
                    db, 1, "第 21 条", actor_id=10,
                    actor_roles=["chief_supervisor"],
                )

    async def test_project_memory_render_block(self):
        async with await self._session() as db:
            await agent_project_memories.create_memory(
                db, 1, "整改回复必须附前后对比照片", actor_id=10,
                actor_roles=["chief_supervisor"],
            )
            await db.commit()
            rows = await agent_project_memories.list_active_memories(db, 1)
            block = agent_project_memories.render_project_memories_block(rows)
            self.assertIn("本项目规约", block)
            self.assertIn("整改回复必须附前后对比照片", block)
            self.assertIn("以本条为准", block)
            self.assertEqual(agent_project_memories.render_project_memories_block([]), "")

    async def test_publish_personal_memory_copies_to_project(self):
        async with await self._session() as db:
            preference = await agent_preferences.upsert_preference(
                db, 10, "整改回复必须附前后对比照片", category="rectification",
            )
            await db.commit()
            row = await agent_project_memories.publish_preference(
                db, 1, preference, actor_id=10, actor_roles=["chief_supervisor"],
            )
            await db.commit()
            self.assertEqual(row.project_id, 1)
            self.assertEqual(row.content, "整改回复必须附前后对比照片")
            self.assertEqual(row.origin_preference_id, preference.id)
            self.assertEqual(row.source, "chief_declared")

    async def test_personal_memory_scope_global_plus_current_project(self):
        async with await self._session() as db:
            await agent_preferences.upsert_preference(db, 10, "全局习惯")
            await agent_preferences.upsert_preference(db, 10, "项目一习惯", project_id=1)
            await agent_preferences.upsert_preference(db, 10, "项目二习惯", project_id=2)
            await db.commit()

            p1 = await agent_preferences.list_active_preferences(db, 10, project_id=1)
            self.assertEqual(
                sorted(row.rule for row in p1), ["全局习惯", "项目一习惯"],
            )
            management = await agent_preferences.list_all_preferences(db, 10, project_id=1)
            self.assertEqual(
                sorted(row.rule for row in management), ["全局习惯", "项目一习惯"],
            )
            p2 = await agent_preferences.list_active_preferences(db, 10, project_id=2)
            self.assertEqual(sorted(row.rule for row in p2), ["全局习惯", "项目二习惯"])


    async def test_usage_count_and_audit_trail(self):
        async with await self._session() as db:
            row = await agent_project_memories.create_memory(
                db, 1, "整改回复必须附前后对比照片",
                actor_id=10, actor_roles=["chief_supervisor"],
            )
            row_id = row.id
            await db.commit()

            used = await agent_project_memories.record_usage(
                db, 1, [row_id], actor_id=12, conversation_id="wx-1",
            )
            await agent_project_memories.record_usage(
                db, 1, [row_id], actor_id=12, conversation_id="wx-2",
            )
            await db.commit()
            self.assertEqual(used, 1)

            db.expire_all()
            refreshed = await agent_project_memories.get_memory(db, 1, row_id)
            self.assertEqual(refreshed.use_count, 2)
            self.assertIsNotNone(refreshed.last_used_at)

            summary = await agent_project_memories.usage_summary(db, 1)
            self.assertEqual(summary[0]["use_count"], 2)

            audit = await agent_project_memories.list_audit(db, 1)
            actions = [item["action"] for item in audit]
            self.assertIn("create", actions)
            self.assertIn("use", actions)
            use_row = next(item for item in audit if item["action"] == "use")
            self.assertEqual(use_row["detail"]["memory_ids"], [row_id])

    async def test_usage_is_isolated_by_project(self):
        async with await self._session() as db:
            p1 = await agent_project_memories.create_memory(
                db, 1, "A 项目规则", actor_id=10,
                actor_roles=["chief_supervisor"],
            )
            p2 = await agent_project_memories.create_memory(
                db, 2, "B 项目规则", actor_id=20,
                actor_roles=["chief_supervisor"],
            )
            p1_id, p2_id = p1.id, p2.id
            await db.commit()
            await agent_project_memories.record_usage(db, 1, [p1_id], actor_id=10)
            await db.commit()
            db.expire_all()
            self.assertEqual(
                (await agent_project_memories.get_memory(db, 1, p1_id)).use_count, 1,
            )
            self.assertEqual(
                (await agent_project_memories.get_memory(db, 2, p2_id)).use_count, 0,
            )

    async def test_personal_memory_scope_can_be_changed(self):
        async with await self._session() as db:
            preference = await agent_preferences.upsert_preference(db, 10, "只在项目一用")
            await db.commit()
            updated = await agent_preferences.set_preference_scope(
                db, 10, preference.id, 1,
            )
            await db.commit()
            self.assertEqual(updated.project_id, 1)
            self.assertEqual(
                [row.rule for row in await agent_preferences.list_active_preferences(
                    db, 10, project_id=1,
                )],
                ["只在项目一用"],
            )
            self.assertEqual(
                await agent_preferences.list_active_preferences(db, 10, project_id=2), [],
            )
            # 切回全局后两个项目都可见
            await agent_preferences.set_preference_scope(db, 10, preference.id, None)
            await db.commit()
            self.assertEqual(
                len(await agent_preferences.list_active_preferences(db, 10, project_id=2)), 1,
            )

    async def test_scope_change_rejects_duplicate_in_target_binding(self):
        async with await self._session() as db:
            global_row = await agent_preferences.upsert_preference(db, 10, "重复规则")
            await agent_preferences.upsert_preference(
                db, 10, "重复规则", project_id=1,
            )
            await db.commit()
            with self.assertRaises(ValueError):
                await agent_preferences.set_preference_scope(
                    db, 10, global_row.id, 1,
                )


if __name__ == "__main__":
    unittest.main()
