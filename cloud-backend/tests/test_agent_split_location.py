# -*- coding: utf-8 -*-
"""Excel 用例表 Sheet2「本轮新增验收清单」的水印位置项 → 分组建单链路自动化。

来源：D:/rokid/exports/监理通全量测试用例_2026-11-19.xlsx
  TC024 水印位置落库-基本流    带 location 水印多图建单 → WorkRecord.location
  TC025 水印位置-多图多地址    去重 join " / "、截断 256
  TC026 无水印位置不臆造       location IS NULL、不落项目名
  （单图文本路径与存量回填 TC027/TC028 已由 test_agent_watermark_two_turns /
    test_agent_no_watermark_values 及一次性任务覆盖）

直接打 agent_tools._create_split_records（分组建单执行层，732c6a6 改动点），
套路沿用 test_agent_split_confirm.py：FakeDb + ToolContext + ImageGroup。
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.models.work_record import WorkRecord
from app.services import agent_tools
from app.services import multi_image_split as mis


def _context():
    return agent_tools.ToolContext(
        db=agent_tools and _FakeDb(),
        user=SimpleNamespace(id=4, username="tester", real_name="测试监理",
                             project_id=7, has_permission=lambda _p: True),
        project_id=7,
        project=SimpleNamespace(name="沙盛四路等四条道路工程",
                                construction_unit="总包A", owner_unit="建设单位A",
                                supervision_unit="监理单位A", supervision_org="项目监理部"),
        message="确认生成", material="",
    )


class _FakeDb:
    """与 test_agent_split_confirm.py 同款：add/flush/commit 全内存。"""

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


def _wm(section="", location="", taken_at="2026-10-27 16:07:00", weather="多云 30℃"):
    d = {"section": section, "taken_at": taken_at, "weather": weather}
    if location:
        d["location"] = location
    return d


def _group(rtype, wms, n_images=None):
    n = n_images if n_images is not None else len(wms)
    return mis.ImageGroup(
        record_type=rtype,
        images=[f"https://img/loc/{rtype}/{i}" for i in range(n)],
        hints=["水泥搅拌桩施工"],
        wm=wms,
    )


def _created_records(ctx) -> list[WorkRecord]:
    return [r for r in ctx.db.rows if isinstance(r, WorkRecord)]


class SplitLocationTests(unittest.TestCase):

    def test_TC024_location_from_watermark_lands_in_location_column(self):
        """TC024 基本流：水印有位置 → WorkRecord.location=水印地址；
        content_json.watermark_location 仍留档；回执带「具体位置也已按水印自动填入」。
        """
        ctx = _context()
        groups = [_group("巡视", [_wm("建材纵一路", "四会市大沙镇马房工业开发区内")])]
        result = asyncio.run(agent_tools._create_split_records(ctx, groups))
        self.assertTrue(result.get("ok"), result)

        recs = _created_records(ctx)
        self.assertEqual(1, len(recs))
        rec = recs[0]
        self.assertEqual("四会市大沙镇马房工业开发区内", rec.location)
        # 留档仍在（可追溯）：content_json.watermark_location 照旧落
        cj = rec.content_json or {}
        self.assertEqual(["四会市大沙镇马房工业开发区内"],
                         cj.get("watermark_location"))
        self.assertIn("具体位置也已按水印自动填入（1/1条）", result.get("reply", ""))

    def test_TC025_multi_location_dedup_join_and_truncate(self):
        """TC025 多图多地址：去重后「 / 」连接；整列截断 256；不覆盖 section。"""
        ctx = _context()
        groups = [_group("旁站", [
            _wm("沙盛四路", "四会市大沙镇马房工业区"),
            _wm("沙盛四路", "四会市大沙镇马房工业区"),          # 重复地址去重
            _wm("大兴路", "四会市大沙镇府前路"),
        ])]
        asyncio.run(agent_tools._create_split_records(ctx, groups))
        rec = _created_records(ctx)[0]
        self.assertEqual("四会市大沙镇马房工业区 / 四会市大沙镇府前路",
                         rec.location)
        # section 独立口径不被 location 覆盖（两处不同部位）
        self.assertEqual("沙盛四路 / 大兴路", rec.section)

    def test_TC025_location_truncated_to_256(self):
        ctx = _context()
        long_loc = "四会市大沙镇" + "马房工业区" * 60   # 远超 256
        groups = [_group("巡视", [_wm("建材纵一路", long_loc)])]
        asyncio.run(agent_tools._create_split_records(ctx, groups))
        rec = _created_records(ctx)[0]
        self.assertEqual(256, len(rec.location or ""))
        self.assertTrue(rec.location.startswith("四会市大沙镇"))

    def test_TC026_no_watermark_location_stays_null(self):
        """TC026 反向：水印无位置 → location=None，绝不拿项目名/部位臆造顶替。"""
        ctx = _context()
        groups = [
            _group("巡视", [_wm("建材纵一路", "")]),   # 水印有部位无位置
            _group("旁站", [dict(taken_at="2026-10-27 09:32")]),  # 无位置无部位
        ]
        result = asyncio.run(agent_tools._create_split_records(ctx, groups))
        recs = _created_records(ctx)
        self.assertEqual(2, len(recs))
        for rec in recs:
            self.assertIsNone(rec.location, "无水印位置却填了值 → 臆造")
            self.assertNotIn("watermark_location", rec.content_json or {})
        # 回执不出现「具体位置也已自动填入」行（0 条有位置）
        self.assertNotIn("具体位置也已按水印自动填入", result.get("reply", ""))
        for rec in recs:
            # 项目名不许被当位置塞进去
            self.assertNotIn("沙盛四路等四条道路工程", str(rec.location or ""))

    def test_TC026_location_without_section_does_not_fill_section(self):
        """位置与部位相互独立：有位置无部位时 section 仍空（不互相顶替）。"""
        ctx = _context()
        groups = [_group("巡视", [dict(location="四会市大沙镇马房工业区",
                                       taken_at="2026-10-27 16:07:00")])]
        asyncio.run(agent_tools._create_split_records(ctx, groups))
        rec = _created_records(ctx)[0]
        self.assertEqual("四会市大沙镇马房工业区", rec.location)
        self.assertIsNone(rec.section)


if __name__ == "__main__":
    unittest.main()
