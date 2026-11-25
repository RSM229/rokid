"""验证：口述建记录 + 有内容 + 类型不明 → 落「待定」（先留痕），不再问类型。

生产实测依据（2026-11-25 统计 11 次类型问句现场）：
- user 32（消息 935/939）：「我要拍照记录工序」「记录现场工作」→ 连续被问
  「整理成哪一类」，对话在 940 后流失；
- user 34（127-134）：三轮「记录今天的工作」死循环（bug #4 兜底已修一轮，
  本修复收尾「有内容仍问类型」的剩余路径）；
- 11 次问句仅 2 次走通、5 次流失（转化率 ~18%）。

保护（2026-11-24 用户反馈「照片类型没判断出来就建成待定」）：
只发照片/资料没说话（无建记录口述）仍走问句，不落待定。
"""
import asyncio
import json
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, ".")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_draft import (
    AgentConversationMemory, AgentConversationMessage,
    AgentConversationDraft, AgentRequestTrace, AgentProjectMemory,
    AgentUserPreference,
)
from app.models.image import ImageRecord
from app.models.miniprogram import MediaRecord
from app.models.project_member import ProjectMember
from app.models.security_audit import SecurityAudit
from app.models.user import Project
from app.models.work_record import WorkRecord
from app.routers import agent as agent_router


def _fake_user(user_id=41, project_id=7):
    return SimpleNamespace(
        id=user_id, project_id=project_id, org_role=None,
        real_name="张监理", username="tester",
        is_super_admin=lambda: False,
        has_permission=lambda _permission: True,
    )


def _router_reply(answer, record_type=None, facts=None):
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


class RecordTypeTbdTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
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
                SecurityAudit.__table__,
                ImageRecord.__table__,
                MediaRecord.__table__,
            ):
                await conn.run_sync(table.create)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.session_factory() as db:
            db.add(Project(id=7, name="沙盛四路等四条道路工程", code="P7",
                           eng_type="房建", construction_unit="总包A",
                           supervision_unit="监理单位A", supervision_org="项目监理部",
                           status="active"))
            db.add(ProjectMember(project_id=7, user_id=41, role="inspector"))
            await db.commit()

        self.test_image = "/uploads/media/image/2026/11/13/test_processed.jpg"

        # 模型 mock：record_work 无类型（复刻 939 现场模型输出——
        # actions=[record_work] + work_content 抽出 + record_type=null）
        self.answers = []

        async def stream_gen(messages, **kwargs):
            answer = self.answers.pop(0) if self.answers else _router_reply("好")
            yield {"type": "config", "config": SimpleNamespace(model_id="test")}
            yield {"type": "content", "text": answer}

        async def fake_chat(messages, **kwargs):
            answer = self.answers.pop(0) if self.answers else _router_reply("好")
            return answer, SimpleNamespace(model_id="test")

        self._orig_stream = agent_router.model_gateway.stream_chat_completion
        self._orig_chat = agent_router.model_gateway.chat_completion
        agent_router.model_gateway.stream_chat_completion = stream_gen
        agent_router.model_gateway.chat_completion = fake_chat

        async def noop(*a, **k):
            return "req-test"

        async def noop_event(**k):
            return None

        async def noop_finish(**k):
            return None

        self._orig_rt = {
            "start_request": agent_router.agent_runtime.start_request,
            "append_progress_event": agent_router.agent_runtime.append_progress_event,
            "finish_request": agent_router.agent_runtime.finish_request,
        }
        agent_router.agent_runtime.start_request = noop
        agent_router.agent_runtime.append_progress_event = noop_event
        agent_router.agent_runtime.finish_request = noop_finish

    async def asyncTearDown(self):
        agent_router.model_gateway.stream_chat_completion = self._orig_stream
        agent_router.model_gateway.chat_completion = self._orig_chat
        for key, value in self._orig_rt.items():
            setattr(agent_router.agent_runtime, key, value)
        await self.engine.dispose()

    async def _chat(self, message, conv, history=None):
        req = agent_router.AgentChatRequest(
            message=message, request_id="req-tbd-test",
            conversation_id=conv, history=history or [],
        )
        async with self.session_factory() as db:
            return await agent_router.agent_chat(req, _fake_user(), db)

    async def test_spoken_record_with_content_falls_to_tbd(self):
        """user 32 现场 939 复刻：口述「记录现场工作」+ 有内容 → 落待定，不问类型。"""
        self.answers.append(_router_reply(
            "收到，先记下来，回头再分类。",
            record_type=None,
            facts=[{"field": "work_content", "value": "拍照记录工序",
                    "source_span": "记录现场工作", "confidence": 0.9}],
        ))
        # 模型 actions 里没有 record_work（被带偏成 chat），bug #4 兜底会注入
        payload = json.loads(self.answers[0])
        payload["actions"] = []
        payload["intent"] = "chat"
        self.answers[0] = json.dumps(payload, ensure_ascii=False)

        r = await self._chat("记录现场工作", "wx-tbd-1")
        plan = r.get("request_plan") or {}
        print("kind:", r.get("kind"), "| tool:", r.get("tool"),
              "| need_more:", r.get("need_more")[:3] if r.get("need_more") else [])
        print("reply:", str(r.get("reply") or "")[:100])
        # 落待定 → 不再问「记录类型」
        self.assertNotIn("记录类型", [str(x) for x in (r.get("need_more") or [])])
        self.assertNotIn("整理成", str(r.get("reply") or ""))

    async def test_photo_only_without_speech_keeps_asking(self):
        """11-24 反馈保护：前端自动请求（无口述）仍问类型。

        复刻生产 595 现场：前端送图时自动发的「用户上传资料，尚未说明整理
        用途」（images 非空 + 无建记录口述）→ 必须保持类型问句，不能静默
        落待定（11-24 用户反馈「照片类型没判断出来就建成待定」）。
        """
        # 模型形态对齐真实现场：识别 record_work（actions 非空）+ 类型不明
        self.answers.append(_router_reply("先确认一下类型。", record_type=None))
        payload = json.loads(self.answers[0])
        payload["intent"] = "create_record"
        payload["actions"] = [{"id": "record_work", "confidence": 0.85}]
        self.answers[0] = json.dumps(payload, ensure_ascii=False)

        # 图片归属行（agent_chat L674 校验 images 必须属于当前账号/项目）
        async with self.session_factory() as db:
            db.add(MediaRecord(
                project_id=7, uploader_id=41, media_type="image",
                source="miniprogram", original_path=self.test_image,
                processed_path=self.test_image, mime_type="image/jpeg",
                size_bytes=1024, file_hash="hash-tbd-test",
                idempotency_key="idem-tbd-test",
            ))
            await db.commit()

        req = agent_router.AgentChatRequest(
            message="用户上传资料，尚未说明整理用途", request_id="req-tbd-2",
            conversation_id="wx-tbd-2", history=[],
            images=[self.test_image],
        )
        async with self.session_factory() as db:
            r = await agent_router.agent_chat(req, _fake_user(), db)
        # 无口述 → 保持问句路径（needs_record_type）
        need = [str(x) for x in (r.get("need_more") or [])]
        reply = str(r.get("reply") or "")
        ok = "记录类型" in need or "整理成" in reply
        print("need_more:", need, "| reply:", reply[:80])
        self.assertTrue(ok, f"应保持问类型: need_more={need} reply={reply[:80]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
