# -*- coding: utf-8 -*-
"""「待定 → 其他类型」对话快捷指令（2026-11-13）。

背景：后端 /api/work-record/{id}/set-type 早已存在，但小程序/Web 后台都没接入口，
A 层落待定后的回执却承诺「可在工作记录页改成巡视/旁站/验收/见证」→ 误导。
本测试锁住两件事：
  1) 纯函数解析刻意保守，不误伤新建/多类型/否定/危大等表述；
  2) 执行层与 set-type 同语义（枚举 4 值、创建者权限、type_history、不自动出文书）。

跑法：
  cd D:\\rokid\\rokid\\cloud-backend
  .venv\\Scripts\\python.exe -m unittest tests.test_agent_pending_type_change -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers.agent import _apply_pending_type_change
from app.services import agent_request_plan as arp
from tests.test_agent_tbd_draft import run


class ParseTests(unittest.TestCase):
    def test_hits(self):
        cases = {
            "改成巡视": "巡视",
            "把刚才那条待定改为旁站": "旁站",
            "那条设成验收": "验收",
            "刚才的标为见证": "见证",
            "改成巡视吧": "巡视",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(arp.parse_pending_type_change(text), expected)

    def test_no_hit(self):
        cases = [
            "", "记一条巡视", "新建一条旁站", "再记一条验收",
            "改成巡视或旁站", "别改", "不要改", "先别改",
            "改成平行检验", "改成起重机械安拆检查",  # set-type 只认 4 个枚举值
            "改成危大巡视",  # 危大要走 hazard_type 分支
            "按巡视记录来", "帮我记一下现场情况",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(arp.parse_pending_type_change(text))


class _Result:
    def __init__(self, record):
        self.record = record

    def scalar_one_or_none(self):
        return self.record


class _DB:
    def __init__(self, record=None):
        self.record = record
        self.committed = False

    async def execute(self, stmt):
        return _Result(self.record)

    async def commit(self):
        self.committed = True


def _record(creator_id=1, status="草稿", content_json=None):
    return SimpleNamespace(
        id=42, record_type="待定", status=status,
        creator_id=creator_id, project_id=1, content_json=content_json,
    )


def _user(uid=1, can_manage=False):
    return SimpleNamespace(
        id=uid, real_name="测试员", username="tester",
        has_permission=lambda _p: can_manage,
    )


class ApplyTests(unittest.TestCase):
    def apply(self, db, user, project_id=1, new_type="巡视"):
        return asyncio.run(_apply_pending_type_change(db, user, project_id, new_type))

    def test_changes_latest_pending_record(self):
        rec, db = _record(), None
        db = _DB(rec)
        out = self.apply(db, _user())
        self.assertIsNotNone(out)
        self.assertEqual(out["record_type"], "巡视")
        self.assertEqual(out["record_id"], 42)
        self.assertEqual(rec.record_type, "巡视")
        self.assertTrue(db.committed)
        self.assertIn("已把刚才那条待定记录改成", out["reply"])
        self.assertIn("巡视", out["reply"])
        self.assertEqual(len(rec.content_json["type_history"]), 1)
        step = rec.content_json["type_history"][0]
        self.assertEqual((step["from"], step["to"], step["source"]),
                         ("待定", "巡视", "agent_chat"))

    def test_appends_to_existing_history(self):
        rec = _record(content_json={"type_history": [{"from": "待定", "to": "巡视"}]})
        out = self.apply(_DB(rec), _user(), new_type="旁站")
        self.assertIsNotNone(out)
        self.assertEqual(len(rec.content_json["type_history"]), 2)

    def test_no_pending_record_returns_none(self):
        self.assertIsNone(self.apply(_DB(None), _user()))

    def test_no_project_returns_none(self):
        self.assertIsNone(self.apply(_DB(_record()), _user(), project_id=None))

    def test_invalid_type_returns_none(self):
        self.assertIsNone(self.apply(_DB(_record()), _user(), new_type="平行检验"))

    def test_other_creator_without_permission_returns_none(self):
        self.assertIsNone(self.apply(_DB(_record(creator_id=99)), _user()))

    def test_other_creator_with_task_manage_ok(self):
        out = self.apply(_DB(_record(creator_id=99)), _user(can_manage=True),
                         new_type="见证")
        self.assertIsNotNone(out)
        self.assertEqual(out["record_type"], "见证")


class ReceiptTests(unittest.TestCase):
    def test_tbd_receipt_points_to_chat_shortcut(self):
        r, _ = run({"record_type": "待定"}, "先记成待定吧")
        self.assertTrue(r.get("ok") is True)
        reply = str(r.get("reply") or "")
        self.assertIn("改成巡视", reply)
        self.assertNotIn("可在「工作记录」页把它改成", reply)


if __name__ == "__main__":
    unittest.main(verbosity=2)
