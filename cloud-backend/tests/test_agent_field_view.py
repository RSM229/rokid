"""工作记录详情页「按用户字段清单渲染」回归测试（2026-11-16）。

真源：app/services/record_field_spec.py（由「监理通各记录关键字（草稿）
(1).xlsx /『字段清单』」生成）。本测试确保详情页栏目名、分组、必填标记
全部来自那份表，且「只搬家不丢弃」—— 原有记录字段不会因为换渲染方式丢掉。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from app.services import record_field_spec, work_record_field_view as view


def _sections(detail: dict, cj: dict | None = None) -> list[dict]:
    return view.build_field_sections(
        str(detail.get("record_type") or ""), detail, cj or {},
    )


def _titles(sections: list[dict]) -> list[str]:
    return [s["title"] for s in sections]


def _field(sections: list[dict], label: str) -> dict | None:
    for section in sections:
        for field in section["fields"]:
            if field["label"] == label:
                return field
    return None


class FieldViewSourceTest(unittest.TestCase):
    """栏目名与分组必须来自 FORMS，不在前端/测试里另造第二份字段表。"""

    def test_acceptance_sections_match_user_xlsx(self):
        sections = _sections({"id": 126, "record_type": "验收"})
        self.assertEqual(_titles(sections)[:3], ["基本信息", "内容", "签章"])
        labels = [f["label"] for s in sections for f in s["fields"]]
        for expected in (
            "工程项目名称", "编号", "验收日期", "验收类型",
            "验收部位、工序、材料、设备进场", "验收内容（验收要点）",
            "施工单位自检结论", "监理验收结论", "举牌验收照片",
            "验收单位及人员", "监理验收人（签名）", "施工单位质检员（签名）",
        ):
            self.assertIn(expected, labels, expected)
        # 用户表里没有「验收结果」「工作内容」这两个自造栏目
        self.assertNotIn("验收结果", labels)
        self.assertNotIn("工作内容", labels)

    def test_every_label_comes_from_FORMS(self):
        form = record_field_spec.FORMS["acceptance_record"]
        source_labels = {
            " ".join(str(f.get("raw_label") or f.get("label") or "").split())
            for f in form["fields"]
        }
        sections = _sections({"id": 1, "record_type": "验收"})
        for section in sections:
            if section["title"] == "其他记录信息":
                continue
            for field in section["fields"]:
                # 长标签短化时全文保留在 note；其余逐字一致
                candidate = field["label"] if not field["note"] else field["note"]
                self.assertIn(candidate, source_labels, field["label"])

    def test_section_order_follows_xlsx(self):
        sections = _sections({"id": 1, "record_type": "旁站"})
        self.assertEqual(
            _titles(sections)[:5],
            ["基本信息", "施工情况", "监理情况", "问题与处理", "签章"],
        )


class FieldViewValueTest(unittest.TestCase):
    """值从详情 dict / content_json / prov_fields 三层取，不臆造。"""

    def test_acceptance_work_content_is_moved_into_part_field(self):
        # 用户截图：工作内容 = 水印识别的工序名，用户表无「工作内容」栏，
        # 必须搬到「验收部位、工序、材料、设备进场」，且不重复出现。
        sections = _sections({
            "id": 126, "record_type": "验收", "section": "大兴路",
            "work_content": "底板钢筋模板验收；9m钢板桩收方；顶板钢筋模板验收",
        })
        part = _field(sections, "验收部位、工序、材料、设备进场")
        self.assertIsNotNone(part)
        self.assertIn("大兴路", part["value"])
        self.assertIn("底板钢筋模板验收", part["value"])
        self.assertNotIn("施工内容（水印识别）", [
            f["label"] for s in sections for f in s["fields"]
        ])

    def test_auto_grouping_draft_is_not_written_into_part_field(self):
        """2026-11-16 截图：系统自动归类占位草稿不该写进正式字段值。"""
        sections = _sections({
            "id": 131, "record_type": "验收",
            "section": "ssy37-38雨水管道",
            "work_content": "验收（由1张现场照片自动归类，待补全内容）",
        })
        part = _field(sections, "验收部位、工序、材料、设备进场")
        self.assertIsNotNone(part)
        # 水印回填的真实部位保留，占位草稿不展示（用户说「保留其他」）
        self.assertEqual("ssy37-38雨水管道", part["value"])
        self.assertNotIn("自动归类", part["value"])
        self.assertNotIn("施工内容（水印识别）", [
            f["label"] for s in sections for f in s["fields"]
        ])
        # 正式记录里任何字段都不得出现系统占位措辞
        for s in sections:
            for f in s["fields"]:
                self.assertNotIn("自动归类", f["value"])

    def test_real_process_name_is_still_kept(self):
        sections = _sections({
            "id": 131, "record_type": "验收", "section": "大兴路",
            "work_content": "底板钢筋模板验收",
        })
        part = _field(sections, "验收部位、工序、材料、设备进场")
        self.assertIn("底板钢筋模板验收", part["value"])

    def test_strip_auto_draft_handles_both_placeholders(self):
        self.assertEqual("", view._strip_auto_draft(
            "验收（由1张现场照片自动归类，待补全内容）"))
        self.assertEqual("", view._strip_auto_draft(
            "待确认归类（由现场照片自动分组，待补充类型与内容）"))
        self.assertEqual("底板钢筋模板验收", view._strip_auto_draft("底板钢筋模板验收"))
        self.assertEqual("", view._strip_auto_draft(""))

    def test_acceptance_points_draft_is_visible_and_marked_pending(self):
        sections = _sections({
            "id": 126, "record_type": "验收",
            "acceptance_content": "",
            "acceptance_points_text": "省统表相近工序「钢筋绑扎」：① 核查钢筋规格",
        })
        content = _field(sections, "验收内容（验收要点）")
        self.assertIn("核查钢筋规格", content["value"])
        self.assertIn("待确认", content["note"])

    def test_formal_acceptance_content_wins_over_draft(self):
        sections = _sections({
            "id": 126, "record_type": "验收",
            "acceptance_content": "① 核查钢筋规格 —— 符合",
            "acceptance_points_text": "旧草案",
        })
        content = _field(sections, "验收内容（验收要点）")
        self.assertEqual(content["value"], "① 核查钢筋规格 —— 符合")
        self.assertEqual(content["note"], "")

    def test_required_missing_flag_only_for_manual_fields(self):
        sections = _sections({"id": 126, "record_type": "验收"})
        self.assertTrue(_field(sections, "验收类型")["missing"])
        self.assertTrue(_field(sections, "验收内容（验收要点）")["missing"])
        # 系统生成的空字段不标「未填写」，签章走签章服务
        self.assertFalse(_field(sections, "验收单位及人员")["missing"])
        self.assertEqual(_field(sections, "验收单位及人员")["display_value"], "-")
        self.assertEqual(_field(sections, "监理验收人（签名）")["value"], "待签章")

    def test_placeholder_words_count_as_missing(self):
        sections = _sections({
            "id": 1, "record_type": "验收", "acceptance_type": "待补",
        })
        self.assertTrue(_field(sections, "验收类型")["missing"])
        # 「无」是有效结论，不能被当成缺项
        sections2 = _sections({
            "id": 1, "record_type": "验收", "supervision_conclusion": "无",
        })
        self.assertFalse(_field(sections2, "监理验收结论")["missing"])

    def test_station_check_content_prefers_station_content_not_watermark(self):
        sections = _sections({
            "id": 128, "record_type": "旁站",
            "station_content": "混凝土浇筑全过程旁站",
            "work_content": "基础混凝土浇筑",
        })
        content = _field(sections, "检查内容及检查结果情况")
        self.assertEqual(content["value"], "混凝土浇筑全过程旁站")

    def test_patrol_content_shows_six_items_from_xlsx(self):
        """2026-11-17 用户截图：详情里的六项也要逐行，不能挤成一段。"""
        sections = _sections({"id": 127, "record_type": "巡视"})
        content = _field(sections, "巡视内容")
        value = content["value"]
        lines = value.splitlines()
        self.assertEqual(6, len(lines))
        self.assertTrue(lines[0].startswith("① 施工单位是否按工程设计文件"))
        self.assertTrue(lines[1].startswith("② 使用的工程材料"))
        self.assertTrue(lines[2].startswith("③ 施工现场管理人员"))
        self.assertTrue(lines[3].startswith("④ 特种作业人员"))
        self.assertTrue(lines[4].startswith("⑤ 安全文明施工情况"))
        self.assertEqual("⑥ 其他", lines[5])
        self.assertNotIn("1.施工单位", value)
        self.assertIn("（专项）施工方案施工", value)  # 原文一个字不丢
        self.assertEqual(content["note"], "")
        self.assertEqual(content["display_value"], value)

    def test_user_written_numbered_text_is_not_reformatted(self):
        self.assertEqual("1.现场已处理", view._format_inspection_items("1.现场已处理"))
        self.assertEqual("（甲 乙）", view._format_inspection_items("（甲 乙）"))

    def test_photo_field_shows_count_not_url_list(self):
        sections = _sections({
            "id": 126, "record_type": "验收",
            "inspection_photos": ["a.jpg", "b.jpg", "c.jpg"],
        })
        self.assertEqual(
            _field(sections, "举牌验收照片")["value"],
            "已附 3 张（见影像资料）",
        )

    def test_content_json_and_prov_fields_are_searched(self):
        sections = _sections(
            {"id": 126, "record_type": "验收"},
            {"self_check_conclusion": "自检合格", "prov_fields": {"acceptance_type": "隐蔽工程"}},
        )
        self.assertEqual(_field(sections, "施工单位自检结论")["value"], "自检合格")
        self.assertEqual(_field(sections, "验收类型")["value"], "隐蔽工程")

    def test_legacy_fields_are_kept_in_extra_section(self):
        sections = _sections({
            "id": 128, "record_type": "旁站",
            "station_conclusion": "符合要求",
            "inspection_remark": "已复核",
        })
        extra = next((s for s in sections if s["title"] == "其他记录信息"), None)
        self.assertIsNotNone(extra)
        labels = [f["label"] for f in extra["fields"]]
        self.assertIn("旁站结论", labels)

    def test_unmapped_type_uses_fact_fields_and_does_not_ask_for_type(self):
        sections = _sections({
            "id": 9, "record_type": "待定", "work_content": "现场巡查",
            "section": "3号楼", "construction_unit": "某某建设",
        })
        labels = [f["label"] for s in sections for f in s["fields"]]
        # 无表式兜底分组仍保留「施工内容（水印识别）」（与有表式的「其他记录信息」不同）
        self.assertIn("施工内容（水印识别）", labels)
        self.assertNotIn("记录类型", labels)
        self.assertTrue(all(not f["missing"] for s in sections for f in s["fields"]))

    def test_watermark_work_content_not_shown_in_extra_section(self):
        """2026-11-17 用户截图：巡视详情「其他记录信息」里冒出
        「施工内容（水 沙盛四路等4条道路工 广东建设监理 印识别）」——
        这是水印 OCR 值（混进项目名/单位名），不是用户字段清单要的栏目，
        有表式记录的「其他记录信息」不应再展示它；数据仍留在库里。"""
        sections = _sections({
            "id": 164, "record_type": "巡视",
            "work_content": "水 沙盛四路等4条道路工 广东建设监理 印识别",
        })
        labels = [f["label"] for s in sections for f in s["fields"]]
        self.assertNotIn("施工内容（水印识别）", labels)
        extra = next((s for s in sections if s["title"] == "其他记录信息"), None)
        if extra:
            self.assertNotIn("施工内容（水印识别）", [f["label"] for f in extra["fields"]])
            for field in extra["fields"]:
                self.assertNotIn("沙盛四路等4条道路工", str(field["value"]))


class FieldViewWiringTest(unittest.TestCase):
    def test_detail_endpoint_returns_field_sections(self):
        source = Path(__file__).resolve().parents[1] / "app/routers/work_record.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn("work_record_field_view.build_field_sections", text)
        self.assertIn('detail["field_sections"]', text)

    def test_wxml_renders_sections_and_keeps_legacy_fallback(self):
        wxml = (Path(__file__).resolve().parents[1]
                / "../wechat-miniprogram/miniprogram/pages/work/index.wxml").resolve()
        text = wxml.read_text(encoding="utf-8")
        self.assertIn("showFieldSections", text)
        self.assertIn("detail.field_sections", text)
        for rtype in ("巡视", "验收", "旁站", "见证"):
            self.assertIn(
                f"detail.record_type === '{rtype}' && !showFieldSections", text,
            )

    def test_detail_value_css_keeps_newlines(self):
        wxss = (Path(__file__).resolve().parents[1]
                / "../wechat-miniprogram/miniprogram/pages/work/index.wxss").resolve()
        line = next(l for l in wxss.read_text(encoding="utf-8").splitlines()
                    if l.startswith(".detail-value"))
        self.assertIn("white-space: pre-wrap", line)


if __name__ == "__main__":
    unittest.main()
