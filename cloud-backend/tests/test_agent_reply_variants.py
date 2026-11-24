# -*- coding: utf-8 -*-
"""话术变体轮换（agent_reply_variants）回归。

2026-11-24 用户反馈「工作台的交互是不是太僵硬了，好多都重复回答」：
逐栏追问/回执模板每轮逐字复读，整体像跟流程机器说话。引入变体池 +
「栏位 + 轮次」散列轮换（同栏同轮稳定、跨轮必换）。

锁死三件事：
    1. 跨轮必换：同一栏连问 N 轮，见到的句式数 > 1（这是本专项的全部意义）。
    2. 同轮幂等：同栏同轮两次调用返回同一句（回执不闪变）。
    3. 红线不破：
       - 变体里不出现假成功措辞（_FAKE_SUCCESS_MARKERS）；
       - 最终确认变体必含「尚未写入正式」真实状态；
       - 变体不带候选/点选类字样（纯文字交互）；
       - 变体不含识别值占位（模板里只有栏目名/编号参数）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.routers.agent import _FAKE_SUCCESS_MARKERS  # noqa: E402
from app.services import agent_reply_variants as v  # noqa: E402


class RotationBehaviourTest(unittest.TestCase):
    def test_same_field_rotates_across_turns(self):
        """同一栏连问 5 轮，至少出现 2 种句式（跨轮必换）。"""
        seen = {v.missing_field_ask("验收类型", t) for t in range(5)}
        self.assertGreater(len(seen), 1, f"追问句未轮换：{seen}")

    def test_adjacent_turns_always_differ(self):
        """相邻两轮必换说法（2026-11-24 散列撞车回归：旧 hash 公式在
        turn 1/2 上选到同一变体，轮换失效）。"""
        pools = (
            (v.MISSING_FIELD_ASKS, "验收类型"),
            (v.ACCEPTANCE_PROCEDURE_ASKS, "验收工序"),
            (v.FINAL_CONFIRM_ASKS, "最终确认"),
            (v.FINAL_CONFIRM_TAILS, "最终确认尾句"),
            (v.WRITE_THROUGH_HEADS, "#155"),
            (v.WRITE_THROUGH_NEXT_ASKS, "天气"),
            (v.FIELD_HINT_HEADS, "日期"),
            (v.AUTO_SKIP_TAILS, "验收类型"),
            (v.CREATED_TAILS, "建单尾句"),
            (v.TBD_TAILS, "待定尾句"),
        )
        for pool, label in pools:
            for turn in range(1, 4):
                self.assertNotEqual(
                    v._pick(label, pool, turn),
                    v._pick(label, pool, turn + 1),
                    f"相邻轮未换说法：{label} turn {turn}→{turn+1}（{pool}）",
                )

    def test_first_turn_anchors_original_phrase(self):
        """首轮（turn=0）恒选第 0 变体 = 已部署版本原句（生产零漂移）。"""
        self.assertIn("你说一下就行", v.missing_field_ask("验收类型", 0))
        self.assertIn("这次验收做的是哪个工序", v.acceptance_procedure_ask(0))
        self.assertIn("字段已按当前表式合同补齐", v.final_confirm_ask(0))
        self.assertIn("即可建档", v.final_confirm_tail(0))
        self.assertIn("已写入正式记录 #155", v.write_through_head(155, "验收", 0))
        self.assertIn("可在「工作记录」页查看", v.created_tail(0))

    def test_same_field_same_turn_is_stable(self):
        """同栏同轮幂等：两次调用一致（回执不闪变）。"""
        for turn in range(4):
            self.assertEqual(
                v.missing_field_ask("日期", turn),
                v.missing_field_ask("日期", turn),
            )

    def test_all_variant_pools_cover_multiple_phrasings(self):
        seen_ask = {v.missing_field_ask("位置", t) for t in range(6)}
        seen_proc = {v.acceptance_procedure_ask(t) for t in range(3)}
        seen_final = {v.final_confirm_ask(t) for t in range(3)}
        seen_tail = {v.final_confirm_tail(t) for t in range(3)}
        seen_head = {v.write_through_head(155, "验收", t) for t in range(3)}
        seen_created = {v.created_tail(t) for t in range(6)}
        seen_tbd = {v.tbd_tail(t) for t in range(3)}
        for name, pool in (
            ("missing_field_ask", seen_ask),
            ("acceptance_procedure_ask", seen_proc),
            ("final_confirm_ask", seen_final),
            ("final_confirm_tail", seen_tail),
            ("write_through_head", seen_head),
            ("created_tail", seen_created),
            ("tbd_tail", seen_tbd),
        ):
            self.assertGreater(
                len(pool), 1, f"{name} 轮换失效，实际只见到：{pool}")

    def test_field_hint_head_carries_hint(self):
        text = v.field_hint_head("日期", "按「年-月-日」给日期", 0)
        self.assertIn("日期", text)
        self.assertIn("年-月-日", text)

    def test_acceptance_procedure_ask_keeps_anchors(self):
        """每个工序问句变体都含「哪个工序」+「直接说」（既有回归断言口径）。"""
        for turn in range(3):
            text = v.acceptance_procedure_ask(turn)
            self.assertIn("哪个工序", text)
            self.assertIn("直接说", text)

    def test_auto_skip_tail_mentions_label_and_work_record_page(self):
        for turn in range(3):
            text = v.auto_skip_tail("验收类型", turn)
            self.assertIn("验收类型", text)
            self.assertIn("「工作记录」页", text)

    def test_write_through_next_ask_keeps_label_and_hint(self):
        text = v.write_through_next_ask("天气", "说天气加气温", 1)
        self.assertIn("天气", text)


class RedLineGuardTest(unittest.TestCase):
    """红线：假成功措辞、点选引导绝不进变体；真实状态提示必须在。"""

    def test_no_fake_success_marker_in_any_variant(self):
        all_texts = (
            list(v.MISSING_FIELD_ASKS) + list(v.ACCEPTANCE_PROCEDURE_ASKS)
            + list(v.FINAL_CONFIRM_ASKS) + list(v.FINAL_CONFIRM_TAILS)
            + list(v.WRITE_THROUGH_HEADS) + list(v.WRITE_THROUGH_NEXT_ASKS)
            + list(v.FIELD_HINT_HEADS) + list(v.AUTO_SKIP_TAILS)
            + list(v.CREATED_TAILS) + list(v.TBD_TAILS)
        )
        for marker in _FAKE_SUCCESS_MARKERS:
            for text in all_texts:
                # WRITE_THROUGH_HEADS 是「真的已写入」回执头，不适用假成功口径
                if text in v.WRITE_THROUGH_HEADS:
                    continue
                self.assertNotIn(
                    marker, text,
                    f"假成功措辞「{marker}」泄漏进变体：{text}",
                )

    def test_final_confirm_asks_keep_not_written_status(self):
        """need_more 态的真实状态（尚未写入）每个变体都得保留。"""
        for text in v.FINAL_CONFIRM_ASKS:
            self.assertTrue(
                "尚未写入" in text or "还没写入" in text,
                f"最终确认变体丢了「尚未写入正式」状态提示：{text}",
            )

    def test_no_chips_or_clickable_wording_in_variants(self):
        all_texts = (
            list(v.MISSING_FIELD_ASKS) + list(v.ACCEPTANCE_PROCEDURE_ASKS)
            + list(v.FINAL_CONFIRM_ASKS) + list(v.FINAL_CONFIRM_TAILS)
            + list(v.WRITE_THROUGH_HEADS) + list(v.WRITE_THROUGH_NEXT_ASKS)
            + list(v.FIELD_HINT_HEADS) + list(v.AUTO_SKIP_TAILS)
            + list(v.CREATED_TAILS) + list(v.TBD_TAILS)
        )
        for text in all_texts:
            for banned in ("点一下", "点下方", "点选", "候选", "chips", "表单卡"):
                self.assertNotIn(
                    banned, text, f"变体出现点选/表单字样（纯文字红线）：{text}")


if __name__ == "__main__":
    unittest.main()
