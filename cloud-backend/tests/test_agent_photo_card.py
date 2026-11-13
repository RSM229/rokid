# -*- coding: utf-8 -*-
"""「记录已保存 · 补充现场照片」卡片是否该下发（2026-11-13）。

背景：用户「照片 + 你先记着」落「待定」记录后，聊天里仍弹一张
「补充现场照片」表单卡（0 项信息 / 0/9 / 暂时跳过），但本轮照片在
_create_work_record 里已经挂到 inspection_photos，这张卡纯属空转，
还会让人误以为照片没保存、需要重传。

规则：
  - 本轮带图（photo_count > 0）→ 不下发补充照片卡，回执明说「已挂到这条记录」；
  - 纯文字建记录（photo_count = 0）→ 仍保留补拍入口；
  - 旧调用方 result 里没有 photo_count → 按 0 处理，保持向后兼容。

跑法：
  cd D:\\rokid\\rokid\\cloud-backend
  .venv\\Scripts\\python.exe -m unittest tests.test_agent_photo_card -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers.agent import _agent_form
from tests.test_agent_tbd_draft import run


def _result(photo_count=None, record_id=12):
    data = {"record_id": record_id}
    if photo_count is not None:
        data["photo_count"] = photo_count
    return {"ok": True, "reply": "已记录：待定｜地下室", "need_more": [], "data": data}


class PhotoCardExecutionTests(unittest.TestCase):
    """执行层：photo_count 与回执文案必须和实际挂图一致。"""

    def test_with_images_reports_attached_and_no_form_hint(self):
        r, fdb = run({"section": "地下室"}, "拍了几张照片，你先记着",
                     images=["/uploads/a.jpg", "/uploads/b.jpg"])
        self.assertTrue(r.get("ok") is True)
        self.assertEqual(r["data"]["photo_count"], 2)
        self.assertEqual(len(fdb.added[0].inspection_photos or []), 2)
        self.assertIn("已挂到这条记录", r.get("reply") or "")
        self.assertNotIn("下方表单里添加", r.get("reply") or "")

    def test_without_images_keeps_upload_hint(self):
        r, _ = run({"record_type": "待定"}, "先记成待定吧")
        self.assertTrue(r.get("ok") is True)
        self.assertEqual(r["data"]["photo_count"], 0)
        self.assertIn("下方表单里添加", r.get("reply") or "")


class PhotoCardFormTests(unittest.TestCase):
    """路由层：带图不下发卡片，无图仍下发。"""

    def test_with_attached_photos_no_form(self):
        form = _agent_form("create_work_record", {"record_type": "待定"},
                           _result(photo_count=2), "你先记着", "某某医院项目")
        self.assertIsNone(form, "本轮已带图时不应再下发补充照片卡")

    def test_without_photos_keeps_form(self):
        form = _agent_form("create_work_record", {"record_type": "待定"},
                           _result(photo_count=0), "先记成待定吧", "某某医院项目")
        self.assertIsNotNone(form)
        self.assertTrue(form["attachments"]["enabled"])
        self.assertEqual(form["record_id"], 12)
        self.assertTrue(form["skippable"])

    def test_legacy_result_without_photo_count_keeps_form(self):
        """旧调用方/其它工具没有 photo_count 时按 0 处理，不要静默吞掉补拍入口。"""
        form = _agent_form("create_work_record", {"record_type": "待定"},
                           _result(photo_count=None), "先记成待定吧", "某某医院项目")
        self.assertIsNotNone(form)

    def test_need_more_still_no_form(self):
        r = _result(photo_count=0)
        r["need_more"] = ["记录类型"]
        self.assertIsNone(
            _agent_form("create_work_record", {}, r, "记一条", "某某医院项目")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
