"""回归：显式「写入正式记录」授权必须真正落库，不再无限追问。

2026-11-16 真机录屏（沙盛四路等四条道路工程）：验收 #126 已由带图链路建好，
用户「把验收要点补充到我前面创建的验收记录里」→「写入正式记录」→
「请写入验收记录」，Agent 每次都回「还没写入正式记录，还差…」，始终没写库。
根因：确认词白名单不认「写入」措辞、草稿有必填缺项时 awaiting=False、
必填合同不接受「留空」、且 Agent 工具表没有「更新已建记录」的能力。
"""
from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.models.work_record import WorkRecord
from app.routers import agent as agent_router
from app.services import agent_tools

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


def _project():
    return SimpleNamespace(
        name="沙盛四路等四条道路工程", construction_unit="总包A",
        owner_unit="建设单位A", supervision_unit="监理单位A",
        supervision_org="项目监理部",
    )


def _context(message: str = "写入正式记录"):
    return agent_tools.ToolContext(
        db=FakeDb(),
        user=SimpleNamespace(id=4, username="tester", project_id=7,
                             has_permission=lambda _p: True),
        project_id=7, project=_project(), message=message, material="",
    )


def _draft(**overrides) -> dict:
    base = {
        "record_type": "验收",
        "record_date": "2026-10-27",
        "section": "大兴路",
        "location": "沙盛四路等4条道路工程",
        "weather": "多云 31℃",
        "work_content": "顶板钢筋模板验收",
        "problems": "符合要求",
        "acceptance_result": "合格",
        "extra_fields": {
            "acceptance_type": "隐蔽工程",
            "acceptance_content": "水泥搅拌桩/钢板桩支护/混凝土进场及浇筑三项验收要点已确认",
            "supervision_conclusion": "验收合格，同意进入下道工序。",
            "self_check_conclusion": "符合要求",
            "photo": "现场照片已上传",
        },
    }
    base.update(overrides)
    return base


class WriteThroughPhraseTest(unittest.TestCase):
    def test_video_phrases_are_write_through(self):
        for phrase in (
            "把验收要点补充到我前面创建的验收记录里",
            "写入正式记录",
            "请写入验收记录",
            "其余栏位先留空，不做臆测填写",
            "直接写入",
        ):
            self.assertTrue(agent_tools.wants_write_through(phrase), phrase)

    def test_questions_and_field_values_are_not_write_through(self):
        for phrase in (
            "验收的要点呢", "合格", "验收要点是什么？", "这里还差什么吗",
            "这次验收做的是哪个工序", "拍了三张照片",
        ):
            self.assertFalse(agent_tools.wants_write_through(phrase), phrase)

    def test_short_conclusion_absorbed(self):
        self.assertEqual(agent_tools.short_conclusion("合格"), "合格")
        self.assertEqual(agent_tools.short_conclusion("待整改"), "待整改")
        for phrase in ("符合要求", "合格，可以进入下道工序", "验收合格"):
            self.assertEqual(agent_tools.short_conclusion(phrase), "")


class WriteThroughRoutingTest(unittest.TestCase):
    def _agent_source(self) -> str:
        return (ROOT / "app" / "routers" / "agent.py").read_text(encoding="utf-8")

    def _confirm_tokens(self) -> set[str]:
        tree = ast.parse(self._agent_source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_confirm_tokens"
                for t in node.targets
            ):
                return set(ast.literal_eval(node.value))
        raise AssertionError("_confirm_tokens 未在路由层定义")

    def test_confirm_tokens_include_write_phrases(self):
        tokens = self._confirm_tokens()
        for phrase in ("写入正式记录", "写入验收记录", "请写入", "直接写入"):
            self.assertIn(phrase, tokens)

    def test_confirmation_no_longer_gated_by_awaiting(self):
        src = self._agent_source()
        self.assertIn("(awaiting or write_through)", src)
        self.assertIn('args["_write_through"] = bool(write_through)', src)

    def test_existing_record_shortcut_exists(self):
        src = self._agent_source()
        self.assertIn("async def _apply_existing_record_write_through", src)
        self.assertIn('"tool": "update_work_record"', src)
        self.assertIn("wants_write_through(message)", src)

    def test_refers_to_existing_record(self):
        self.assertTrue(agent_router._refers_to_existing_record(
            "把验收要点补充到我前面创建的验收记录里"))
        self.assertTrue(agent_router._refers_to_existing_record(
            "写入正式记录", ["验收记录 #126 目前只填了日期"]))
        self.assertFalse(agent_router._refers_to_existing_record(
            "写入正式记录", ["今天天气不错"]))
        self.assertEqual(agent_router._record_id_from_text(
            "上一条 #121", "验收记录 #126 还差验收内容"), 126)

    def test_fake_success_markers_cover_submit_claim(self):
        src = self._agent_source()
        self.assertIn("按现在已确认的内容提交", src)


class WriteThroughExecutionTest(unittest.TestCase):
    def test_write_through_creates_record_even_with_missing_required_fields(self):
        """核心回归：缺必填栏但用户已授权留空 → 必须落库，不能停在 need_more。"""
        ctx = _context("写入正式记录")
        args = _draft(extra_fields={}, _write_through=True)
        result = asyncio.run(agent_tools._create_work_record(ctx, args))
        self.assertTrue(result.get("ok"), result)
        self.assertFalse(result.get("need_more"))
        self.assertTrue(any(isinstance(r, WorkRecord) for r in ctx.db.rows))

    def test_without_authorization_missing_fields_still_block(self):
        """不能破坏原流程：没有写入授权时必填缺项照旧追问。"""
        ctx = _context("继续补充")
        args = _draft(extra_fields={})
        result = asyncio.run(agent_tools._create_work_record(ctx, args))
        self.assertTrue(result.get("need_more"), result)
        self.assertFalse(any(isinstance(r, WorkRecord) for r in ctx.db.rows))

    def test_confirmed_draft_still_creates_record(self):
        ctx = _context("请写入验收记录")
        args = _draft(_write_through=True)
        result = asyncio.run(agent_tools._create_work_record(ctx, args))
        self.assertTrue(result.get("ok"), result)
        self.assertIn("已记录", str(result.get("reply") or ""))
        self.assertTrue(any(isinstance(r, WorkRecord) for r in ctx.db.rows))


class _EmptyResult:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return SimpleNamespace(all=lambda: [])


class RouterFakeDb:
    def __init__(self, record=None):
        self.record = record
        self.commits = 0

    async def get(self, _model, record_id):
        return self.record if self.record is not None and self.record.id == record_id else None

    async def execute(self, _stmt):
        return _EmptyResult()

    async def delete(self, _row):
        return None

    async def commit(self):
        self.commits += 1


class ExistingRecordShortcutTest(unittest.TestCase):
    def _user(self):
        return SimpleNamespace(id=4, real_name="监理员", username="jl",
                               has_permission=lambda _p: True)

    def _record(self):
        return WorkRecord(
            id=126, project_id=7, creator_id=4, record_type="验收",
            status="draft", content_json={"voice_text": "原始水印原话"},
            acceptance_result=None, work_content="顶板钢筋模板验收",
        )

    def test_write_through_updates_existing_record(self):
        """视频主路径：更新 #126，而不是又建一条待确认草稿。"""
        record = self._record()
        db = RouterFakeDb(record)
        result = asyncio.run(agent_router._apply_existing_record_write_through(
            db, self._user(), 7, "conv-1", "写入正式记录 #126",
            {"record_type": "验收", "acceptance_result": "合格",
             "extra_fields": {"acceptance_content": "水泥搅拌桩/钢板桩/混凝土三项要点"}},
        ))
        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["record_id"], 126)
        self.assertEqual(record.acceptance_result, "合格")
        self.assertEqual(record.content_json["acceptance_content"],
                         "水泥搅拌桩/钢板桩/混凝土三项要点")
        self.assertIn("写入正式记录 #126", record.content_json["voice_text"])
        self.assertEqual(record.content_json["voice_text"].splitlines()[0], "原始水印原话")
        self.assertGreaterEqual(db.commits, 1)

    def test_missing_target_falls_back_to_original_flow(self):
        """没定位到已建记录时返回 None，不阻断原建档流程。"""
        result = asyncio.run(agent_router._apply_existing_record_write_through(
            RouterFakeDb(None), self._user(), 7, "conv-1",
            "写入正式记录 #999", {"record_type": "验收"},
        ))
        self.assertIsNone(result)

    def test_cross_project_record_is_rejected(self):
        record = self._record()
        record.project_id = 99
        result = asyncio.run(agent_router._apply_existing_record_write_through(
            RouterFakeDb(record), self._user(), 7, "conv-1",
            "写入正式记录 #126", {"record_type": "验收"},
        ))
        self.assertIsNone(result)

    def _record_with_points(self):
        record = self._record()
        record.content_json = {
            "voice_text": "原始水印原话",
            "acceptance_points": {
                "procedure": "构造柱、圈梁、过梁的钢筋绑扎、模板安装及混凝土浇筑",
                "identified": "底板钢筋模板验收",
                "points": ["① 核查钢筋规格、数量", "② 模板安装牢固", "③ 混凝土强度符合设计"],
                "text": "省统表相近工序：① ② ③",
                "confirmed": False,
            },
        }
        return record

    def test_confirmed_write_through_turns_points_into_formal_content(self):
        """要点草案经用户写入授权后，正式验收内容必须带逐条结论。"""
        record = self._record_with_points()
        result = asyncio.run(agent_router._apply_existing_record_write_through(
            RouterFakeDb(record), self._user(), 7, "conv-1",
            "写入正式记录 #126", {"record_type": "验收"},
        ))
        self.assertTrue(result and result["ok"])
        text = str(record.content_json.get("acceptance_content") or "")
        self.assertIn("逐项核对，共 3 项", text)
        self.assertEqual(3, text.count("—— 符合"))
        self.assertIn("acceptance_content", result.get("fields") or [])
        # 草案与工序名都保留（只搬家不丢弃）
        self.assertTrue(record.content_json.get("acceptance_points"))
        self.assertIn("顶板钢筋模板验收", str(record.work_content))

    def test_negative_reply_never_writes_points_as_conforming(self):
        """用户说「不符合」时，绝不能把要点静默写成「符合」。"""
        record = self._record_with_points()
        asyncio.run(agent_router._apply_existing_record_write_through(
            RouterFakeDb(record), self._user(), 7, "conv-1",
            "第2项不符合，写入正式记录 #126", {"record_type": "验收"},
        ))
        self.assertFalse(str(record.content_json.get("acceptance_content") or ""))
        self.assertTrue(record.content_json.get("acceptance_points"))


if __name__ == "__main__":
    unittest.main()
