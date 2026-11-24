# -*- coding: utf-8 -*-
"""A 层「零必填·类型不明」执行层测试（2026-11-13 建，2026-11-24 改版对齐）。

改的是 agent_tools._create_work_record 的类型识别分支：
  类型识别不出 + 有实体（内容/部位/位置/照片）→ 先问一句「记成哪一类」
  （用户答了再建，照片/内容保留在回执与草稿里不丢）；
  「你先记着」直达（_auto_tbd）与用户明确说「待定/不确定」→ 仍落「待定」；
  完全无实体 → 仍追问「记录内容或区域」「记录类型」；
  类型明确的记录 → 省统表必填仍照常追问（不因本次改动放松）。

⚠️ 为什么单测而不是跑分器？见 agent_eval/README.md「gap 用例」一节：
跑分器只测「模型选工具 + 抽参数」路由层，测不到服务端执行层，
本测试直接调 _create_work_record（执行层），不连库不调模型。

跑法：
  cd D:\rokid\rokid\cloud-backend
  .venv\Scripts\python.exe -m unittest tests.test_agent_tbd_draft -v
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import agent_tools as at


class FakeDB:
    """只满足落库最小接口：add/flush/commit（仿 test_acceptance_checklists.py）。"""

    def __init__(self):
        self.added = []
        self._next = 1

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next
                self._next += 1

    async def commit(self):
        pass


def run(args: dict, message: str, **kw) -> tuple[dict, FakeDB]:
    """返回 (工具结果, FakeDB)；FakeDB.added 里是落库的模型对象。"""
    fdb = FakeDB()
    ctx = at.ToolContext(
        db=fdb,
        user=SimpleNamespace(id=1, username="测试员"),
        project_id=1,
        project=SimpleNamespace(name="某某医院项目"),
        message=message,
        images=kw.get("images", []),
        image_signals=kw.get("image_signals", []),
    )
    return asyncio.run(at._create_work_record(ctx, dict(args))), fdb


class AgentTbdDraftTests(unittest.TestCase):
    """A 层分支矩阵：4 个主路径 + 2 个边界。"""

    def test_type_known_with_content_passes_entrance(self):
        """① 类型明确+有内容 → 不再准入拦截；省统表必填（时间/问题处理）仍追问。"""
        r, _ = run(
            {"record_type": "巡视", "section": "3号楼二层", "work_content": "钢筋绑扎"},
            "记一条巡视：3号楼二层钢筋绑扎",
        )
        # 关键：不再被「记录内容或施工区域」准入拦截（否则 need_more 只有内容/区域）
        missing = [str(x) for x in (r.get("need_more") or [])]
        self.assertTrue(missing, "类型明确+有内容时不应静默成功（表式必填仍在）")
        self.assertTrue(
            any("时间" in m or "处理" in m for m in missing),
            f"应追问表式必填而非准入字段，实际={missing}",
        )
        self.assertNotIn("记录内容或施工区域", missing)

    def test_type_unknown_with_content_asks_type(self):
        """② 【A 层核心·2026-11-24 改版】类型识别不出 + 有内容 → 先问类型。

        用户反馈「照片/内容都给了却建成待定」：有实体但类型不明时先回执
        问一句类型（用户答了再建），不再静默落「待定」。
        「你先记着」（_auto_tbd 直达）与明确说「待定/不确定」仍落待定。
        """
        r, fdb = run(
            {"section": "3号楼二层", "work_content": "在浇混凝土"},
            "记录一下，3号楼二层在浇混凝土",
        )
        self.assertEqual(r.get("need_more"), ["记录类型"],
                         f"应追问记录类型，实际={r.get('need_more')}")
        self.assertFalse(r.get("ok"), "类型未明不应直接建库")
        self.assertIn("记着", r.get("reply") or "", "回执应说明信息已保留")
        self.assertFalse(fdb.added, "类型未明时不应有记录落库")

    def test_type_unknown_with_images_asks_type(self):
        """②b 类型识别不出 + 带照片 → 先问类型（照片已保留，答后挂上）。"""
        r, fdb = run({"section": "地下室"}, "拍了几张现场照片", images=["/uploads/a.jpg"])
        self.assertEqual(r.get("need_more"), ["记录类型"],
                         f"应追问记录类型，实际={r.get('need_more')}")
        self.assertFalse(r.get("ok"), "类型未明不应直接建库")
        self.assertIn("记着", r.get("reply") or "", "回执应说明照片已保留")
        self.assertFalse(fdb.added, "类型未明时不应有记录落库")

    def test_auto_tbd_direct_still_falls_to_tbd(self):
        """②c 路由层直达（_auto_tbd：「你先记着」）→ 仍落待定，不问。"""
        r, fdb = run(
            {"section": "3号楼二层", "record_type": "待定", "_auto_tbd": True},
            "拍了几张照片，你先记着",
        )
        self.assertEqual(r.get("need_more"), [], f"不应追问，实际={r.get('need_more')}")
        self.assertTrue(r.get("ok") is True, f"应创建成功，实际 ok={r.get('ok')}")
        self.assertTrue(fdb.added, "应有一条记录落库")
        self.assertEqual(fdb.added[0].record_type, "待定",
                         f"落库类型应为待定，实际={getattr(fdb.added[0], 'record_type', None)}")

    def test_empty_request_still_asks(self):
        """③ 完全无实体 → 仍追问（不建空记录，不静默待定）。"""
        r, _ = run({}, "帮我记一条")
        self.assertTrue(r.get("need_more"), "无实体应追问")
        self.assertFalse(r.get("ok"), f"无实体不应创建成功，ok={r.get('ok')}")

    def test_type_known_no_content_still_blocked(self):
        """④ 类型明确 + 无内容（巡视）→ 准入拦截保留，不崩。"""
        r, _ = run({"record_type": "巡视"}, "记一条巡视")
        self.assertTrue(r.get("need_more"), "无内容应被准入追问")
        reply = r.get("reply") or ""
        self.assertTrue(
            any(k in reply for k in ("内容", "位置", "区域")),
            f"追问应提及内容/位置/区域，实际回复={reply}",
        )

    def test_explicit_tbd_keyword_ok_even_without_entity(self):
        """⑤ 无实体，但用户明确说「待定/不确定」→ 落待定（历史行为保留）。"""
        r, _ = run({"record_type": "待定"}, "先记成待定吧")
        self.assertEqual(r.get("need_more"), [], f"明确待定不应追问，实际={r.get('need_more')}")
        self.assertTrue(r.get("ok") is True, f"明确待定应创建，ok={r.get('ok')}")


class AgentTbdMultiImageShortCircuitTests(unittest.TestCase):
    """A 层：只有「你先记着」类请求（_no_multi_split）才跳过多图分组追问。

    普通「有照片但没说类型」的路由同样会打 _auto_tbd，但那只是不追问记录类型，
    **不能**把分组也关掉——否则跨类型的多张照片会全塞进一条待定记录。
    """

    def _ctx(self, images, signals=None, message=""):
        return SimpleNamespace(
            images=list(images),
            image_signals=list(signals or []),
            message=message,
        )

    def _groups(self):
        return [
            {"record_type": "巡视", "image_indices": [1]},
            {"record_type": "旁站", "image_indices": [2]},
        ]

    def test_zero_required_stash_short_circuits_split(self):
        """① 「你先记着」（_no_multi_split）→ 直接 return None 落单条待定，不多问一轮。"""
        ctx = self._ctx(["/u/a.jpg", "/u/b.jpg"])
        control = asyncio.run(at._maybe_handle_multi_image_split(
            ctx, {"image_groups": self._groups()}, {}))
        self.assertIsNotNone(control, "无 _no_multi_split 时应走分组确认（对照组）")
        short = asyncio.run(at._maybe_handle_multi_image_split(
            ctx, {"_no_multi_split": True, "image_groups": self._groups()}, {}))
        self.assertIsNone(short, "_no_multi_split 应短路分组，直接放行落待定")

    def test_auto_tbd_keeps_multi_image_split(self):
        """② 普通 _auto_tbd（有证据但类型不明）→ **仍要**分组建多条，不能吞掉分组。"""
        ctx = self._ctx(["/u/a.jpg", "/u/b.jpg"])
        result = asyncio.run(at._maybe_handle_multi_image_split(
            ctx, {"_auto_tbd": True, "record_type": "待定", "image_groups": self._groups()}, {}))
        self.assertIsNotNone(result, "_auto_tbd 不应短路分组")
        self.assertIn("分组确认", result.get("need_more") or [])
        self.assertEqual((result.get("ask_block") or {}).get("type"), "multi_image_split")


if __name__ == "__main__":
    unittest.main(verbosity=2)