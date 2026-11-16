"""回归：分类/分组建档后必须逐类提醒「这一类要填什么」，并带出对应内容/验收要点请用户确认。

2026-11-16 真机录屏（沙盛四路等四条道路工程，6 张水印照片）：
点「确认生成」后只回「已按分组创建以下记录…具体位置等可继续在『工作记录』页补充」，
用户不知道巡视要填巡视内容、验收要填验收要点，也没机会确认是否符合现场。
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.models.work_record import WorkRecord
from app.services import acceptance_ask_together as aat
from app.services import agent_tools
from app.services import multi_image_split as mis
from app.services import split_record_confirm as src

ROOT = Path(__file__).resolve().parents[1]


class FakeDb:
    def __init__(self):
        self.rows = []

    def add(self, row):
        if isinstance(row, WorkRecord):
            row.id = len(self.rows) + 1
        self.rows.append(row)

    async def flush(self):
        return None

    async def commit(self):
        return None


def _context():
    return agent_tools.ToolContext(
        db=FakeDb(),
        user=SimpleNamespace(id=4, username="tester", project_id=7,
                             has_permission=lambda _p: True),
        project_id=7,
        project=SimpleNamespace(name="沙盛四路等四条道路工程", construction_unit="总包A",
                                owner_unit="建设单位A", supervision_unit="监理单位A",
                                supervision_org="项目监理部"),
        message="确认生成", material="",
    )


def _group(rtype, hints, section="", n_images=1):
    return mis.ImageGroup(
        record_type=rtype,
        images=[f"https://img/{rtype}/{i}" for i in range(n_images)],
        hints=list(hints),
        wm=[{"section": section, "taken_at": "2026-10-27 16:07:00",
             "weather": "多云 30℃"}],
    )


def _video_groups():
    """录屏里的 6 组：巡视1 / 旁站3 / 验收1 / 见证1。"""
    return [
        _group("巡视", ["水泥搅拌桩施工"], "建材纵一路"),
        _group("旁站", ["基础混凝土浇筑"], "沙盛四路"),
        _group("旁站", ["给水管道安装"], "沙盛四路"),
        _group("旁站", ["底板钢筋模板安装"], "大兴路"),
        _group("验收", ["底板钢筋模板验收"], "大兴路", 3),
        _group("见证", ["钢筋原材取样"], "天头路"),
    ]


class TypeConfirmationTextTest(unittest.TestCase):
    def test_created_receipt_shows_each_type_content_and_pending_fields(self):
        text = src.build_type_confirmation(
            _video_groups(), display=agent_tools._split_record_display_name,
            created_ids={"巡视": [127], "旁站": [128, 129, 130],
                         "验收": [131], "见证": [132]},
            stage="created",
        )
        self.assertIn("【巡视检查记录 #127】 现场识别：水泥搅拌桩施工", text)
        self.assertIn("巡视内容（GD-B-214）", text)
        self.assertIn("特种作业人员是否持证上岗", text)
        self.assertIn("【旁站监理记录 #128 #129 #130】", text)
        self.assertIn("旁站内容（GD-B-212／范本）", text)
        self.assertIn("【验收记录 #131】", text)
        self.assertIn("验收要点（省统表", text)
        self.assertIn("① 核查", text)
        self.assertNotIn("① ①", text)  # 要点原文自带序号，不能套两层
        self.assertIn("待填：验收类型", text)
        self.assertIn("【见证取样记录 #132】", text)
        self.assertIn("请确认是否符合现场情况", text)
        self.assertIn("写入正式记录", text)

    def test_plan_stage_asks_to_confirm_classification(self):
        text = src.build_type_confirmation(
            _video_groups(), display=agent_tools._split_record_display_name,
            stage="plan")
        self.assertIn("巡视内容（GD-B-214）", text)
        self.assertIn("点「确认生成」", text)
        self.assertNotIn("#127", text)

    def test_watermark_section_is_not_asked_again(self):
        groups = _video_groups()
        with_section = src.build_type_confirmation(
            [groups[0]], display=lambda rt: f"{rt}记录", stage="created")
        self.assertNotIn("待填：巡视的工程部位", with_section)
        self.assertIn("待填：巡视开始时间", with_section)

        g = _group("巡视", ["水泥搅拌桩施工"], section="")
        no_section = src.build_type_confirmation(
            [g], display=lambda rt: f"{rt}记录", stage="created")
        self.assertIn("巡视的工程部位", no_section)

    def test_fields_and_xunshi_items_come_from_form_spec(self):
        items = src._xunshi_items()
        self.assertEqual(5, len(items))
        self.assertIn("施工单位是否按工程设计文件", items[0])
        secs = src._pangzhan_sections()
        self.assertEqual(["施工情况", "监理情况", "问题与处理"], secs)
        self.assertEqual(["巡视开始时间"], src._pending_labels(
            "巡视", has_section=True, has_photo=True, has_points=False))

    def test_unknown_type_and_empty_groups_are_safe(self):
        self.assertEqual("", src.build_type_confirmation([]))
        text = src.build_type_confirmation([_group("待定", [])])
        self.assertIn("待确认归类后补全", text)
        self.assertIn("请确认是否符合现场情况", text)


class AcceptancePointsMatchTest(unittest.TestCase):
    def test_keywords_match_procedure_and_points(self):
        proc, points = aat.match_points_by_text("底板钢筋模板验收")
        self.assertTrue(proc)
        self.assertEqual(3, len(points))
        self.assertTrue(all(p.startswith(("①", "②", "③")) for p in points))

    def test_pipeline_and_plain_text(self):
        proc, points = aat.match_points_by_text("给水管道安装")
        self.assertIn("管道", proc)
        self.assertTrue(points)
        self.assertEqual(("", []), aat.match_points_by_text("随便说一句"))

    def test_unmatched_text_yields_friendly_fallback(self):
        text = src.build_type_confirmation(
            [_group("验收", ["随便说一句"], "大兴路")],
            display=lambda rt: f"{rt}记录")
        self.assertIn("暂未匹配到这道工序的规范要点", text)


class SplitReceiptIntegrationTest(unittest.TestCase):
    def test_create_split_records_reply_contains_confirmation(self):
        result = asyncio.run(agent_tools._create_split_records(_context(), _video_groups()))
        self.assertTrue(result.get("ok"), result)
        reply = str(result.get("reply") or "")
        self.assertIn("已按分组创建以下记录", reply)
        self.assertIn("巡视内容（GD-B-214）", reply)
        self.assertIn("验收要点（省统表", reply)
        self.assertIn("请确认是否符合现场情况", reply)
        self.assertNotIn("具体位置等可继续", reply)
        self.assertTrue(result.get("data", {}).get("type_confirmation"))
        self.assertEqual(6, len(result.get("cards") or []))

    def test_split_need_more_reply_and_ask_block_carry_confirmation(self):
        result = agent_tools._split_need_more(_video_groups(), material="", n_images=0)
        reply = str(result.get("reply") or "")
        self.assertIn("这批图我分了", reply)
        self.assertIn("巡视内容（GD-B-214）", reply)
        self.assertIn("点「确认生成」", reply)
        self.assertTrue(result.get("ask_block", {}).get("type_confirmation"))

    def test_router_never_overwrites_split_receipt_with_model_answer(self):
        router_src = (ROOT / "app/routers/agent.py").read_text(encoding="utf-8")
        self.assertIn('_result_data.get("split")', router_src)


if __name__ == "__main__":
    unittest.main()
