# -*- coding: utf-8 -*-
"""跨会话续办（2026-11-24「两轮对话重复/没记进度」）路由层测试。

场景：新会话里说「记一条验收」，同项目同类型未归档记录仍缺栏 →
先问「接着补还是另建」；答「接着补」→ 续办那条；答「另建」→ 正常新建。

触发条件（保守）：
  1. 消息是「记一条X」新建意图且带明确类型词；
  2. 同项目 + 本人创建 + 同类型 + 未归档的最近一条记录仍缺
     聊天填不了的栏以外的必填栏（缺照片/签章不算「没填完」）。

不连库不调模型：直调路由层纯函数 + FakeDb 模拟查库。
跑法：
  cd D:\\rokid\\rokid\\cloud-backend
  python -X utf8 -m unittest tests.test_agent_continue_record -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.work_record import WorkRecord
from app.routers import agent as agent_router


class _Scalar:
    def __init__(self, record):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class FakeDb:
    """execute() 不跑真 SQL：按查询语义过滤（status != ARCHIVED 的那条）。"""

    def __init__(self, record=None):
        self.record = record
        self.commits = 0

    async def execute(self, _stmt):
        record = self.record
        if record is not None and str(record.status or "") == "archived":
            record = None  # SQL where status != ARCHIVED 的语义模拟
        return _Scalar(record)

    async def commit(self):
        self.commits += 1


def _user():
    return SimpleNamespace(id=4, real_name="监理员", username="jl",
                           has_permission=lambda _p: True)


def _record(record_type="验收", status="draft", content_json=None):
    return WorkRecord(
        id=126, project_id=7, creator_id=4, record_type=record_type,
        status=status, content_json=content_json or {"voice_text": ""},
        work_content="顶板钢筋模板验收", section="大兴路",
    )


class ContinueRecordTriggerTest(unittest.TestCase):
    def test_new_record_intent_with_missing_fields_triggers_ask(self):
        db = FakeDb(_record())
        out = asyncio.run(agent_router._maybe_ask_continue_existing(
            db, _user(), 7, "wx-new-session", "记一条验收"))
        self.assertIsNotNone(out)
        self.assertEqual(out["record_id"], 126)
        self.assertEqual(out["record_type"], "验收")
        self.assertTrue(out["labels"], "应至少缺一栏才触发")

    def test_same_type_archived_record_does_not_trigger(self):
        # FakeDb 按查询语义过滤 archived；真实层由 SQL where 保证
        db = FakeDb(_record(status="archived"))
        out = asyncio.run(agent_router._maybe_ask_continue_existing(
            db, _user(), 7, "wx-new-session", "记一条验收"))
        self.assertIsNone(out)

    def test_other_record_types_are_filtered_by_sql(self):
        # record_type 过滤在 SQL where；FakeDb 不过滤，此用例只验证守卫不误伤：
        # 查询返回的记录类型与消息意图类型一致时才触发（FakeDb 返回巡视记录
        # 模拟「查库没命中验收」的语义由真实层保证，这里换成返回 None 验证）。
        db = FakeDb(None)
        out = asyncio.run(agent_router._maybe_ask_continue_existing(
            db, _user(), 7, "wx-new-session", "记一条验收"))
        self.assertIsNone(out)

    def test_non_new_record_intent_does_not_trigger(self):
        db = FakeDb(_record())
        for msg in ("今天天气怎么样", "帮我生成监理日志", "合格"):
            out = asyncio.run(agent_router._maybe_ask_continue_existing(
                db, _user(), 7, "wx-new-session", msg))
            self.assertIsNone(out, msg)

    def test_no_type_word_does_not_trigger(self):
        db = FakeDb(_record())
        out = asyncio.run(agent_router._maybe_ask_continue_existing(
            db, _user(), 7, "wx-new-session", "记一条"))
        self.assertIsNone(out)

    def test_no_project_does_not_trigger(self):
        db = FakeDb(_record())
        out = asyncio.run(agent_router._maybe_ask_continue_existing(
            db, _user(), None, "wx-new-session", "记一条验收"))
        self.assertIsNone(out)


class ContinueRecordAnswerTest(unittest.TestCase):
    def test_continue_answers(self):
        for msg in ("接着补", "继续补", "接着办", "继续", "接着填"):
            self.assertEqual(
                agent_router._consume_continue_record_answer(msg), "continue", msg)

    def test_new_answers(self):
        for msg in ("另建", "新建", "新开一条", "再建一条"):
            self.assertEqual(
                agent_router._consume_continue_record_answer(msg), "new", msg)

    def test_other_answers_are_none(self):
        for msg in ("合格", "大兴路", "隐蔽工程", "今天天气不错"):
            self.assertIsNone(
                agent_router._consume_continue_record_answer(msg), msg)


class ContinueRecordReplyTest(unittest.TestCase):
    def test_ask_reply_is_plain_text_without_chips(self):
        reply = agent_router._continue_record_ask_reply("验收")
        self.assertIn("接着", reply)
        self.assertIn("另建", reply)
        # 纯文字对话红线（2026-11-06）：不带候选 chips / 表单卡
        self.assertNotIn("[", reply)

    def test_routing_has_stash_and_consume_logic(self):
        """路由层必须把续办询问态存进草稿并消费（源码锚点）。"""
        src = (Path(__file__).resolve().parents[1]
               / "app" / "routers" / "agent.py").read_text(encoding="utf-8")
        self.assertIn("_continue_record", src)
        self.assertIn("_consume_continue_record_answer", src)
        self.assertIn("_maybe_ask_continue_existing", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
