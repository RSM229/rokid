"""多图逐张分类分流（B 方案）单元测试。

覆盖：
- classify_to_record_type：8 类影像 → 工作记录类型映射（含待定口径）
- group_images：按图分类归并、次序、待定兜底
- summarize：确认回执文案
- is_confirm：肯定/否定判定
- _maybe_handle_multi_image_split：首轮返回汇总且不落库；确认轮循环建多条
- _create_split_records：分组方案消费、多条记录落库（用 FakeSession 验证）

运行：
    python tests/test_multi_image_split.py
    # 或
    python -m unittest tests.test_multi_image_split -v
"""
import asyncio
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import multi_image_split
from app.services.agent_tools import (
    _create_split_records,
    _maybe_handle_multi_image_split,
)
from app.models.work_record import WorkRecord, WorkRecordStatus


# ----------------------------------------------------------------------------
# 分类映射 / 分组 / 回执 / 确认词 —— 纯逻辑
# ----------------------------------------------------------------------------

class TestClassifyToRecordType(unittest.TestCase):
    def test_mapping_hard_rules(self):
        # 巡视：施工中但工序不在旁站清单 → X
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"content": "场内便道洒水降尘", "in_progress": True})[0], "巡视")
        # 旁站：回填在旁站清单 + 施工中 → P
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"content": "污水管道回填夯实", "in_progress": True})[0], "旁站")
        # 举牌验收（非材料）→ Y → 验收
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"has_board": True})[0], "验收")
        # 举牌 + 进场材料 → M → 验收
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"has_board": True, "material_incoming": True})[0], "验收")
        # 封样 → W → 见证
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"has_seal": True})[0], "见证")
        # 监理实测读数 → H → 平行检验
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"has_reading": True})[0], "平行检验")

    def test_pending_fallthrough(self):
        # 会议交底 C → 待定
        self.assertEqual(
            multi_image_split.classify_to_record_type({"is_meeting": True})[0], "待定")
        # 其他 O（无行为信号）→ 待定
        self.assertEqual(
            multi_image_split.classify_to_record_type({})[0], "待定")
        # 字符串布尔规整
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"has_board": "true"})[0], "验收")


class TestGroupImages(unittest.TestCase):
    def _signals(self):
        return (
            [{"content": "场内便道洒水降尘", "in_progress": True}] * 3   # 巡视
            + [{"content": "污水管道回填夯实", "in_progress": True}] * 3  # 旁站
            + [{}, {}, {}]                                               # 待定
        )

    def test_group_order_and_counts(self):
        images = [f"https://img/{i}.jpg" for i in range(9)]
        groups = multi_image_split.group_images(images, self._signals())
        self.assertEqual([g.record_type for g in groups], ["巡视", "旁站", "待定"])
        self.assertEqual([len(g.images) for g in groups], [3, 3, 3])

    def test_single_category_collapses(self):
        # 单一类型多图 → 一组（分流逻辑应退化回单记录流程）
        images = [f"https://img/{i}.jpg" for i in range(3)]
        signals = [{"content": "污水管道回填夯实", "in_progress": True}] * 3
        groups = multi_image_split.group_images(images, signals)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].record_type, "旁站")
        self.assertEqual(len(groups[0].images), 3)

    def test_missing_signal_is_pending(self):
        images = ["a.jpg", "b.jpg"]
        # 第二张无信号 → 待定
        signals = [{"content": "场内便道洒水降尘", "in_progress": True}, None]
        groups = multi_image_split.group_images(images, signals)
        self.assertEqual([g.record_type for g in groups], ["巡视", "待定"])


class TestSummarizeAndConfirm(unittest.TestCase):
    def test_summarize_text(self):
        from app.services.multi_image_split import ImageGroup
        groups = [
            ImageGroup(record_type="巡视", images=["a", "b", "c"], hints=[]),
            ImageGroup(record_type="旁站", images=["d", "e", "f"], hints=[]),
            ImageGroup(record_type="待定", images=["g", "h", "i"], hints=[]),
        ]
        self.assertEqual(
            multi_image_split.summarize(groups),
            "这批图我分了 巡视3 / 旁站3 / 待定3，确认后建。")

    def test_is_confirm(self):
        self.assertTrue(multi_image_split.is_confirm("确认建"))
        self.assertTrue(multi_image_split.is_confirm("好的，建吧"))
        self.assertTrue(multi_image_split.is_confirm("可以"))
        self.assertFalse(multi_image_split.is_confirm(""))
        self.assertFalse(multi_image_split.is_confirm("不用了，先不建"))
        self.assertFalse(multi_image_split.is_confirm("取消"))


# ----------------------------------------------------------------------------
# 分流入口：首轮汇总 / 确认轮建多条 —— 需 ctx + 假 DB
# ----------------------------------------------------------------------------

class FakeUser:
    def __init__(self, uid=1, username="tester"):
        self.id = uid
        self.username = username


class FakeSession:
    """最小 AsyncSession 替身：记录 add/flush/commit，flush 时给对象补 id。"""
    def __init__(self):
        self.added = []
        self.flush_calls = 0
        self.commit_calls = 0
        self._next_id = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_calls += 1
        for o in self.added:
            if getattr(o, "id", None) is None:
                self._next_id += 1
                o.id = self._next_id

    async def commit(self):
        self.commit_calls += 1


class FakeCtx:
    def __init__(self, images=None, signals=None, message="", db=None,
                 user=None, project_id=1):
        self.images = images or []
        self.image_signals = signals or []
        self.message = message
        self.db = db
        self.user = user or FakeUser()
        self.project_id = project_id


def _nine_ctx(db=None, message=""):
    images = [f"https://img/{i}.jpg" for i in range(9)]
    signals = (
        [{"content": "场内便道洒水降尘", "in_progress": True}] * 3
        + [{"content": "污水管道回填夯实", "in_progress": True}] * 3
        + [{}, {}, {}]
    )
    return FakeCtx(images=images, signals=signals, message=message, db=db)


class TestMultiImageSplitFlow(unittest.TestCase):
    def test_first_turn_returns_summary_no_write(self):
        db = FakeSession()
        ctx = _nine_ctx(db=db, message="")
        args = {}
        result = asyncio.run(_maybe_handle_multi_image_split(ctx, args, args))
        # 首轮：need_more、回执文案、且没有任何落库动作
        self.assertFalse(result.get("ok"))
        self.assertIn("分组确认", result.get("need_more", []))
        self.assertIn("巡视3 / 旁站3 / 待定3", result.get("reply", ""))
        self.assertIn("ask_block", result)
        self.assertEqual(db.added, [])
        self.assertEqual(db.commit_calls, 0)
        # 分组方案已写入 args，供次轮消费
        self.assertIn("_agent_pending_split", args)
        plan = args["_agent_pending_split"]
        self.assertEqual([g["record_type"] for g in plan["groups"]],
                         ["巡视", "旁站", "待定"])
        self.assertEqual([len(g["images"]) for g in plan["groups"]], [3, 3, 3])

    def test_confirm_turn_creates_three_records(self):
        db = FakeSession()
        ctx = _nine_ctx(db=db, message="确认建")
        # 次轮：args 携带首轮写入的分组方案
        args = {}
        first = asyncio.run(_maybe_handle_multi_image_split(ctx, args, args))
        plan = args["_agent_pending_split"]
        # 构造确认轮 ctx（message=确认词，本轮回话无新图；方案来自草稿）
        ctx2 = FakeCtx(images=[], signals=[], message="确认建", db=db)
        args2 = {"_agent_pending_split": plan}
        result = asyncio.run(_maybe_handle_multi_image_split(ctx2, args2, args2))
        self.assertTrue(result.get("ok"))
        records = [o for o in db.added if isinstance(o, WorkRecord)]
        self.assertEqual(len(records), 3)
        self.assertEqual(set(r.record_type for r in records), {"巡视", "旁站", "待定"})
        # 待定记录为草稿状态、仅带照片
        pending = [r for r in records if r.record_type == "待定"][0]
        self.assertEqual(pending.status, WorkRecordStatus.DRAFT.value)
        self.assertEqual(len(pending.inspection_photos), 3)
        self.assertEqual(db.commit_calls, 1)
        self.assertIn("record_ids", result.get("data", {}))

    def test_witness_group_uses_witness_photos(self):
        # 含见证组的混合信号：验证见证照写入 witness_photos 而非 inspection_photos
        db = FakeSession()
        images = [f"https://img/{i}.jpg" for i in range(4)]
        signals = (
            [{"content": "场内便道洒水降尘", "in_progress": True}] * 2
            + [{"has_seal": True}] * 2
        )
        ctx = FakeCtx(images=images, signals=signals, message="", db=db)
        args = {}
        asyncio.run(_maybe_handle_multi_image_split(ctx, args, args))
        plan = args["_agent_pending_split"]
        ctx2 = FakeCtx(images=[], signals=[], message="建", db=db)
        args2 = {"_agent_pending_split": plan}
        result = asyncio.run(_maybe_handle_multi_image_split(ctx2, args2, args2))
        self.assertTrue(result.get("ok"))
        records = [o for o in db.added if isinstance(o, WorkRecord)]
        witness = [r for r in records if r.record_type == "见证"][0]
        self.assertEqual(witness.witness_photos, images[2:])
        self.assertIsNone(witness.inspection_photos)

    def test_no_signals_falls_back_to_single_record_flow(self):
        # 无逐图信号 → 分流短路返回 None，交由单记录逻辑处理（不误建多条）
        db = FakeSession()
        images = [f"https://img/{i}.jpg" for i in range(5)]
        ctx = FakeCtx(images=images, signals=[], message="", db=db)
        args = {}
        result = asyncio.run(_maybe_handle_multi_image_split(ctx, args, args))
        self.assertIsNone(result)
        self.assertEqual(db.added, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
