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


def _context():
    return agent_tools.ToolContext(
        db=FakeDb(),
        user=SimpleNamespace(id=4, username="tester", project_id=7,
                             has_permission=lambda _p: True),
        project_id=7,
        project=_project(),
        message=MESSAGE,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
