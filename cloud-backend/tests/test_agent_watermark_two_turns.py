# -*- coding: utf-8 -*-
"""🔴 K05 场景端到端：带水印照片 +「记录今天工作」的两轮链路。

跑法：cd cloud-backend && python -m unittest tests.test_agent_watermark_two_turns -v

为什么要有这个测试
------------------
agent_eval 跑分器只测「模型选工具 + 抽参数」这一层，测不到执行层
（stash_pending / apply_prefill）。K05 用例的期望写的是 2026-10-19 的旧规则
「带素材 → 默认巡视直接建单」，而 e3a8bc8（2026-10-21）有意反转成
「素材只提供证据，类型不明时保留澄清」，并在 agent.py:1001-1006 把水印
prefill 存进草稿，等用户回答类型后再自动填入。

所以「模型第一轮追问类型」是**当前设计**，不是缺陷。真正必须保证的是
10-19 用户抱怨的那个点：系统别「什么都没填」——水印读到的客观字段
（拍摄日期/地点/部位/天气）要在用户答完类型后**自动落进表单**，
且用户没说的栏目一个都不许编造。这条执行层链路跑分器测不到，本测试补上。

链路（agent.py）：
  轮1  needs_record_type → tool="" → stash_pending(draft_args["_prefill"]=水印)
  轮2  get_pending → draft_prefill 恢复(961-963) → canonical_record_type("巡视")
       → tool=create_work_record(976-980) → apply_prefill(1008-1021)
"""
from __future__ import annotations

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_draft import AgentConversationDraft, AgentDraft
from app.routers import agent as agent_router
from app.services import agent_request_plan, agent_tools

# K05 用例的真实水印素材（cases.py:632-635）
WATERMARK_MATERIAL = (
    "【图片 1】\n【水印】拍摄时间：2026-10-19 15:42；地点：广州市黄埔区XX路；"
    "部位：3号楼二层东侧；天气：晴\n未识别到明显安全隐患。"
)

# 前端 watermarkPrefill 把水印识别结果压成的 prefill dict
PREFILL = {
    "taken_at": "2026-10-19 15:42",
    "location": "广州市黄埔区XX路",
    "section": "3号楼二层东侧",
    "weather": "晴",
}

USER_ID = 7
CONV_ID = "wx-k05"


class WatermarkTwoTurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(AgentDraft.__table__.create)
            await conn.run_sync(AgentConversationDraft.__table__.create)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def turn1_stash_evidence_draft(self) -> dict:
        """轮1：类型不明 → 清空 tool，把水印存进草稿（复刻 agent.py:993-1006）。"""
        args = {"section": "3号楼二层东侧"}   # 模型从水印素材抽到的客观字段
        draft_args = dict(args)
        draft_args.pop("record_type", None)
        draft_args["_prefill"] = PREFILL
        await agent_tools.stash_pending(
            self.db, USER_ID, "create_work_record", draft_args, ["记录类型"],
            message="记录今天工作", conversation_id=CONV_ID,
        )
        return draft_args

    async def test_turn1_draft_keeps_watermark_prefill(self):
        """轮1 存草稿后，水印 prefill 必须完整活着（不能被 _draft_clean_args 滤掉）。"""
        await self.turn1_stash_evidence_draft()
        pending = await agent_tools.get_pending(
            self.db, USER_ID, "create_work_record", CONV_ID)
        self.assertIsNotNone(pending, "草稿没存进去")
        tool, draft = pending
        self.assertEqual(tool, "create_work_record")
        self.assertIn("_prefill", draft["args"],
                      "_prefill 被 _draft_clean_args 滤掉了 → 第二轮水印会全丢")
        self.assertEqual(draft["args"]["_prefill"]["taken_at"], "2026-10-19 15:42")
        self.assertEqual(draft["missing"], ["记录类型"])

    async def test_turn2_answer_type_restores_watermark_into_args(self):
        """🔴 核心：用户只回「巡视」，水印的日期/部位/地点/天气/时间要自动填进去。

        这就是 10-19 用户抱怨「什么都没填就反问」的修复保证 ——
        追问类型是设计，但追问之后必须自动带入水印，不能让用户重说一遍。
        """
        await self.turn1_stash_evidence_draft()
        pending = await agent_tools.get_pending(
            self.db, USER_ID, "create_work_record", CONV_ID)
        self.assertIsNotNone(pending)
        _, draft = pending

        message = "巡视"
        # 复刻 agent.py:961-963 —— 本轮没带素材，从草稿恢复 prefill
        prefill = {}
        draft_prefill = (draft.get("args") or {}).get("_prefill")
        self.assertIsInstance(draft_prefill, dict, "草稿里没恢复出 prefill")
        if not prefill and isinstance(draft_prefill, dict):
            prefill = draft_prefill

        # 复刻 agent.py:976-980 —— 只回类型是确定性转换，不靠模型猜表式
        candidate = agent_request_plan.canonical_record_type(message)
        self.assertEqual(candidate, "巡视")
        args = {**(draft.get("args") or {}), "record_type": candidate}

        # 复刻 agent.py:1008-1021
        rtype = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
        self.assertEqual(rtype, "巡视")
        args, auto_filled = agent_router.apply_prefill(args, prefill, rtype)
        args.pop("_prefill", None)   # agent.py:1045

        self.assertEqual(args["record_type"], "巡视")
        self.assertEqual(args["record_date"], "2026-10-19", "水印拍摄日期没填")
        self.assertEqual(args["section"], "3号楼二层东侧", "水印部位没填")
        self.assertEqual(args["location"], "广州市黄埔区XX路", "水印地点没填")
        self.assertEqual(args["weather"], "晴", "水印天气没填")
        self.assertEqual(args["inspection_start_time"], "15:42",
                         "巡视开始时间没按水印拍摄时间填")
        self.assertTrue(auto_filled, "auto_filled 为空 → 前端无法告知用户自动填了哪些栏")
        self.assertNotIn("_prefill", args, "_prefill 是上下文，绝不能当业务字段落库")

    async def test_turn2_never_fabricates_fields_user_did_not_state(self):
        """🔴 用户只回了「巡视」两个字，除 record_type 和水印客观字段外
        一个栏目都不许凭空补（编造字段会直接写进正式监理文书）。"""
        await self.turn1_stash_evidence_draft()
        _, draft = await agent_tools.get_pending(
            self.db, USER_ID, "create_work_record", CONV_ID)
        args = {**(draft.get("args") or {}), "record_type": "巡视"}
        args, _ = agent_router.apply_prefill(args, draft["args"]["_prefill"], "巡视")
        args.pop("_prefill", None)

        for forbidden in ("work_content", "problems", "treatment",
                          "worker_count", "inspection_end_time"):
            self.assertNotIn(forbidden, args,
                             f"用户没说过 {forbidden}，却被填了值 → 编造")
        self.assertNotIn("_context", args, "_context 是草稿内部上下文，不该进业务参数")

    async def test_watermark_objective_fields_win_over_user_restatement(self):
        """水印是客观证据：与用户转述冲突时以照片为准（apply_prefill objective=True）。"""
        args = {"section": "用户口误说的西区", "record_type": "巡视"}
        out, filled = agent_router.apply_prefill(args, PREFILL, "巡视")
        self.assertEqual(out["section"], "3号楼二层东侧",
                         "部位冲突时没采用照片水印")
        self.assertTrue(any("改用照片水印" in f for f in filled),
                        "冲突替换没记进 auto_filled，用户无从得知被改了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
