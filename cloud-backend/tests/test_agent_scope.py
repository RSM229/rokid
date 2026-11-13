"""Agent 作用域解析测试：项目隔离、会话绑定、总监识别。

2026-11-09 起项目/用户隔离的一切读写都先经过 ``agent_scope``；这里锁住：
- project_id 只从登录态推导，且校验 project_members 有效成员；
- 会话不能跨项目续聊；
- 项目记忆写权限认 project_members 岗位（总监/总监代表），不看 users.role。
"""
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_draft import (
    AgentConversationMemory,
    AgentConversationMessage,
)
from app.models.project_member import ProjectMember
from app.models.user import User
from app.services import agent_scope


def fake_user(user_id=1, project_id=1, role="inspector", org_role=None):
    return User(
        id=user_id, username=f"u{user_id}", password_hash="x",
        real_name=f"用户{user_id}", role=role,
        project_id=project_id, org_role=org_role,
    )


class AgentScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(ProjectMember.__table__.create)
            await conn.run_sync(AgentConversationMessage.__table__.create)
            await conn.run_sync(AgentConversationMemory.__table__.create)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _member(self, db, project_id, user_id, role):
        db.add(ProjectMember(project_id=project_id, user_id=user_id, role=role))
        await db.commit()

    async def _bind_conversation(self, db, user_id, conversation_id, project_id):
        db.add(AgentConversationMessage(
            user_id=user_id, project_id=project_id,
            conversation_id=conversation_id, role="user", content_json="{}",
        ))
        await db.commit()

    async def test_member_scope_uses_project_members_not_global_role(self):
        """A 项目总监在 B 项目按 B 项目岗位降级（不读 users.role）。"""
        async with self.session_factory() as db:
            await self._member(db, 1, 1, "chief_supervisor")
            await self._member(db, 2, 1, "inspector")

            scope_a = await agent_scope.resolve_agent_scope(db, fake_user(project_id=1))
            self.assertEqual(scope_a.project_id, 1)
            self.assertEqual(scope_a.role_codes, ("chief_supervisor",))
            self.assertTrue(scope_a.can_manage_project_memory)

            scope_b = await agent_scope.resolve_agent_scope(db, fake_user(project_id=2))
            self.assertEqual(scope_b.project_id, 2)
            self.assertEqual(scope_b.role_codes, ("inspector",))
            self.assertFalse(scope_b.can_manage_project_memory)

    async def test_deputy_can_manage_project_memory(self):
        async with self.session_factory() as db:
            await self._member(db, 1, 2, "deputy_chief")
            scope = await agent_scope.resolve_agent_scope(db, fake_user(user_id=2))
            self.assertTrue(scope.can_manage_project_memory)

    async def test_super_admin_can_manage_without_membership(self):
        async with self.session_factory() as db:
            scope = await agent_scope.resolve_agent_scope(
                db, fake_user(user_id=9, project_id=1, org_role="super_admin"),
            )
            self.assertTrue(scope.can_manage_project_memory)
            memory_scope = await agent_scope.resolve_project_memory_scope(
                db, fake_user(user_id=9, project_id=1, org_role="super_admin"),
            )
            self.assertEqual(memory_scope.project_id, 1)

    async def test_non_member_degrades_to_no_project_context(self):
        """离场脏数据不应阻断聊天，但项目记忆/项目会话全部不参与。"""
        async with self.session_factory() as db:
            scope = await agent_scope.resolve_agent_scope(
                db, fake_user(user_id=8, project_id=1),
            )
            self.assertIsNone(scope.project_id)
            self.assertFalse(scope.can_manage_project_memory)

    async def test_non_member_denied_for_project_memory_scope(self):
        async with self.session_factory() as db:
            with self.assertRaises(agent_scope.ProjectContextError):
                await agent_scope.resolve_project_memory_scope(
                    db, fake_user(user_id=8, project_id=1),
                )
            with self.assertRaises(agent_scope.ProjectContextError):
                await agent_scope.resolve_project_memory_scope(
                    db, fake_user(user_id=8, project_id=None),
                )

    async def test_conversation_cannot_cross_projects(self):
        async with self.session_factory() as db:
            await self._member(db, 1, 1, "inspector")
            await self._member(db, 2, 1, "inspector")
            await self._bind_conversation(db, 1, "wx-p1", 1)

            scope = await agent_scope.resolve_agent_scope(
                db, fake_user(project_id=1), "wx-p1",
            )
            self.assertEqual(scope.conversation_id, "wx-p1")

            with self.assertRaises(agent_scope.ConversationProjectMismatch) as ctx:
                await agent_scope.resolve_agent_scope(
                    db, fake_user(project_id=2), "wx-p1",
                )
            self.assertEqual(ctx.exception.bound_project_id, 1)
            self.assertEqual(ctx.exception.current_project_id, 2)

    async def test_unbound_conversation_is_allowed(self):
        async with self.session_factory() as db:
            await self._member(db, 1, 1, "inspector")
            scope = await agent_scope.resolve_agent_scope(
                db, fake_user(project_id=1), "wx-new",
            )
            self.assertEqual(scope.conversation_id, "wx-new")

    async def test_legacy_null_project_conversation_can_be_adopted(self):
        """老数据 project_id=NULL 没有项目归属：允许续聊，首次写入时认领到当前项目。"""
        async with self.session_factory() as db:
            await self._member(db, 1, 1, "inspector")
            await self._bind_conversation(db, 1, "wx-legacy", None)
            scope = await agent_scope.resolve_agent_scope(
                db, fake_user(project_id=1), "wx-legacy",
            )
            self.assertEqual(scope.conversation_id, "wx-legacy")
            self.assertEqual(scope.project_id, 1)


if __name__ == "__main__":
    unittest.main()
