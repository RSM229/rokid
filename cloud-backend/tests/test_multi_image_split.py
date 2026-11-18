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
    _split_content,
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

    def test_acceptance_without_board_maps_to_acceptance(self):
        """2026-11-12 真机：水印写『××验收』但视觉没读到举牌牌面时，仍按验收记录留痕。"""
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"content": "底板钢筋模板验收", "in_progress": True})[0], "验收")
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"content": "顶板钢筋模板验收", "in_progress": True})[0], "验收")
        # 否定说法不该改判
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"content": "待验收的箱涵底板", "in_progress": True})[0], "巡视")

    def test_settlement_is_not_pangzhan(self):
        """『钢板桩收方』被清单第 10 项裸词命中，实为计量复核；真正的打拔仍是旁站。"""
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"content": "9m钢板桩收方", "in_progress": True})[0], "巡视")
        self.assertEqual(
            multi_image_split.classify_to_record_type(
                {"content": "钢板桩支护打拔", "in_progress": True})[0], "旁站")


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

    def test_same_type_different_section_splits(self):
        """同类型但水印部位不同 → 各成一组（4 条道路不该塞进同一条记录）。"""
        images = ["a.jpg", "b.jpg", "c.jpg"]
        signals = [
            {"content": "场内便道洒水降尘", "in_progress": True, "section": "沙盛四路"},
            {"content": "场内便道洒水降尘", "in_progress": True, "section": "大兴路"},
            {"content": "场内便道洒水降尘", "in_progress": True, "section": "沙盛四路 "},
        ]
        groups = multi_image_split.group_images(images, signals)
        self.assertEqual([g.record_type for g in groups], ["巡视", "巡视"])
        self.assertEqual([len(g.images) for g in groups], [2, 1])
        # 尾随空白不应拆组：沙盛四路 的两张（a、c）在第一组
        self.assertEqual(groups[0].images, ["a.jpg", "c.jpg"])

    def test_section_falls_back_to_unit(self):
        """水印只写了单位工程时，也用单位工程当分组键。"""
        images = ["a.jpg", "b.jpg"]
        signals = [
            {"content": "场内便道洒水降尘", "in_progress": True, "unit": "沙盛四路"},
            {"content": "场内便道洒水降尘", "in_progress": True, "unit": "大兴路"},
        ]
        groups = multi_image_split.group_images(images, signals)
        self.assertEqual(len(groups), 2)


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

    def test_summarize_merges_sections_by_type(self):
        """2026-11-16 用户口径「识别的内容只保留到思考里」：

        同一记录类型即使按部位拆成了多组，回执也只按类型合并计数，
        不把水印识别出的部位名写出来（分组本身仍是按部位拆的）。
        """
        from app.services.multi_image_split import ImageGroup
        groups = [
            ImageGroup(record_type="验收", images=["a", "b"],
                       hints=[], wm=[{"section": "沙盛四路"}, {}]),
            ImageGroup(record_type="验收", images=["c"],
                       hints=[], wm=[{"section": "大兴路"}]),
        ]
        self.assertEqual(
            multi_image_split.summarize(groups),
            "这批图我分了 验收3，确认后建。")

    def test_is_confirm(self):
        """2026-11-17 改：零限制，非空即 True。

        用户明确要求「要有足够的自由让智能体来理解用户的说法，不能太多限制」。
        词表穷举永远补不完，is_confirm 只在空串时返回 False，其余一律 True。
        路由层的 LLM 意图识别会先判断用户是不是真的要取消/跑题——这里只管兜底放行。
        """
        self.assertTrue(multi_image_split.is_confirm("确认建"))
        self.assertTrue(multi_image_split.is_confirm("好的，建吧"))
        self.assertTrue(multi_image_split.is_confirm("可以"))
        self.assertTrue(multi_image_split.is_confirm("取消"))           # 旧测试期望 False → 新逻辑 True
        self.assertTrue(multi_image_split.is_confirm("不用了，先不建"))   # 同上
        self.assertTrue(multi_image_split.is_confirm("巡视符合要求，验收符合"))
        self.assertTrue(multi_image_split.is_confirm("随便说什么都行"))
        self.assertFalse(multi_image_split.is_confirm(""))
        self.assertFalse(multi_image_split.is_confirm("   "))


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

    def test_confirm_turn_with_no_issue_backfills_problems(self):
        """2026-11-17 用户「这个要跟用户交互的」：确认轮用户说「无异常」，
        巡视/旁站的问题及处理情况要落 problems 列，不静默留空。"""
        db = FakeSession()
        ctx = _nine_ctx(db=db, message="")
        args = {}
        asyncio.run(_maybe_handle_multi_image_split(ctx, args, args))
        plan = args["_agent_pending_split"]
        ctx2 = FakeCtx(images=[], signals=[], message="确认建，无异常", db=db)
        args2 = {"_agent_pending_split": plan}
        result = asyncio.run(_maybe_handle_multi_image_split(ctx2, args2, args2))
        self.assertTrue(result.get("ok"))
        records = [o for o in db.added if isinstance(o, WorkRecord)]
        patrol = [r for r in records if r.record_type == "巡视"][0]
        station = [r for r in records if r.record_type == "旁站"][0]
        self.assertIn("无异常", str(patrol.problems or ""))
        self.assertIn("无异常", str(station.problems or ""))
        pending = [r for r in records if r.record_type == "待定"][0]
        self.assertIsNone(pending.problems)  # 待定不套用问题栏

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


class TestHintCleaning(unittest.TestCase):
    """水印「内容」尾巴清洗 + 尾巴字段回填（2026-11-13 真机：工作内容被写成
    『底板钢筋模板验收；顶板钢筋模板验收 摄时间：2026.10.27 15:18 气：多云31°C 监理』）。

    用户明确要求「不要改一样丢一样」：内容干净了，时间/天气/人员/部位必须还在。
    """

    def test_cut_watermark_tail(self):
        self.assertEqual(
            multi_image_split.clean_hint("顶板钢筋模板验收 摄时间：2026.10.27 15:18 气：多云31°C 监理"),
            "顶板钢筋模板验收")
        self.assertEqual(
            multi_image_split.clean_hint("9m钢板桩收方 拍摄时间：2026-10-27 09:30 天气：晴26℃ 人员：监理"),
            "9m钢板桩收方")

    def test_tail_fields_are_kept(self):
        """切下来的尾巴要解析成结构化字段，不能扔。"""
        clean, tail = multi_image_split.split_hint(
            "顶板钢筋模板验收 摄时间：2026.10.27 15:18 气：多云31°C 监理")
        self.assertEqual(clean, "顶板钢筋模板验收")
        self.assertEqual(tail["taken_at"], "2026.10.27 15:18")
        self.assertEqual(tail["weather"], "多云31°C")
        self.assertEqual(tail["person"], "监理")

    def test_keep_content_that_mentions_weather_words(self):
        # 正文里出现「气温」不是水印字段（前面没有分隔符），不能误切
        self.assertEqual(
            multi_image_split.clean_hint("混凝土养护气温控制在20℃以内"),
            "混凝土养护气温控制在20℃以内")
        self.assertFalse(multi_image_split.looks_like_watermark_tail("混凝土养护气温控制在20℃以内"))

    def test_clean_hints_dedup_and_drop_empty(self):
        self.assertEqual(
            multi_image_split.clean_hints(["基坑巡视", "基坑巡视 摄时间：2026-10-27", "拍摄时间：2026-10-27", ""]),
            ["基坑巡视"])

    def test_group_hints_cleaned_and_wm_filled(self):
        """分组后：hints 干净，wm 保留时间/天气/人员（共存）。"""
        images = ["a.jpg", "b.jpg"]
        signals = [
            {"content": "底板钢筋模板验收 摄时间：2026.10.27 09:55 气：晴27°C 监理",
             "section": "dxy5-6段雨水箱涵"},
            {"content": "顶板钢筋模板验收 摄时间：2026.10.27 15:18 气：多云31°C 监理",
             "section": "dxy5-6段雨水箱涵"},
        ]
        groups = multi_image_split.group_images(images, signals)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].hints, ["底板钢筋模板验收", "顶板钢筋模板验收"])
        self.assertEqual([w["weather"] for w in groups[0].wm], ["晴27°C", "多云31°C"])
        self.assertEqual([w["taken_at"] for w in groups[0].wm],
                         ["2026.10.27 09:55", "2026.10.27 15:18"])
        self.assertEqual([w["person"] for w in groups[0].wm], ["监理", "监理"])

    def test_model_groups_also_keep_tail_fields(self):
        """模型自己给 image_groups 时（hints 原本为空），也要补内容 + 保留尾巴字段。"""
        from app.services.agent_tools import _parse_model_image_groups
        imgs = ["https://x/1.jpg", "https://x/2.jpg"]
        signals = [
            {"content": "底板钢筋模板验收 摄时间：2026.10.27 09:55 气：晴27°C 监理",
             "section": "dxy5-6段雨水箱涵"},
            {"content": "顶板钢筋模板验收 摄时间：2026.10.27 15:18 气：多云31°C 监理",
             "section": "dxy5-6段雨水箱涵"},
        ]
        groups = _parse_model_image_groups(
            [{"record_type": "验收", "image_indices": [1, 2]}], imgs, signals)
        g = groups[0]
        self.assertEqual(g.hints, ["底板钢筋模板验收", "顶板钢筋模板验收"])
        self.assertEqual([w["weather"] for w in g.wm], ["晴27°C", "多云31°C"])
        self.assertEqual([w["person"] for w in g.wm], ["监理", "监理"])


# 2026-11-13 真机（旁站记录详情）：「旁站内容」被写成
# 「基础混凝土浇筑 调度电话：0758-3371922 13822617995短号：78995 燃利：四会市大沙镇
#  马房工业开发区内公司传真：0758-3371923 时间 间：2026.10.2716:47」
# 这是水印相机模板自带的企业落款（电话/短号/传真/地址），不是施工内容；
# 且 OCR 把「时间」重复识别成「时间 间」，旧正则可丁点都切不动。
_PHONE_WM_RAW = (
    "基础混凝土浇筑 调度电话：0758-3371922 13822617995短号：78995 "
    "燃利：四会市大沙镇马房工业开发区内公司传真：0758-3371923 时间 间：2026.10.2716:47"
)


class TestWatermarkTemplateFooter(unittest.TestCase):
    """水印模板落款（电话/传真/地址）+ OCR 重复字。"""

    def test_content_keeps_only_work(self):
        self.assertEqual(multi_image_split.clean_hint(_PHONE_WM_RAW), "基础混凝土浇筑")

    def test_footer_is_kept_not_dropped(self):
        """切下来的电话/地址必须归位留档（共存，不许丢）。"""
        _clean, tail = multi_image_split.split_hint(_PHONE_WM_RAW)
        self.assertIn("0758-3371922", tail["contact"])
        self.assertIn("13822617995", tail["contact"])
        self.assertIn("四会市大沙镇", tail["location"])
        # OCR 把「时间」重复成「时间 间」，仍要认出拍摄时间
        self.assertTrue(tail["taken_at"].startswith("2026.10.27"))

    def test_ocr_repeated_char_label(self):
        self.assertEqual(
            multi_image_split.clean_hint("顶板钢筋模板验收 时间 间：2026.10.27 15:18"),
            "顶板钢筋模板验收")

    def test_anchor_detects_template_footer(self):
        # 只有摄时间/天气的老锚点会漏判这类水印 → 单图建记录时完全不清洗
        self.assertTrue(multi_image_split.looks_like_watermark_tail(_PHONE_WM_RAW))

    def test_phone_in_body_still_not_mistaken(self):
        # 正文里正常提到电话（不带冒号）不能当水印尾巴
        self.assertFalse(multi_image_split.looks_like_watermark_tail("已电话通知施工单位整改"))

    def test_group_keeps_contact_and_location(self):
        signals = [{"content": _PHONE_WM_RAW, "section": "直卸"}]
        groups = multi_image_split.group_images(["a.jpg"], signals)
        g = groups[0]
        self.assertEqual(g.hints, ["基础混凝土浇筑"])
        self.assertIn("0758-3371922", g.wm[0]["contact"])
        self.assertIn("四会市大沙镇", g.wm[0]["location"])

    def test_record_backfill_uses_photo_date(self):
        """记录日期取照片拍摄时间（2026-10-27），不是建单当天。"""
        from app.services.agent_tools import _split_watermark_fields
        signals = [{"content": _PHONE_WM_RAW, "section": "直卸"}]
        g = multi_image_split.group_images(["a.jpg"], signals)[0]
        fields = _split_watermark_fields(g.wm)
        self.assertEqual(fields["date"].isoformat(), "2026-10-27")
        self.assertTrue(fields["contacts"])
        self.assertTrue(fields["locations"])

    def test_no_clock_in_watermark_does_not_invent_start_time(self):
        """水印只有日期没时分 → start_dt=None，绝不臆造开始时刻。"""
        from app.services.agent_tools import _split_watermark_fields
        fields = _split_watermark_fields([
            {"section": "大兴路", "taken_at": "2026-10-27"},
        ])
        self.assertIsNone(fields["start_dt"])


class TestSplitRecordStartTimeBackfill(unittest.TestCase):
    """2026-11-17 用户截图「日期和位置呢」：分组建单此前只写记录日期，
    详情里「巡视开始时间（年月日 时）」读 inspection_start_time 一直空着。
    水印有拍摄时刻就必须回填到开始时间栏（旁站→station_start_time）。"""

    def _build(self, record_type: str):
        from app.services.multi_image_split import ImageGroup
        db = FakeSession()
        ctx = FakeCtx(db=db)
        wm = [{"section": "直卸", "taken_at": "2026.10.27 16:47",
               "weather": "晴26℃"}]
        group = ImageGroup(record_type=record_type, images=["a.jpg"],
                           hints=[_PHONE_WM_RAW], wm=wm)
        result = asyncio.run(_create_split_records(ctx, [group]))
        self.assertTrue(result.get("ok"))
        records = [o for o in db.added if isinstance(o, WorkRecord)]
        self.assertEqual(len(records), 1)
        return records[0]

    def test_patrol_start_time_from_watermark_clock(self):
        rec = self._build("巡视")
        self.assertIsNotNone(rec.inspection_start_time)
        self.assertEqual(rec.inspection_start_time.strftime("%Y-%m-%d %H:%M"),
                         "2026-10-27 16:47")
        self.assertEqual(rec.record_date.isoformat(), "2026-10-27")
        self.assertIsNone(rec.inspection_end_time)  # 不臆造结束时间

    def test_station_start_time_from_watermark_clock(self):
        rec = self._build("旁站")
        self.assertIsNotNone(rec.station_start_time)
        self.assertEqual(rec.station_start_time.strftime("%Y-%m-%d %H:%M"),
                         "2026-10-27 16:47")


if __name__ == "__main__":
    unittest.main(verbosity=2)
