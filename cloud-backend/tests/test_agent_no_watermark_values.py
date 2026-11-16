"""回归：水印识别到的**具体值**不写进任何对话回执（2026-11-16 15:14 真机截图）。

用户口径「识别的内容只保留到思考里，不需要写出来，保留其他就行」。
14:45 只改了 _AGENT_SYSTEM，但逐项水印清单由服务端硬编码回执生成：
  - agent_tools._split_need_more() 拼 watermark_lines()（图片1：拍摄时间…）
  - routers.agent.py 把 auto_filled 整串拼成「已从照片水印自动填入：日期…：2026-10-27」
  - 小程序 index.ts 还会兜底拼一段 formatWatermark(wmFields)
这里锁死三条链路：识别值仍用于分组/回填，但对话文本里只出现栏目名。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from app.routers import agent as agent_router

ROOT = Path(__file__).resolve().parents[1]


class AutoFilledSummaryTest(unittest.TestCase):
    def test_only_field_labels_no_actual_values(self):
        filled = [
            "日期（改用照片水印）：2026-10-27",
            "地点：沙盛四路等4条道路工",
            "工程部位：建材纵一路",
            "天气：多云 30℃",
        ]
        text = agent_router._auto_filled_summary(filled)
        for value in ("2026-10-27", "沙盛四路", "建材纵一路", "30℃", "改用照片水印"):
            self.assertNotIn(value, text)
        for label in ("日期", "地点", "工程部位", "天气"):
            self.assertIn(label, text)
        # 旧版小程序靠「自动填入」判断是否跳过本地水印清单，不能丢
        self.assertIn("自动填入", text)

    def test_deduplicates_and_handles_numeric_colon(self):
        filled = ["开始时间（水印拍摄时间）：16:07:00", "日期：2026-10-27", "日期：2026-10-28"]
        text = agent_router._auto_filled_summary(filled)
        self.assertEqual(1, text.count("日期"))
        self.assertIn("开始时间", text)
        self.assertNotIn("16:07", text)
        self.assertNotIn("2026-10-27", text)

    def test_empty_filled_still_keeps_marker(self):
        text = agent_router._auto_filled_summary([])
        self.assertIn("自动填入", text)
        self.assertNotIn("：", text)


class HardCodedReceiptTest(unittest.TestCase):
    """静态兜底：防止有人把逐项复述拼回回执。"""

    def test_router_uses_summary_helper(self):
        src = (ROOT / "app/routers/agent.py").read_text(encoding="utf-8")
        self.assertIn("head = _auto_filled_summary(auto_filled)", src)
        self.assertNotIn('"、" .join(auto_filled)', src)
        self.assertNotIn('"、".join(auto_filled)', src)

    def test_split_reply_does_not_join_watermark_lines(self):
        src = (ROOT / "app/services/agent_tools.py").read_text(encoding="utf-8")
        start = src.index("def _split_need_more(")
        end = src.index("def _split_watermark_fields(", start)
        body = src[start:end]
        self.assertNotIn("watermark_lines(material", body)
        self.assertNotIn('"\\n".join(lines)', body)
        self.assertIn("+ summary", body)
        # 旧版小程序靠「自动填入」跳过本地水印清单，这句概括不能丢
        self.assertIn("自动填入记录栏位", body)

    def test_miniprogram_does_not_prepend_watermark_text(self):
        ts = (ROOT.parent / "wechat-miniprogram/miniprogram/pages/agent/index.ts").read_text(
            encoding="utf-8")
        self.assertNotIn("[wmText, reply]", ts)
        self.assertIn("识别的内容只保留到思考里", ts)

    def test_receipt_modules_have_no_identified_text(self):
        confirm = (ROOT / "app/services/split_record_confirm.py").read_text(encoding="utf-8")
        self.assertNotIn('head += f" 现场识别：{identified}"', confirm)
        splitter = (ROOT / "app/services/multi_image_split.py").read_text(encoding="utf-8")
        start = splitter.index("def summarize(")
        body = splitter[start:splitter.index("def _group_section(", start)]
        self.assertNotIn("_short_section(section)", body)

    def test_acceptance_points_hide_matched_procedure(self):
        """15:37 截图：验收要点后面括号里的规范工序名也是识别/匹配结果，不写出来。"""
        from app.services import multi_image_split as mis
        from app.services import split_record_confirm as src
        group = mis.ImageGroup(record_type="验收", images=["a"],
                               hints=["底板钢筋模板验收"], wm=[])
        draft = src.acceptance_points_for([group])
        self.assertTrue(draft and draft["procedure"])  # 内部仍保留
        for text in (src.build_type_confirmation([group]),
                     src.acceptance_points_text(draft),
                     src.build_acceptance_content_from_points(draft)):
            self.assertNotIn(draft["procedure"], text)
            self.assertNotIn("省统表相近工序", text)
        self.assertIn("核查钢筋规格", src.acceptance_points_text(draft))


class GroupSummaryAndConfirmationTest(unittest.TestCase):
    """2026-11-16 用户「都要」：分组汇总与分类回执也不写识别值。"""

    @staticmethod
    def _groups():
        from app.services import multi_image_split as mis
        return [
            mis.ImageGroup(record_type="巡视", images=["a"], hints=["水泥搅拌桩施工"],
                           wm=[{"section": "建材纵一路"}]),
            mis.ImageGroup(record_type="巡视", images=["b"], hints=["水泥搅拌桩施工"],
                           wm=[{"section": "大兴路"}]),
            mis.ImageGroup(record_type="验收", images=["c", "d"],
                           hints=["底板钢筋模板验收"], wm=[{"section": "天头路"}]),
            mis.ImageGroup(record_type="见证", images=["e"], hints=["钢筋原材取样"], wm=[]),
        ]

    def test_summarize_merges_counts_without_watermark_sections(self):
        from app.services import multi_image_split as mis
        text = mis.summarize(self._groups())
        self.assertIn("这批图我分了", text)
        self.assertIn("巡视2", text)
        self.assertIn("验收2", text)
        self.assertIn("见证1", text)
        for leaked in ("建材纵一路", "大兴路", "天头路", "水泥搅拌桩施工",
                       "底板钢筋模板验收", "钢筋原材取样"):
            self.assertNotIn(leaked, text)

    def test_type_confirmation_keeps_items_but_hides_identified(self):
        from app.services import agent_tools, split_record_confirm as src
        text = src.build_type_confirmation(
            self._groups(), display=agent_tools._split_record_display_name, stage="plan")
        # 「保留其他」：逐类内容/要点、待填栏目、确认引导仍在
        self.assertIn("巡视内容（GD-B-214）", text)
        self.assertIn("验收要点", text)
        self.assertIn("点「确认生成」", text)
        for leaked in ("现场识别", "水泥搅拌桩施工", "底板钢筋模板验收",
                       "钢筋原材取样", "建材纵一路"):
            self.assertNotIn(leaked, text)


if __name__ == "__main__":
    unittest.main()
