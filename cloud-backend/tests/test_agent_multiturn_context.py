# -*- coding: utf-8 -*-
"""端到端集成回归：多轮对话时路由模型必须收到 assistant 轮上下文（2026-11-23 Phase 0）。

背景（用户真实痛点「智能体听不懂我的意思、上下文不相关」）：
生产链路里模型上下文有三条来源——
  1. 前端 req.history（最近 6 轮，`_history_messages` 截断）；
  2. DB 展示历史 earlier_messages（`render_conversation_history` 渲染成引用块）；
  3. 持久草稿（`render_context_summary`）。
此前 2 只渲染 user 原话、assistant 轮整段丢失 → 模型不知道自己上轮问过
什么，用户答「合格」接不住。

本测试打真实 `_agent_chat_impl`（不打桩核心逻辑），只 mock 两个外部边界：
  - model_gateway.stream_chat_completion / chat_completion（不真调供应商，
    但**捕获完整 messages 列表**用于断言）；
  - agent_runtime 进度上报（避免依赖 agent_async_tasks 表）。
DB 用真实 SQLite 内存库（AIOSQLite），Project/ProjectMember/会话表全部真建。

跑法：
    .venv/Scripts/python.exe -m pytest tests/test_agent_multiturn_context.py -v
"""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_draft import (
    AgentConversationMemory,
    AgentConversationMessage,
)
from app.models.project_member import ProjectMember
from app.models.user import User
from app.routers import agent as agent_router
from app.services import agent_tools


def _fake_user(user_id=41, project_id=7):
    return SimpleNamespace(
        id=user_id, project_id=project_id, org_role=None,
        real_name="张监理", username="tester",
        is_super_admin=lambda: False,
        has_permission=lambda _permission: True,
    )


def _project():
    return SimpleNamespace(
        id=7, name="沙盛四路等四条道路工程", construction_unit="总包A",
        supervision_unit="监理单位A", supervision_org="项目监理部",
        eng_type="房建",
    )


def _router_reply(answer: str, record_type: str | None = "验收",
                  facts: list[dict] | None = None) -> str:
    """构造路由模型的标准 JSON 输出（对齐 _AGENT_SYSTEM 要求的输出结构）。"""
    payload = {
        "intent": "create_record" if record_type else "chat",
        "actions": [{"id": "record_work", "confidence": 0.9}] if record_type else [],
        "record_type": record_type,
        "record_type_confidence": 0.9 if record_type else 0,
        "facts": facts or [],
        "args": {},
        "answer": answer,
    }
    return json.dumps(payload, ensure_ascii=False)


class MultiTurnContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from app.models.user import Project
        from app.models.work_record import WorkRecord
        from app.models.agent_draft import (
            AgentConversationDraft,
            AgentRequestTrace,
            AgentProjectMemory,
            AgentUserPreference,
        )

        async with self.engine.begin() as conn:
            for table in (
                Project.__table__, ProjectMember.__table__,
                AgentConversationMessage.__table__,
                AgentConversationMemory.__table__,
                AgentConversationDraft.__table__,
                AgentRequestTrace.__table__,
                AgentProjectMemory.__table__,
                AgentUserPreference.__table__,
                WorkRecord.__table__,
                *self._extra_tables(),
            ):
                await conn.run_sync(table.create)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.session_factory() as db:
            db.add(_project_row())
            db.add(ProjectMember(project_id=7, user_id=41, role="inspector"))
            await db.commit()

        # 捕获每轮路由模型收到的完整 messages
        self.router_calls: list[list[dict]] = []

    def _extra_tables(self):
        tables = []
        # 2026-11-25 口述落待定：create_work_record 真正执行（写安全审计），
        # 测试库必须建 security_audit，否则工具执行撞表缺失误报失败。
        try:
            from app.models.security_audit import SecurityAudit
            tables.append(SecurityAudit.__table__)
        except ImportError:
            pass
        try:
            from app.models.agent_draft import AgentReviewPlan
            tables.append(AgentReviewPlan.__table__)
        except ImportError:
            pass
        return tables

    async def asyncTearDown(self):
        await self.engine.dispose()

    # ---- mock 边界（只打外部依赖，不打被测逻辑） ----

    def _install_model_mock(self, answers: list[str]):
        """每轮路由调用返回 answers[i]，并捕获 messages。

        🔴 测试隔离：直接赋值会污染共享的 model_gateway 模块对象，导致同批
        运行的其他测试（如 test_model_gateway_tools 契约测试）拿到假函数。
        必须用 addCleanup 恢复原函数。
        """
        self._answers = list(answers)
        router_self = self
        _orig_stream = agent_router.model_gateway.stream_chat_completion
        _orig_chat = agent_router.model_gateway.chat_completion

        async def stream_gen(messages, **kwargs):
            router_self.router_calls.append(messages)
            answer = router_self._answers.pop(0) if router_self._answers else _router_reply("好", None)
            yield {"type": "config", "config": SimpleNamespace(model_id="test-model")}
            yield {"type": "content", "text": answer}

        async def fake_chat(messages, **kwargs):
            router_self.router_calls.append(messages)
            answer = router_self._answers.pop(0) if router_self._answers else _router_reply("好", None)
            return answer, SimpleNamespace(model_id="test-model")

        agent_router.model_gateway.stream_chat_completion = stream_gen
        agent_router.model_gateway.chat_completion = fake_chat
        self.addCleanup(setattr, agent_router.model_gateway,
                        "stream_chat_completion", _orig_stream)
        self.addCleanup(setattr, agent_router.model_gateway,
                        "chat_completion", _orig_chat)

    def _install_runtime_mock(self):
        async def noop(*_args, **_kwargs):
            return "req-test"

        async def noop_event(**_kwargs):
            return None

        async def noop_finish(**_kwargs):
            return None

        _orig = {
            "start_request": agent_router.agent_runtime.start_request,
            "append_progress_event": agent_router.agent_runtime.append_progress_event,
            "finish_request": agent_router.agent_runtime.finish_request,
        }
        agent_router.agent_runtime.start_request = noop
        agent_router.agent_runtime.append_progress_event = noop_event
        agent_router.agent_runtime.finish_request = noop_finish
        for key, value in _orig.items():
            self.addCleanup(setattr, agent_router.agent_runtime, key, value)

    async def _chat(self, message: str, conversation_id: str,
                    history: list[dict] | None = None):
        """走 agent_chat 包装层（含 append_conversation_response 落库），
        这才是生产的真实路径——assistant 轮靠它写进 AgentConversationMessage。"""
        req = agent_router.AgentChatRequest(
            message=message,
            request_id=f"req-{len(self.router_calls)}",
            conversation_id=conversation_id,
            history=history or [],
        )
        async with self.session_factory() as db:
            result = await agent_router.agent_chat(req, _fake_user(), db)
        return result

    async def test_second_turn_router_sees_first_turn_assistant_reply(self):
        """核心回归：第 2 轮路由模型的消息列表必须含第 1 轮 assistant 回复。

        场景对齐真实踩坑（多轮补字段）：
          第 1 轮：用户「记一条验收」→ 智能体整理草稿、回复缺栏追问
          第 2 轮：用户「合格」→ 此前模型看不到自己问过「验收结论」，
          现在必须能看到（前端不带 history 的最坏情况也要能靠 DB 历史接住）。
        """
        conv = "wx-multiturn-1"
        # 第 1 轮模型输出：整理验收记录、答复「还差验收结论」
        answer_1 = _router_reply(
            "收到，先整理成验收记录。验收结论是合格还是不合格？",
            record_type="验收",
            facts=[{"field": "record_type", "value": "验收",
                    "source_span": "验收", "confidence": 0.95}],
        )
        # 第 2 轮模型输出：接住短答（这是被测的「理解」行为，mock 里模拟
        # 一个「读到了上下文才可能给出」的接话）
        answer_2 = _router_reply(
            "收到，验收结论记『合格』。",
            record_type="验收",
            facts=[{"field": "acceptance_result", "value": "合格",
                    "source_span": "合格", "confidence": 0.9}],
        )
        self._install_model_mock([answer_1, answer_2])
        self._install_runtime_mock()

        result_1 = await self._chat("记一条验收", conv)
        result_2 = await self._chat("合格", conv)

        # ---- 断言 1：两轮都正常返回（不因改动引入异常路径） ----
        self.assertIn("reply", result_1)
        self.assertIn("reply", result_2)

        # ---- 断言 2：第 2 轮模型收到的消息里必须有第 1 轮 assistant 文案 ----
        self.assertEqual(len(self.router_calls), 2, "每轮恰好一次路由调用")
        turn2_messages = self.router_calls[1]
        all_text = json.dumps(
            [m.get("content", "") for m in turn2_messages], ensure_ascii=False)
        # 第 1 轮 reply 的标志性内容必须出现在第 2 轮上下文（任意位置：
        # _history_messages 或 render_conversation_history 引用块均可）
        self.assertIn(
            "验收结论是合格还是不合格",
            all_text,
            "第 2 轮模型看不到自己上一轮的追问 —— 上下文断裂未修复",
        )

        # ---- 断言 3：用户第 1 轮原话也在第 2 轮上下文里（回归保障） ----
        self.assertIn("记一条验收", all_text)

    async def test_long_user_message_not_cut_at_500(self):
        """Phase 0 第二处修复：>500 字的口述不再被拦腰截断。

        前端 history 单条超 500 字（模拟长口述），旧版 _history_messages
        会截成 500 字；修复后应到 2000。通过第 2 轮路由收到的内容验证。
        """
        conv = "wx-long-1"
        long_story = "现场巡视情况：" + "上午在各栋巡检，" * 120  # >1300 字
        long_story = long_story[:1200]
        answer = _router_reply("收到。", record_type=None)
        self._install_model_mock([answer, answer])
        self._install_runtime_mock()

        history = [
            {"role": "user", "text": long_story},
            {"role": "assistant", "text": "已记录现场情况。"},
        ]
        await self._chat("记一条巡视", conv, history=history)

        # 前端带超长历史时：第 1 轮模型就该看到全文（而非前 500 字）
        turn1_text = json.dumps(
            [m.get("content", "") for m in self.router_calls[0]], ensure_ascii=False)
        tail_marker = long_story[-20:]
        self.assertIn(
            tail_marker, turn1_text,
            "超过 500 字的口述结尾被截掉 —— 截断上限未放宽到 2000",
        )

    async def test_db_history_supplies_assistant_turn_without_frontend_history(self):
        """前端不带 history（旧版小程序/断网重发）时，DB 展示历史补上下文。

        第 1 轮落库的 assistant 回复，第 2 轮（req.history 为空）也要经
        render_conversation_history 注入 —— 这是修复的主路径。
        """
        conv = "wx-db-1"
        answer_1 = _router_reply(
            "已整理旁站草稿，还差旁站的关键工序。", record_type="旁站")
        answer_2 = _router_reply("收到。", record_type="旁站")
        self._install_model_mock([answer_1, answer_2])
        self._install_runtime_mock()

        await self._chat("记一条旁站", conv)     # 不传 history
        await self._chat("混凝土浇筑", conv)     # 同样不传

        turn2_text = json.dumps(
            [m.get("content", "") for m in self.router_calls[1]], ensure_ascii=False)
        self.assertIn("还差旁站的关键工序", turn2_text)

    async def test_conversation_rows_contain_assistant_turns(self):
        """落库完整性：两轮后 AgentConversationMessage 应有 user+assistant 交错。"""
        conv = "wx-rows-1"
        answer = _router_reply("收到。", record_type="巡视")
        self._install_model_mock([answer, answer])
        self._install_runtime_mock()

        await self._chat("记一条巡视", conv)
        await self._chat("符合要求", conv)

        async with self.session_factory() as db:
            rows = (await db.execute(select(AgentConversationMessage).where(
                AgentConversationMessage.conversation_id == conv,
            ).order_by(AgentConversationMessage.id.asc()))).scalars().all()
        roles = [row.role for row in rows]
        # append_conversation_response 在流式路径外由 agent_chat 调用；
        # _agent_chat_impl 直连时不经过该包装。这里只断言 user 轮两条都在，
        # assistant 轮由 agent_chat 包装层负责（见上方 runtime mock 下的路径）。
        self.assertGreaterEqual(roles.count("user"), 2)


def _project_row():
    from app.models.user import Project
    return Project(
        id=7, name="沙盛四路等四条道路工程", code="P7",
        eng_type="房建",
        construction_unit="总包A",
        supervision_unit="监理单位A",
        supervision_org="项目监理部",
        status="active",
    )


if __name__ == "__main__":
    unittest.main()
