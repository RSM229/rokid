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
        self.assertIn("【巡视检查记录 #127】", text)
        # 2026-11-16 用户「都要」：识别内容一律不写进回执，只留内部匹配要点
        self.assertNotIn("现场识别", text)
        self.assertNotIn("水泥搅拌桩施工", text)
        self.assertNotIn("钢筋原材取样", text)
        self.assertIn("巡视内容（GD-B-214）", text)
        self.assertIn("特种作业人员是否持证上岗", text)
        self.assertIn("【旁站监理记录 #128 #129 #130】", text)
        self.assertIn("旁站内容（GD-B-212／范本）", text)
        self.assertIn("【验收记录 #131】", text)
        self.assertIn("验收要点：", text)
        self.assertNotIn("省统表相近工序", text)  # 15:37 截图：括号里的工序名要去掉
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
        self.assertIn("验收要点：", reply)
        self.assertNotIn("省统表相近工序", reply)
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


class NoWatermarkValuesInSplitReplyTest(unittest.TestCase):
    """2026-11-16 15:14 真机截图：分组确认回执里仍逐张念「图片1：拍摄时间…」。"""

    MATERIAL = (
        "【图片 1】\n"
        "【水印】拍摄时间：2026-10-27 16:07:00\n地点：沙盛四路等4条道路工\n"
        "部位：建材纵一路\n天气：多云30℃\n施工内容：水泥搅拌桩施工\n"
        "【图片内容观察】水泥搅拌桩施工\n\n"
        "【图片 2】\n"
        "【水印】拍摄时间：2026-10-27 15:59:00\n地点：沙盛四路等4条道路工\n"
        "部位：大兴路\n天气：多云30℃\n施工内容：钢板桩支护安装\n"
        "【图片内容观察】钢板桩支护安装\n"
    )

    def test_split_need_more_does_not_print_watermark_fields(self):
        from app.services.agent_vision_signals import watermark_lines
        # 先证明素材确实能解析出逐张水印，避免「本来就没生成」造成假通过
        parsed = watermark_lines(self.MATERIAL, 2)
        self.assertTrue(any("拍摄时间：" in line for line in parsed))

        result = agent_tools._split_need_more(
            _video_groups(), material=self.MATERIAL, n_images=2)
        reply = str(result.get("reply") or "")
        for leaked in ("图片1：", "图片2：", "拍摄时间：", "地点：", "天气：",
                       "2026-10-27", "多云30℃", "沙盛四路等4条道路工",
                       "现场识别", "建材纵一路", "水泥搅拌桩施工"):
            self.assertNotIn(leaked, reply, "水印识别值仍被写出：" + leaked)
        # 「保留其他」：分组汇总、逐类待填内容/要点、确认引导一个都不能少
        self.assertIn("自动填入记录栏位", reply)  # 兼容旧版客户端的概括句
        self.assertIn("这批图我分了", reply)
        self.assertIn("巡视内容（GD-B-214）", reply)
        self.assertIn("点「确认生成」", reply)
        self.assertEqual([], result.get("ask_block", {}).get("watermark_lines"))


class NoProcedureLabelInAcceptanceTextTest(unittest.TestCase):
    """2026-11-16 15:37 截图「验收要点，括号里的要去掉吧」。

    匹配到的规范工序名（识别/匹配结果）不写进任何用户可见文本，
    但 content_json.acceptance_points.procedure 仍保留供内部追溯。
    """

    @staticmethod
    def _draft():
        return src.acceptance_points_for(
            [_group("验收", ["底板钢筋模板验收"], "大兴路")])

    def test_receipt_shows_points_without_procedure_bracket(self):
        text = src.build_type_confirmation(
            _video_groups(), display=agent_tools._split_record_display_name)
        draft = self._draft()
        self.assertIn("验收要点：①", text)
        self.assertNotIn("省统表相近工序", text)
        self.assertNotIn(draft["procedure"], text)
        self.assertIn("待填：", text)  # 其他内容照旧保留

    def test_draft_and_formal_text_hide_procedure_but_keep_it_internally(self):
        draft = self._draft()
        self.assertIn("procedure", draft)  # 内部字段仍在，不丢数据
        self.assertTrue(draft["procedure"])
        shown = src.acceptance_points_text(draft)
        self.assertIn("核查钢筋规格", shown)
        self.assertNotIn(draft["procedure"], shown)
        formal = src.build_acceptance_content_from_points(draft)
        self.assertNotIn(draft["procedure"], formal)
        self.assertIn("—— 符合", formal)
        self.assertIn("逐项核对，共 3 项", formal)

    def test_legacy_draft_with_procedure_prefix_is_stripped_on_display(self):
        draft = self._draft()
        legacy = dict(draft)
        legacy["text"] = f"省统表相近工序「{draft['procedure']}」：" + draft["text"]
        shown = src.acceptance_points_text(legacy)
        self.assertNotIn(draft["procedure"], shown)
        self.assertIn("核查钢筋规格", shown)

    def test_legacy_record_detail_hides_procedure(self):
        from app.routers import work_record as wr
        legacy = WorkRecord(record_type="验收", work_content="底板钢筋模板验收",
                            content_json={})
        text = wr._detail_acceptance_points_text(legacy)
        proc, _items = aat.match_points_by_text("底板钢筋模板验收")
        self.assertNotIn(proc, text)
        self.assertNotIn("省统表相近工序", text)
        self.assertIn("核查钢筋规格", text)
        self.assertIn("待确认", text)


class AcceptancePointsPersistTest(unittest.TestCase):
    """2026-11-16 用户截图「工作内容填的不是要点吗」：要点必须真的进记录。"""

    def test_points_draft_is_structured_and_matches_receipt(self):
        draft = src.acceptance_points_for([_group("验收", ["底板钢筋模板验收"], "大兴路", 3)])
        self.assertIsInstance(draft, dict)
        self.assertEqual(3, len(draft["points"]))
        self.assertIn("钢筋", draft["procedure"])
        self.assertIn("底板钢筋模板验收", draft["identified"])
        self.assertFalse(draft["confirmed"])
        self.assertIn("核查钢筋规格", src.acceptance_points_text(draft))
        self.assertEqual("", src.acceptance_points_text(None))
        self.assertEqual("", src.acceptance_points_text("bad"))

    def test_unmatched_procedure_does_not_invent_points(self):
        self.assertIsNone(src.acceptance_points_for(
            [_group("验收", ["随便说一句"], "大兴路")]))
        self.assertEqual("", src.build_acceptance_content_from_points(None))

    def test_confirmed_points_become_formal_content_with_conclusions(self):
        draft = src.acceptance_points_for([_group("验收", ["底板钢筋模板验收"], "大兴路")])
        text = src.build_acceptance_content_from_points(draft)
        self.assertIn("逐项核对，共 3 项", text)
        self.assertEqual(3, text.count("—— 符合"))
        self.assertIn("核查钢筋规格", text)
        # 用户说「不符合」时绝不能把要点写成「符合」
        self.assertEqual("", src.build_acceptance_content_from_points(
            draft, positive=False))

    def test_create_split_records_persists_points_and_keeps_work_content(self):
        ctx = _context()
        result = asyncio.run(agent_tools._create_split_records(ctx, _video_groups()))
        self.assertTrue(result.get("ok"), result)
        rows = [r for r in ctx.db.rows
                if isinstance(r, WorkRecord) and r.record_type == "验收"]
        self.assertEqual(1, len(rows))
        rec = rows[0]
        # 工序名保留在 work_content（共存，不因要点而丢）
        self.assertIn("底板钢筋模板验收", str(rec.work_content or ""))
        cj = rec.content_json or {}
        self.assertEqual(3, len((cj.get("acceptance_points") or {}).get("points") or []))
        # 草案不等于正式验收内容，未经用户确认不许静默写进去
        self.assertFalse(cj.get("acceptance_content"))

    def test_unmatched_group_has_no_points_key(self):
        ctx = _context()
        asyncio.run(agent_tools._create_split_records(
            ctx, [_group("验收", ["随便说一句"], "大兴路", 2)]))
        rec = next(r for r in ctx.db.rows
                   if isinstance(r, WorkRecord) and r.record_type == "验收")
        self.assertNotIn("acceptance_points", rec.content_json or {})

    def test_legacy_record_without_draft_still_shows_points(self):
        """本次改动前建的旧记录（用户截图那条）也要能看到要点，只展示不写库。"""
        from app.routers import work_record as wr
        legacy = WorkRecord(record_type="验收", work_content="底板钢筋模板验收",
                            content_json={})
        text = wr._detail_acceptance_points_text(legacy)
        self.assertIn("核查钢筋规格", text)
        self.assertIn("待确认", text)
        self.assertEqual("", wr._detail_acceptance_points_text(WorkRecord(
            record_type="验收", work_content="底板钢筋模板验收",
            content_json={"acceptance_content": "已确认内容"})))
        self.assertEqual("", wr._detail_acceptance_points_text(WorkRecord(
            record_type="巡视", work_content="水泥搅拌桩施工", content_json={})))

    def test_router_and_detail_and_wxml_wire_points(self):
        router_src = (ROOT / "app/routers/agent.py").read_text(encoding="utf-8")
        self.assertIn("build_acceptance_content_from_points", router_src)
        detail_src = (ROOT / "app/routers/work_record.py").read_text(encoding="utf-8")
        self.assertIn('"acceptance_points_text"', detail_src)
        wxml = (ROOT.parent / "wechat-miniprogram/miniprogram/pages/work/index.wxml").read_text(
            encoding="utf-8")
        self.assertIn("验收内容（验收要点）", wxml)
        self.assertIn("detail.acceptance_points_text", wxml)
        # 「工作内容」仍展示工序名，不能被要点顶替
        self.assertIn("{{detail.work_content || '-'}}", wxml)


if __name__ == "__main__":
    unittest.main()
