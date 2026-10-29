"""D06 安全监理日志日聚合 + D02 extra_fields 透传（文件型 SQLite 端到端）。

🔴 为什么必须端到端跑真库：`py_compile` 只查语法，抓不到运行时
AttributeError（例如 Hazard 上并没有 treatment 字段，处理措施在 WorkRecord 上）。
安全监理日志此前根本没有自己的聚合入口，日分支被硬编码成普通监理日志，
所以这里用真 SQLite + 真 docx 模板渲染，确认能落库并渲出文书。

运行：cd cloud-backend && .venv/Scripts/python -m unittest tests.test_safety_log_aggregate
（本仓库 venv 未装 pytest，故统一 unittest 风格，勿写成 monkeypatch 夹具。）
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

# app.db 在导入时即按 DATABASE_URL 建 engine；本模块必须自己全新导入，
# 否则沿用 .env 的 MySQL URL 会连不上生产/测试库。
_WORKDIR = tempfile.mkdtemp(prefix="gdjl_safety_log_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(
    Path(_WORKDIR) / "test.db"
).replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _name in list(sys.modules):
    if _name == "app" or _name.startswith("app."):
        del sys.modules[_name]

from app.db import async_session, init_db  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.hazard import Hazard, HazardStatus  # noqa: E402
from app.models.project_member import ProjectMember  # noqa: E402
from app.models.rectification import RectificationRecord  # noqa: E402
from app.models.user import Project, User, UserRole  # noqa: E402
from app.models.work_record import (  # noqa: E402
    WorkRecord,
    WorkRecordStatus,
    WorkRecordType,
)
from app.services.period_report_service import (  # noqa: E402
    generate_aggregate_doc,
    generate_safety_log,
)

# 固定基准日，避免"今天"漂移导致跨日断言不稳
TARGET_DATE = date(2026, 10, 20)
DAY_START = datetime.combine(TARGET_DATE, datetime.min.time())


async def _seed() -> dict:
    """播种：项目 + 成员 + 巡视记录（带处理措施）+ 隐患 + 整改记录。"""
    async with async_session() as db:
        project = Project(name="安全日志测试工程", code="TEST-AQ")
        db.add(project)
        await db.flush()

        user = User(
            username="safetytest",
            real_name="测试安全监理",
            role=UserRole.SPECIAL_SUPERVISOR.value,
            password_hash="$2b$12$unused-hash-for-test",
            project_id=project.id,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        db.add(ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role=UserRole.SPECIAL_SUPERVISOR.value,
        ))

        # 巡视记录：有 problems/treatment（处理措施栏的真实来源在 WorkRecord 上）
        record = WorkRecord(
            record_type=WorkRecordType.INSPECTION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=user.id,
            project_id=project.id,
            section="3#楼二层",
            location="临边洞口",
            work_content="检查二层临边防护栏杆搭设。",
            inspection_remark="防护栏杆局部缺失。",
            problems="二层东侧临边防护栏杆缺失两处。",
            treatment="已口头通知施工单位立即补设，并下发监理通知单。",
            record_date=TARGET_DATE,
            created_at=DAY_START + timedelta(hours=1),
        )
        db.add(record)

        # 隐患：走「危大工程安全状况」与「隐患整改情况」两栏
        hazard = Hazard(
            project_id=project.id,
            reporter_id=user.id,
            status=HazardStatus.RECTIFYING.value,
            risk_category="危大工程-脚手架",
            top1_risk="落地式钢管脚手架连墙件被拆除",
            location="1#楼外架",
            section="1#楼",
            recheck_suggestion=["限 3 日内恢复连墙件并复验"],
            created_at=DAY_START + timedelta(hours=2),
        )
        db.add(hazard)
        await db.flush()

        db.add(RectificationRecord(
            hazard_id=hazard.id,
            deadline=DAY_START + timedelta(days=3),
            rectified_at=DAY_START + timedelta(hours=20),
            recheck_passed=False,
            recheck_remark="连墙件仅恢复一处，另一处仍未处理。",
            recheck_at=DAY_START + timedelta(hours=21),
            created_at=DAY_START + timedelta(hours=3),
        ))

        await db.commit()
        return {"project_id": project.id, "user_id": user.id}


class SafetyLogAggregateTest(unittest.TestCase):
    """安全监理日志日聚合与 extra_fields 透传。"""

    @classmethod
    def setUpClass(cls):
        asyncio.run(init_db())
        cls.ids = asyncio.run(_seed())

    def test_01_generate_safety_log_writes_doc_and_docx(self):
        """能真跑出文书：落库 + form_code 正确 + docx 渲染成功（非空路径）。"""
        async def run():
            async with async_session() as db:
                return await generate_safety_log(
                    db, self.ids["project_id"], TARGET_DATE, force=True,
                )
        result = asyncio.run(run())

        self.assertFalse(result["skipped"])
        self.assertEqual(result["form_code"], "anquan_jianli_rizhi")
        self.assertEqual(result["form_name"], "安全监理日志")
        self.assertTrue(result.get("docx_url"), "docx 未渲染成功，docx_url 为空")

        async def check():
            async with async_session() as db:
                doc = await db.get(Document, result["doc_id"])
                content = doc.content or {}
                return doc, content
        doc, content = asyncio.run(check())

        self.assertEqual(doc.form_code, "anquan_jianli_rizhi")
        # 施工内容栏
        self.assertIn("临边防护栏杆", content["content"])
        # 安全检查情况栏（巡视 + 隐患）
        self.assertIn("防护栏杆局部缺失", content["safety_check"])
        self.assertIn("连墙件", content["safety_check"])
        # 处理措施栏：来自 WorkRecord.treatment/problems，不是 Hazard.treatment（不存在）
        self.assertIn("下发监理通知单", content["treatment"])
        self.assertIn("限期整改", content["treatment"])
        # 隐患整改情况栏：复查不合格
        self.assertIn("复查不合格", content["rectify_status"])
        # 危大工程安全状况栏
        self.assertIn("连墙件", content["hazard_status"])
        self.assertEqual(content["safety_supervisor"], "测试安全监理")

    def test_02_aggregate_day_routes_safety_form_code(self):
        """generate_aggregate_doc 的 day 分支按 form_code 路由到安全日志（D06 修复点）。

        修复前：day 分支硬编码 gd_b_220_rizhi，说「生成安全监理日志」会静默产出普通监理日志。
        """
        async def run():
            async with async_session() as db:
                return await generate_aggregate_doc(
                    db, self.ids["project_id"], "day", TARGET_DATE.isoformat(),
                    form_code="anquan_jianli_rizhi", force=True,
                )
        result = asyncio.run(run())

        self.assertEqual(result["form_code"], "anquan_jianli_rizhi")
        self.assertEqual(result["form_name"], "安全监理日志")
        self.assertNotEqual(result["form_code"], "gd_b_220_rizhi")

    def test_03_aggregate_day_default_still_supervision_log(self):
        """不带 form_code 的 day 聚合仍是普通监理日志（不回归既有行为）。"""
        async def run():
            async with async_session() as db:
                return await generate_aggregate_doc(
                    db, self.ids["project_id"], "day", TARGET_DATE.isoformat(), force=True,
                )
        result = asyncio.run(run())

        self.assertEqual(result["form_code"], "gd_b_220_rizhi")
        self.assertEqual(result["form_name"], "监理日志")

    def test_04_extra_fields_reach_supervision_log(self):
        """D02：extra_fields（天气/上级检查/其他事项）真能落进监理日志 content。"""
        async def run():
            async with async_session() as db:
                return await generate_aggregate_doc(
                    db, self.ids["project_id"], "day", TARGET_DATE.isoformat(),
                    force=True,
                    extra_fields={
                        "weather": "晴",
                        "supervision_check": "省质监站抽查二层钢筋，提出 2 条意见。",
                        "other_section": "下午 14:00-16:00 停水停电，现场停工。",
                    },
                )
        result = asyncio.run(run())

        async def check():
            async with async_session() as db:
                doc = await db.get(Document, result["doc_id"])
                return doc.content or {}
        content = asyncio.run(check())

        self.assertEqual(content["weather_morning"], "晴")
        self.assertEqual(content["weather_afternoon"], "晴")
        self.assertEqual(content["weather_night"], "晴")
        self.assertEqual(content["weather_source"], "人工补充")
        # 渲染器 _rizhi_other_lines 读 supervision_check / misc_section
        self.assertIn("省质监站抽查", content["supervision_check"])
        self.assertIn("停水停电", content["misc_section"])

    def test_05_extra_fields_reach_safety_log_weather(self):
        """D02+D06 交叉：安全日志也吃 extra_fields 的天气覆盖。"""
        async def run():
            async with async_session() as db:
                return await generate_safety_log(
                    db, self.ids["project_id"], TARGET_DATE,
                    force=True, extra_fields={"weather": "大雨"},
                )
        result = asyncio.run(run())

        async def check():
            async with async_session() as db:
                doc = await db.get(Document, result["doc_id"])
                return doc.content or {}
        content = asyncio.run(check())

        self.assertEqual(content["weather"], "大雨")
        self.assertEqual(content["weather_am"], "大雨")
        self.assertEqual(content["weather_pm"], "大雨")

    def test_06_extra_fields_raw_schema_keys_normalized(self):
        """D02 归一：HTTP 调用方直接传表字段 key（7_12/7_13）也要落进文书。

        web-admin 走 /api/form-docs/aggregate，未必先归一 canonical；
        _canon_extra_fields 兜这道，否则用户填了却不进文书（静默失效）。
        """
        async def run():
            async with async_session() as db:
                return await generate_aggregate_doc(
                    db, self.ids["project_id"], "day", TARGET_DATE.isoformat(),
                    force=True,
                    extra_fields={
                        "7_12": "市住建局飞行检查，要求补充见证取样台账。",
                        "7_13": "夜间混凝土浇筑，已安排旁站。",
                        "weather": "多云",
                    },
                )
        result = asyncio.run(run())

        async def check():
            async with async_session() as db:
                doc = await db.get(Document, result["doc_id"])
                return doc.content or {}
        content = asyncio.run(check())

        # 7_12 -> supervision_check，7_13 -> other_section 并入 misc_section
        self.assertIn("市住建局飞行检查", content["supervision_check"])
        self.assertIn("夜间混凝土浇筑", content["misc_section"])
        self.assertEqual(content["weather_morning"], "多云")
        # 原始 key 不应残留在 content 里（只留渲染器认得的键）
        self.assertNotIn("7_12", content)
        self.assertNotIn("7_13", content)

    def test_07_force_false_skips_when_exists(self):
        """force=False 时当日已有安全日志应 skipped，不重复堆件。"""
        async def run():
            async with async_session() as db:
                first = await generate_safety_log(
                    db, self.ids["project_id"], TARGET_DATE, force=True,
                )
            async with async_session() as db:
                second = await generate_safety_log(
                    db, self.ids["project_id"], TARGET_DATE, force=False,
                )
            return first, second
        first, second = asyncio.run(run())

        self.assertTrue(second["skipped"])
        self.assertEqual(second["doc_id"], first["doc_id"])


class FormCodeResolveTest(unittest.TestCase):
    """表式名归一：中文表名必须解析成 code，否则 D06 路由静默失效。"""

    def test_chinese_names_resolve_to_safety_code(self):
        from app.services.agent_tools import resolve_provform_form_code

        for raw in ("安全监理日志", "安全日志", "anquan_jianli_rizhi", "安全监理日记"):
            self.assertEqual(
                resolve_provform_form_code(raw), "anquan_jianli_rizhi",
                f"「{raw}」未归一到 anquan_jianli_rizhi",
            )

    def test_plain_log_not_stolen_by_safety_alias(self):
        """「监理日志」不能被「安全监理日志」抢走（2026-10-27 建错文书同类回归）。"""
        from app.services.agent_tools import resolve_provform_form_code

        self.assertEqual(resolve_provform_form_code("监理日志"), "gd_b_220_rizhi")
        self.assertEqual(
            resolve_provform_form_code("生成今天的监理日志"), "gd_b_220_rizhi",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
