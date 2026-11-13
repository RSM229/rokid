# -*- coding: utf-8 -*-
"""A 层零必填「照片 + 你先记着 → 直达 /agent/chat 落待定」链路测试（2026-11-13）。

真因（第三次复测才定位）：小程序带图发消息时，前端 sendAttachments 先调
/agent/request-plan，再用 wantsRecordPlan 判定：
  images.length > 0 && (actions 含 record_work/archive_evidence || execution_mode==='clarify')
命中就走 /agent/review-plans「候选事项 → 待确认草稿 → 追问记录类型」，在前端直接 return，
**根本不调用 /agent/chat**。所以只改 chat 路由 / agent_tools 永远不生效。

本测试覆盖：
  1) wants_zero_required_stash 判定矩阵（命中/不误伤）；
  2) apply_zero_required_stash 改写后 plan 不再触发前端 wantsRecordPlan；
  3) 明确记录类型 / 多图分组整理 / 只发图不说话 等场景保持原链路。

跑法：
  cd D:\\rokid\\rokid\\cloud-backend
  .venv\\Scripts\\python.exe -m unittest tests.test_agent_zero_required_stash -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import agent_request_plan as arp


def _plan(record_type=None, actions=None, execution_mode="clarify"):
    """构造一个典型的「带图 + 类型不明」规划结果（正是截图里的形态）。"""
    return {
        "schema_version": 2,
        "intent": "create_record",
        "actions": actions if actions is not None else [
            {"id": "record_work", "confidence": 0.9}
        ],
        "record_type": record_type,
        "clarifications": [
            {"field": "record_type", "question": "这次要整理成哪一类工作记录？"}
        ],
        "execution_mode": execution_mode,
        "selected_tool": None,
        "needs_hazard_analysis": True,
        "degraded": False,
    }


def frontend_wants_record_plan(plan: dict, images_count: int = 2) -> bool:
    """复刻小程序 index.ts:1540 的判定，用来验证改写后的 plan 能落到 /agent/chat。"""
    action_ids = {str(a.get("id") or "") for a in (plan.get("actions") or [])}
    return images_count > 0 and (
        "record_work" in action_ids
        or "archive_evidence" in action_ids
        or plan.get("execution_mode") == "clarify"
    )


class WantsZeroRequiredStashTests(unittest.TestCase):
    """判定矩阵：只在「带图 + 无明确类型 + 明确暂缓措辞」时命中。"""

    def test_hit_exact_user_message(self):
        """【用户真机原话】「拍了几张照片，你先记着」+ 2 图 → 命中。"""
        self.assertTrue(arp.wants_zero_required_stash("拍了几张照片，你先记着", True, None))

    def test_hit_common_stash_phrases(self):
        for text in ("你先记着", "先记一下", "先存着吧", "回头再整理", "暂时不确定，先放着"):
            with self.subTest(text=text):
                self.assertTrue(arp.wants_zero_required_stash(text, True, None), text)

    def test_no_images_never_hits(self):
        """纯文字走 /agent/chat 原有 A 层，不需要改写 plan。"""
        self.assertFalse(arp.wants_zero_required_stash("你先记着", False, None))

    def test_explicit_record_type_never_hits(self):
        """规划器已识别出类型 → 正常建单，不绕过。"""
        self.assertFalse(arp.wants_zero_required_stash("你先记着", True, "巡视"))

    def test_message_with_type_word_never_hits(self):
        """「先记着，这是隐蔽验收」→ 用户其实给了类型线索，保留整理计划。"""
        self.assertFalse(arp.wants_zero_required_stash("先记着，这是隐蔽验收照片", True, None))
        self.assertFalse(arp.wants_zero_required_stash("先记着，回头做巡视记录", True, None))

    def test_empty_message_never_hits(self):
        """🔴 只发图不说话 = 既有「多图分组整理」场景，绝不能误伤。"""
        self.assertFalse(arp.wants_zero_required_stash("", True, None))
        self.assertFalse(arp.wants_zero_required_stash("   ", True, None))

    def test_grouping_intent_never_hits(self):
        """「按水印分组整理」是多图分流，保留 review-plans。"""
        self.assertFalse(arp.wants_zero_required_stash("这些照片按水印分组整理", True, None))

    def test_normal_chat_never_hits(self):
        self.assertFalse(arp.wants_zero_required_stash("今天天气怎么样", True, None))


class ApplyZeroRequiredStashTests(unittest.TestCase):
    """改写后的 plan 必须让前端 wantsRecordPlan=false，并带上 chat 侧标记。"""

    def test_rewritten_plan_bypasses_review_plan_branch(self):
        plan = _plan()
        self.assertTrue(frontend_wants_record_plan(plan), "改写前确实会被前端劫持")
        self.assertTrue(arp.apply_zero_required_stash(plan, "拍了几张照片，你先记着", True))
        self.assertFalse(frontend_wants_record_plan(plan), "改写后必须落到 /agent/chat")
        self.assertTrue(plan.get("stash_as_pending"))
        self.assertEqual(plan.get("execution_mode"), "single_tool")
        self.assertEqual(plan.get("actions"), [])
        self.assertFalse(plan.get("needs_hazard_analysis"))
        self.assertIsNone(plan.get("record_type"))

    def test_non_stash_plan_untouched(self):
        """明确类型 / 只发图 / 分组整理 → plan 一字节不改，前端仍走整理计划。"""
        cases = [
            ("帮我记一条巡视，3号楼二层", _plan(record_type="巡视")),
            ("", _plan()),
            ("按水印分组整理", _plan()),
        ]
        for message, plan in cases:
            with self.subTest(message=message or "<empty>"):
                before = dict(plan)
                self.assertFalse(arp.apply_zero_required_stash(plan, message, True))
                self.assertEqual(plan, before)
                self.assertTrue(frontend_wants_record_plan(plan))

    def test_no_images_keeps_plan(self):
        plan = _plan()
        self.assertFalse(arp.apply_zero_required_stash(plan, "你先记着", False))
        self.assertFalse(plan.get("stash_as_pending"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
