import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_draft import AgentUserPreference
from app.services.agent_preferences import delete_preference, update_preference


def test_preferences_can_be_edited_and_deleted_only_by_owner():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            # 只建这张表：Base.metadata.create_all() 会连带编译 users 等本用例
            # 用不到、且依赖其他模块的表。
            await conn.run_sync(AgentUserPreference.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            own = AgentUserPreference(user_id=1, rule="旧规则", category="general", active=True)
            duplicate = AgentUserPreference(user_id=1, rule="重复规则", category="record", active=True)
            foreign = AgentUserPreference(user_id=2, rule="他人规则", category="general", active=True)
            db.add_all([own, duplicate, foreign])
            await db.commit()

            assert await update_preference(db, 2, own.id, "越权编辑") is None
            assert await delete_preference(db, 2, own.id) is False
            updated = await update_preference(db, 1, own.id, "新规则", category="writing")
            assert updated is own
            assert own.rule == "新规则"
            assert own.category == "writing"

            with pytest.raises(ValueError, match="相同的记忆"):
                await update_preference(db, 1, own.id, duplicate.rule)
            assert await delete_preference(db, 1, own.id) is True
            await db.commit()
            assert await db.get(AgentUserPreference, own.id) is None
            assert await db.get(AgentUserPreference, foreign.id) is foreign
        await engine.dispose()

    asyncio.run(scenario())
