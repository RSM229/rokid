import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Base  # noqa: E402
from app.models.agent_draft import AgentFlowInstance, AgentRequestTrace  # noqa: E402
from app.services import agent_runtime  # noqa: E402


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        agent_runtime._trace_write_unavailable = False
        asyncio.get_running_loop().set_debug(False)
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name, "agent-runtime.sqlite").as_posix()
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[AgentRequestTrace.__table__, AgentFlowInstance.__table__],
            )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    async def test_request_trace_spans_stages_and_keeps_plan(self):
        request_id = await agent_runtime.start_request(
            request_id="req-trace-1",
            user_id=7,
            project_id=3,
            conversation_id="session-1",
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            message="记录今天工作",
            provider="test",
            model="model-a",
            session_factory=self.sessions,
        )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=7,
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            request_plan={"intent": "create_record", "request_id": request_id},
            result={"intent": "create_record"},
            model="model-a",
            session_factory=self.sessions,
        )
        await agent_runtime.start_request(
            request_id=request_id,
            user_id=7,
            project_id=3,
            conversation_id="session-1",
            endpoint="/api/agent/review-plans/9/execute",
            stage="review_execute",
            session_factory=self.sessions,
        )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=7,
            endpoint="/api/agent/review-plans/9/execute",
            stage="review_execute",
            result={"created_count": 2, "records": [{"record_id": 11}, {"record_id": 12}]},
            review_plan_id=9,
            created_record_refs=[{"record_id": 11}, {"record_id": 12}],
            session_factory=self.sessions,
        )

        async with self.sessions() as db:
            row = (await db.execute(select(AgentRequestTrace).where(
                AgentRequestTrace.request_id == request_id,
            ))).scalars().one()
            payload = agent_runtime.trace_payload(row)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["request_plan"]["intent"], "create_record")
        self.assertEqual(len(payload["stages"]), 2)
        self.assertEqual(payload["result"]["created_count"], 2)
        self.assertEqual(len(payload["created_record_refs"]), 2)
        self.assertIsNotNone(payload["duration_ms"])

    async def test_trace_write_failure_never_breaks_business_call(self):
        def broken_session_factory():
            raise RuntimeError("trace database unavailable")

        request_id = await agent_runtime.start_request(
            request_id="req-trace-failure",
            user_id=7,
            project_id=3,
            conversation_id=None,
            endpoint="/api/agent/chat",
            stage="agent_chat",
            session_factory=broken_session_factory,
        )
        self.assertEqual(request_id, "req-trace-failure")

    async def test_running_execution_resumes_then_replays_committed_result(self):
        execution_id = agent_runtime.review_execution_id(21, 4)
        async with self.sessions() as db:
            await agent_runtime.prepare_execution(
                db,
                execution_id=execution_id,
                request_id="req-exec-1",
                flow_type="review_plan_execute",
                scope_id="review-plan:21",
                user_id=7,
                project_id=3,
                conversation_id="session-1",
                expected_revision=4,
            )

        # A crash after the RUNNING row commits leaves no formal write behind.
        async with self.sessions() as db:
            row = await agent_runtime.lock_execution(db, execution_id)
            self.assertEqual(row.status, "RUNNING")
            await db.rollback()

        async with self.sessions() as db:
            row = await agent_runtime.lock_execution(db, execution_id)
            agent_runtime.mark_execution_running(row, stage="WRITING")
            agent_runtime.mark_execution_committed(row, {
                "created_count": 1,
                "records": [{"record_id": 88}],
            })
            await db.commit()

        async with self.sessions() as db:
            row = await agent_runtime.lock_execution(db, execution_id)
            self.assertEqual(row.status, "COMMITTED")
            self.assertEqual(row.attempts, 2)
            self.assertEqual(agent_runtime.execution_result(row)["records"][0]["record_id"], 88)
            count = (await db.execute(select(func.count(AgentFlowInstance.id)))).scalar_one()
            self.assertEqual(count, 1)

    async def test_execution_id_cannot_be_reused_across_users_or_payloads(self):
        execution_id = "hazard-execution-shared"
        async with self.sessions() as db:
            await agent_runtime.prepare_execution(
                db,
                execution_id=execution_id,
                request_id="req-hazard-1",
                flow_type="hazard_candidate_batch",
                scope_id="hazard-batch:aaa",
                user_id=7,
                project_id=3,
            )
        async with self.sessions() as db:
            with self.assertRaisesRegex(ValueError, "已被其他执行占用"):
                await agent_runtime.prepare_execution(
                    db,
                    execution_id=execution_id,
                    request_id="req-hazard-2",
                    flow_type="hazard_candidate_batch",
                    scope_id="hazard-batch:bbb",
                    user_id=8,
                    project_id=3,
                )

    async def test_lock_execution_refreshes_committed_state_from_another_session(self):
        execution_id = agent_runtime.review_execution_id(35, 2)
        async with self.sessions() as first:
            await agent_runtime.prepare_execution(
                first,
                execution_id=execution_id,
                request_id="req-refresh-1",
                flow_type="review_plan_execute",
                scope_id="review-plan:35",
                user_id=7,
                project_id=3,
                expected_revision=2,
            )

            async with self.sessions() as second:
                row = await agent_runtime.lock_execution(second, execution_id)
                self.assertIsNotNone(row)
                agent_runtime.mark_execution_running(row)
                agent_runtime.mark_execution_committed(row, {"created_count": 1})
                await second.commit()

            # The first session still holds the old RUNNING identity-map value.
            # Locking must replace it with the committed state before deciding
            # whether the business write can run again.
            refreshed = await agent_runtime.lock_execution(first, execution_id)
            self.assertIsNotNone(refreshed)
            self.assertEqual(refreshed.status, "COMMITTED")
            await first.rollback()

if __name__ == "__main__":
    unittest.main()
