"""回归：验收草稿「按当前草稿生成」不能卡在「验收类型」而无创建编号。

2026-11-09 真机：一起询问归集完成后，验收类型/自检结论/监理结论/照片都
已写入 extra_fields 槽位，但表式合同校验只展开 fields[form] 一层，把这些
值全判成缺失（待补字段 ["验收类型"]、创建编号 []），记录始终没写库。
"""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from app.models.work_record import WorkRecord
from app.services import agent_tools, record_field_spec
from app.services.agent_form_requirements import resolve_event_form
from app.services.record_fields import project_fields

MESSAGE = (
    "收到，按当前草稿生成验收工作记录：污水管道基础包封验收，验收结论为合格；"
    "日期、地点和部位以已确认的水印信息为准。"
)


def _draft() -> dict:
    return {
        "record_type": "验收",
        "record_date": "2026-11-09",
        "section": "沙盛四路",
        "location": "沙盛四路等4条道路工",
        "weather": "多云",
        "work_content": "污水管道基础包封验收",
        "problems": "都符合",
        "acceptance_result": "合格",
        # 4 项询问已归集完，草稿保留已询问标记，不再重复带出询问块
        "_acceptance_asked": {"div": "预制管槽施工主体结构", "item": "管道基础"},
        "extra_fields": {
            "acceptance_type": "隐蔽工程",
            "acceptance_content": "【预制管槽施工主体结构 — 管道基础】验收内容（共 4 项）：1、核查…—— 符合",
            "supervision_conclusion": "验收合格，同意进入下道工序。",
            "self_check_conclusion": "都符合",
            "photo": "合格",
        },
    }


def _project():
    return SimpleNamespace(
        name="广东监理示范工程", construction_unit="总包A", owner_unit="建设单位A",
        supervision_unit="监理单位A", supervision_org="项目监理部",
    )


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


def _context(message: str = MESSAGE):
    return agent_tools.ToolContext(
        db=FakeDb(),
        user=SimpleNamespace(id=4, username="tester", project_id=7,
                             has_permission=lambda _p: True),
        project_id=7,
        project=_project(),
        message=message,
        material="",
    )


class AcceptanceContractTest(unittest.TestCase):
    def test_extra_fields_slots_are_valid_contract_inputs(self):
        form = record_field_spec.form_for_record_type("验收", False)
        args = _draft()
        pv = project_fields(_project())
        resolved = resolve_event_form(
            {"evidence": [], "system_project": pv},
            {
                "record_type": "验收", "record_type_confirmed": True,
                "date": "2026-11-09", "section": args["section"],
                "location": args["location"], "content": args["work_content"],
                "forms": [form], "entity_fields": pv,
                "fields": {form: args},
            },
        )
        self.assertNotIn("验收类型", resolved["missing_labels"])
        self.assertNotIn("施工单位自检结论", resolved["missing_labels"])
        self.assertNotIn("监理验收结论", resolved["missing_labels"])
        self.assertNotIn("举牌验收照片", resolved["missing_labels"])
        self.assertEqual(resolved["values"].get("acceptance_type"), "隐蔽工程")
        self.assertTrue(resolved["write_allowed"])

    def test_auto_generated_columns_do_not_block_creation(self):
        """「验收单位及人员」是系统生成栏，项目对象没有现成值也不该追问。"""
        form = record_field_spec.form_for_record_type("验收", False)
        pv = project_fields(_project())
        args = _draft()
        self.assertEqual(pv.get("acceptance_participants"), "")
        resolved = resolve_event_form(
            {"evidence": [], "system_project": pv},
            {
                "record_type": "验收", "record_type_confirmed": True,
                "date": "2026-11-09", "section": args["section"],
                "location": args["location"], "content": args["work_content"],
                "forms": [form], "entity_fields": pv,
                "fields": {form: args},
            },
        )
        self.assertNotIn("验收单位及人员", resolved["missing_labels"])

    def test_draft_waits_for_confirmation_then_creates_record(self):
        args = _draft()
        args["_require_confirmation"] = True
        ctx = _context()
        preview = asyncio.run(agent_tools._create_work_record(ctx, args))
        self.assertEqual(preview["need_more"], ["最终确认"])
        self.assertFalse(ctx.db.rows)

        args["_execution_confirmed"] = True
        ctx2 = _context()
        result = asyncio.run(agent_tools._create_work_record(ctx2, args))
        self.assertTrue(result["ok"])
        self.assertEqual((result.get("card") or {}).get("kind"), "work_record")
        self.assertTrue(any(isinstance(row, WorkRecord) for row in ctx2.db.rows))


class ConfirmationUxTest(unittest.TestCase):
    """路由层确认闸：用户说「生成」却查不到记录的交互回归。

    2026-11-09 生产实测：合同修复后草稿正确停在「最终确认」，但模型接话
    「收到，已生成」把系统追问盖掉；用户再回「按当前草稿生成」又因白名单
    只认「确认」字样被当成新一轮整理，记录始终没写库。
    """

    @staticmethod
    def _agent_source() -> str:
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "app" / "routers" / "agent.py"
        return path.read_text(encoding="utf-8")

    @classmethod
    def _confirm_tokens(cls) -> set[str]:
        import ast
        tree = ast.parse(cls._agent_source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_confirm_tokens"
                for t in node.targets
            ):
                return set(ast.literal_eval(node.value))
        raise AssertionError("_confirm_tokens 未在路由层定义")

    def test_draft_generation_phrases_are_treated_as_confirmation(self):
        tokens = self._confirm_tokens()
        for phrase in ("确认生成", "按当前草稿生成", "按草稿生成", "直接生成", "生成吧"):
            self.assertIn(phrase, tokens)
        self.assertTrue(any(t in "收到，按当前草稿生成验收工作记录" for t in tokens))

    def test_generation_phrase_unlocks_record_creation(self):
        tokens = self._confirm_tokens()
        message = "按当前草稿生成"
        awaiting = True  # 草稿已停在「最终确认」
        confirmed = awaiting and any(t in message for t in tokens)
        self.assertTrue(confirmed)

        args = _draft()
        args["_require_confirmation"] = not confirmed
        args["_execution_confirmed"] = confirmed
        ctx = _context()
        result = asyncio.run(agent_tools._create_work_record(ctx, args))
        self.assertTrue(result["ok"])
        self.assertTrue(any(isinstance(row, WorkRecord) for row in ctx.db.rows))

    def test_final_confirmation_reply_is_plain_text_no_form(self):
        src = self._agent_source()
        self.assertIn("_awaiting_final", src)
        # 2026-11-09 用户「不要核对表格，只要纯文本」：去掉「最终核对」表单卡，
        # 最终确认轮只下发纯文字指引，不再出现「确认并生成记录」按钮。
        self.assertIn("回复「确认生成」即可建档", src)
        self.assertNotIn("确认并生成记录", src)

    def test_final_confirmation_form_card_removed(self):
        src = self._agent_source()
        # 「最终核对」表单卡已移除：need_more 阶段（含「最终确认」）一律不下发表单卡。
        self.assertNotIn("work_record_confirm", src)
        self.assertNotIn('"title": f"{record_type}记录 · 最终核对"', src)


INQUIRY_CONTENT = (
    "【预制管槽施工主体结构 — 管道基础】验收内容（共 4 项，系统按询问项逐项归集）：\n"
    "1、① 核查管道基础形式（原状土/砂石/混凝土）与设计是否一致\n"
    "2、② 检查基础标高、宽度、平整度及密实度是否合格\n"
    "3、③ 混凝土（或砂石）垫层厚度、强度是否符合设计\n"
    "4、④ 基础是否验收合格后方可下管铺设"
)


class AcceptanceInquiryGuardTest(unittest.TestCase):
    """正式验收内容必须是逐项判断结论，不能是「是否…」询问句清单。

    2026-11-09 真机截图：最终确认卡的「验收内容（验收要点）」里出现
    「与设计是否一致／是否合格」的问卷，是模型把「一起询问」的要点预填进
    字段、绕过了逐条归集。合同只校验非空，问卷因此能一路带到正式记录。
    """

    @staticmethod
    def _args():
        args = _draft()
        args["extra_fields"]["acceptance_content"] = INQUIRY_CONTENT
        args["_require_confirmation"] = False
        args["_execution_confirmed"] = True
        return args

    @staticmethod
    def _stored_content(ctx) -> str:
        row = next(r for r in ctx.db.rows if isinstance(r, WorkRecord))
        return str((row.content_json.get("prov_fields") or {}).get("acceptance_content") or "")

    def test_inquiry_text_is_detected_but_conclusions_are_not(self):
        self.assertTrue(agent_tools._is_inquiry_style_content(INQUIRY_CONTENT))
        self.assertFalse(agent_tools._is_inquiry_style_content(_draft()["extra_fields"]["acceptance_content"]))
        # 要点原文里含「是否」但已带逐项结论 → 不得误伤
        self.assertFalse(agent_tools._is_inquiry_style_content(
            "1、① 检查是否按方案施工 —— 符合"))
        self.assertFalse(agent_tools._is_inquiry_style_content(""))

    def test_rule_aggregate_only_for_explicit_all_pass(self):
        items = ["① A", "② B", "③ C", "④ D"]
        self.assertEqual(len(agent_tools._rule_aggregate_acceptance(items, "4 项都符合")), 4)
        self.assertIsNone(agent_tools._rule_aggregate_acceptance(items, "第 2 项不符合"))
        self.assertIsNone(agent_tools._rule_aggregate_acceptance(items, "确认生成"))
        self.assertIsNone(agent_tools._rule_aggregate_acceptance([], "都符合"))

    def test_inquiry_content_is_rebuilt_from_explicit_conclusion(self):
        ctx = _context("4 项都符合")
        result = asyncio.run(agent_tools._create_work_record(ctx, self._args()))
        self.assertTrue(result["ok"])
        stored = self._stored_content(ctx)
        self.assertIn("—— 符合", stored)
        self.assertNotIn("是否", stored)
        self.assertIn("与设计一致", stored)

    def test_inquiry_content_without_conclusion_is_rejected(self):
        ctx = _context("确认生成")
        result = asyncio.run(agent_tools._create_work_record(ctx, self._args()))
        self.assertEqual(result["need_more"], ["验收内容（验收要点）"])
        self.assertFalse(any(isinstance(r, WorkRecord) for r in ctx.db.rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
