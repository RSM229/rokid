"""doc_quality：缺项规则与独立复核的降级行为（不连库、不调真实模型）。"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from app.services import doc_quality


class MissingFieldsTests(unittest.TestCase):
    def test_placeholder_counts_as_missing(self):
        items = doc_quality.missing_fields("巡视", {"section": "（待补）"})
        hit = [x for x in items if x["key"] == "section"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["reason"], "placeholder")
        self.assertTrue(hit[0]["required"])

    def test_real_value_not_missing(self):
        items = doc_quality.missing_fields("巡视", {"section": "3号楼五层顶板"})
        self.assertFalse([x for x in items if x["key"] == "section"])

    def test_no_issue_conclusion_is_real_content(self):
        # jl 的 placeholders 含「无」，但监理通 problems 里「无问题/符合要求」
        # 是有效结论（见 agent_tools._backfill_no_issue），不能判成缺项。
        self.assertNotIn("无", doc_quality.PLACEHOLDERS)
        blank, _ = doc_quality._is_blank("无问题，符合要求")
        self.assertFalse(blank)

    def test_auto_and_signoff_are_not_asked(self):
        items = doc_quality.missing_fields("巡视", {})
        self.assertTrue(items)
        self.assertFalse(any("签名" in x["label"] for x in items))
        self.assertFalse(any(x["label"] == "编号" for x in items))

    def test_pending_draft_never_asks_record_type(self):
        # A 层零必填：类型不明先存「待定」，不能反过来追问记录类型。
        items = doc_quality.missing_fields("待定", {})
        self.assertTrue(items)
        self.assertFalse(any("记录类型" in x["label"] for x in items))
        self.assertTrue(all(x["required"] is False for x in items))

    def test_summary_states_no_blocking(self):
        summary = doc_quality.missing_summary(doc_quality.missing_fields("待定", {}))
        self.assertIn("不影响保存", summary)
        self.assertNotIn("请补充", summary)
        self.assertNotIn("？", summary)

    def test_empty_summary(self):
        self.assertEqual(doc_quality.missing_summary([]), "")


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled(self):
        with patch.dict(os.environ, {"DOC_REVIEW_ENABLED": "0"}):
            out = await doc_quality.review_document(
                {"record_type": "巡视", "section": "3#5F"}, [],
            )
        self.assertEqual(out["status"], "disabled")
        self.assertEqual(out["items"], [])

    async def test_skips_when_only_record_type(self):
        # jl 踩过「复核员被告知缺项：无」，没有实质内容就不烧配额。
        out = await doc_quality.review_document({"record_type": "巡视"}, [])
        self.assertEqual(out["status"], "skipped")

    async def test_parses_independent_review(self):
        raw = (
            '```json\n{"items":[{"level":"must_fix","text":"依据栏为空，需补规范条号"},'
            '{"level":"suggest","text":"事由与结论表述重复"}]}\n```'
        )
        with patch(
            "app.services.model_gateway.chat_completion",
            new=AsyncMock(return_value=(raw, None)),
        ):
            out = await doc_quality.review_document(
                {"record_type": "旁站", "section": "3#5F 顶板", "problems": "未写依据"}, [],
            )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(out["items"][0]["level"], "must_fix")

    async def test_unparsed_output_degrades(self):
        with patch(
            "app.services.model_gateway.chat_completion",
            new=AsyncMock(return_value=("我觉得还行", None)),
        ):
            out = await doc_quality.review_document(
                {"record_type": "巡视", "section": "3#5F"}, [],
            )
        self.assertEqual(out["items"], [])
        self.assertEqual(out["status"], "unparsed")

    async def test_timeout_degrades(self):
        async def slow(*_args, **_kwargs):
            await asyncio.sleep(1)
            return ("", None)

        with patch.dict(os.environ, {"DOC_REVIEW_TIMEOUT": "0.01"}), patch(
            "app.services.model_gateway.chat_completion",
            new=AsyncMock(side_effect=slow),
        ):
            out = await doc_quality.review_document(
                {"record_type": "巡视", "section": "3#5F"}, [],
            )
        self.assertEqual(out["items"], [])
        self.assertEqual(out["status"], "timeout")

    async def test_model_failure_degrades(self):
        with patch(
            "app.services.model_gateway.chat_completion",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            out = await doc_quality.review_document(
                {"record_type": "巡视", "section": "3#5F"}, [],
            )
        self.assertEqual(out["status"], "failed")


class EnrichTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_both_without_touching_business_data(self):
        result = {
            "ok": True,
            "reply": "已记录：巡视｜3#5F",
            "data": {"record_id": 7, "photo_count": 0},
            "card": {"title": "巡视记录"},
        }
        with patch.dict(os.environ, {"DOC_REVIEW_ENABLED": "0"}):
            out = await doc_quality.enrich_action_result(
                result, {"record_type": "巡视", "section": "3#5F"}, "",
            )
        self.assertTrue(out["missing_fields"])
        self.assertIn("不影响保存", out["reply"])
        self.assertEqual(out["review"]["status"], "disabled")
        self.assertEqual(out["data"]["record_id"], 7)
        self.assertTrue(out["reply"].startswith("已记录：巡视"))

    async def test_failed_action_untouched(self):
        result = {"ok": False, "reply": "没建成", "data": {}}
        out = await doc_quality.enrich_action_result(result, {}, "")
        self.assertNotIn("missing_fields", out)
        self.assertNotIn("review", out)

    async def test_global_switch_off(self):
        result = {"ok": True, "reply": "已记录", "data": {"record_id": 1}}
        with patch.dict(os.environ, {"DOC_QUALITY_ENABLED": "0"}):
            out = await doc_quality.enrich_action_result(result, {}, "")
        self.assertNotIn("missing_fields", out)
        self.assertNotIn("review", out)


if __name__ == "__main__":
    unittest.main()
