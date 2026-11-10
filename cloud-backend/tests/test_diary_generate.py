"""监理日记（个人维度）生成测试 —— 作者过滤 + 落款 + 发文归集 + 归档映射。

与 test_safety_log_aggregate 同构：文件型 SQLite 端到端真跑，确认：
1. author_id 只聚合本人当日记录/隐患（别人的记录不进个人日记）；
2. 文种为「监理日记」（DocType.SUPERVISION_DIARY）、form_code=jianli_riji；
3. 落款回到作者本人；
4. 当天发出的通知单类文书进「重要发文」栏；
5. 归档文件夹映射到「监理日记」。

运行：cd cloud-backend && .venv/Scripts/python -m unittest tests.test_diary_generate
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

_WORKDIR = tempfile.mkdtemp(prefix="gdjl_diary_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(
    Path(_WORKDIR) / "test.db"
).replace("\\", "/")
os.environ.setdefault("KNOWLEDGE_BASE_PATH", str(Path(_WORKDIR) / "kb"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _name in list(sys.modules):
    if _name == "app" or _name.startswith("app."):
        del sys.modules[_name]

from app.db import async_session, init_db  # noqa: E402
from app.models.document import DocType, Document  # noqa: E402
from app.models.hazard import Hazard, HazardStatus  # noqa: E402
from app.models.project_member import ProjectMember  # noqa: E402
from app.models.user import Project, User, UserRole  # noqa: E402
from app.models.work_record import (  # noqa: E402
    WorkRecord,
    WorkRecordStatus,
    WorkRecordType,
)
from app.services.period_report_service import generate_supervision_log  # noqa: E402
from app.services.project_archive import (  # noqa: E402
    FOLDER_BY_DOC_TYPE,
    FOLDER_BY_FORM_CODE,
)

TARGET_DATE = date(2026, 10, 21)
DAY_START = datetime.combine(TARGET_DATE, datetime.min.time())


async def _seed() -> dict:
    async with async_session() as db:
        project = Project(name="监理日记测试工程", code="TEST-RJ")
        db.add(project)
        await db.flush()

        me = User(
            username="diaryme",
            real_name="日记本人",
            role=UserRole.SPECIAL_SUPERVISOR.value,
            password_hash="$2b$12$unused-hash-for-test",
            project_id=project.id,
            is_active=True,
        )
        other = User(
            username="diaryother",
            real_name="同事乙",
            role=UserRole.INSPECTOR.value,
            password_hash="$2b$12$unused-hash-for-test",
            project_id=project.id,
            is_active=True,
        )
        db.add_all([me, other])
        await db.flush()

        db.add_all([
            ProjectMember(project_id=project.id, user_id=me.id, role=UserRole.SPECIAL_SUPERVISOR.value),
            ProjectMember(project_id=project.id, user_id=other.id, role=UserRole.INSPECTOR.value),
        ])

        # 我当天的巡视记录
        my_record = WorkRecord(
            record_type=WorkRecordType.INSPECTION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=me.id,
            project_id=project.id,
            section="1#楼",
            work_content="午间巡视 1#楼三层梁板模板支撑。",
            problems="局部立杆未设扫地杆。",
            treatment="已要求施工单位即时补设。",
            record_date=TARGET_DATE,
            created_at=DAY_START + timedelta(hours=1),
        )
        # 同事当天也有记录（不应进我的日记）
        other_record = WorkRecord(
            record_type=WorkRecordType.INSPECTION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=other.id,
            project_id=project.id,
            section="2#楼",
            work_content="同事乙巡视 2#楼砌体。",
            problems="",
            treatment="",
            record_date=TARGET_DATE,
            created_at=DAY_START + timedelta(hours=2),
        )
        # 我当天的隐患
        my_hazard = Hazard(
            project_id=project.id,
            reporter_id=me.id,
            status=HazardStatus.REPORTED.value,
            risk_category="脚手架",
            top1_risk="外架连墙件缺失",
            location="1#楼外架",
            section="1#楼",
            created_at=DAY_START + timedelta(hours=3),
        )
        db.add_all([my_record, other_record, my_hazard])
        await db.flush()

        # 当天我发出的整改通知单（应进「重要发文」栏）
        db.add(Document(
            doc_type=DocType.RECTIFICATION.value,
            title="关于开展外架连墙件专项整改的通知",
            content={"doc_number": "GD-TZ-001"},
            form_code="gdaq_30308_yinhuan",
            project_id=project.id,
            creator_id=me.id,
            status="草稿",
            created_at=DAY_START + timedelta(hours=4),
        ))

        await db.commit()
        return {"project_id": project.id, "me": me.id, "other": other.id}


class DiaryGenerateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        asyncio.run(init_db())
        cls.ids = asyncio.run(_seed())

    def test_01_diary_only_aggregates_own_records(self):
        """个人日记只合成本人记录：不含同事记录，隐患/发文正确归入。"""
        async def run():
            async with async_session() as db:
                return await generate_supervision_log(
                    db, self.ids["project_id"], TARGET_DATE,
                    author_id=self.ids["me"], force=True,
                )
        result = asyncio.run(run())
        self.assertFalse(result["skipped"])

        async def check():
            async with async_session() as db:
                doc = await db.get(Document, result["doc_id"])
                return doc, (doc.content or {})
        doc, content = asyncio.run(check())

        # 文种与表式
        self.assertEqual(doc.doc_type, DocType.SUPERVISION_DIARY.value)
        self.assertEqual(doc.doc_type, "监理日记")
        self.assertEqual(doc.form_code, "jianli_riji")
        # 落款 = 作者本人
        self.assertEqual(content["supervisor"], "日记本人")
        # 施工进展栏：只有我的记录，不含同事的「2#楼砌体」
        self.assertIn("1#楼三层梁板模板支撑", content["construction_progress"])
        self.assertNotIn("2#楼砌体", content["construction_progress"])
        # 隐患
        self.assertIn("连墙件", content["problems_section"])
        # 我当天发的整改通知单进「重要发文」
        self.assertIn("外架连墙件专项整改", content["document_section"])

    def test_02_other_user_diary_is_separate_and_empty_of_mine(self):
        """同事的日记不应包含我的记录（对称隔离）。"""
        async def run():
            async with async_session() as db:
                return await generate_supervision_log(
                    db, self.ids["project_id"], TARGET_DATE,
                    author_id=self.ids["other"], force=True,
                )
        result = asyncio.run(run())

        async def check():
            async with async_session() as db:
                doc = await db.get(Document, result["doc_id"])
                return (doc.content or {})
        content = asyncio.run(check())
        self.assertIn("2#楼砌体", content["construction_progress"])
        self.assertNotIn("1#楼三层梁板模板支撑", content["construction_progress"])
        self.assertNotIn("连墙件", content["problems_section"])
        # 同事当天没发文，重要发文为空缺省语
        self.assertEqual(content["document_section"], "  无。")

    def test_03_supervision_log_without_author_is_project_wide(self):
        """不带 author_id 的日志仍是项目级（不回归）：含两人的记录。"""
        async def run():
            async with async_session() as db:
                return await generate_supervision_log(
                    db, self.ids["project_id"], TARGET_DATE, force=True,
                )
        result = asyncio.run(run())

        async def check():
            async with async_session() as db:
                doc = await db.get(Document, result["doc_id"])
                return doc, (doc.content or {})
        doc, content = asyncio.run(check())
        self.assertEqual(doc.doc_type, DocType.SUPERVISION_LOG.value)
        self.assertEqual(doc.form_code, "gd_b_220_rizhi")
        self.assertIn("1#楼三层梁板模板支撑", content["construction_progress"])
        self.assertIn("2#楼砌体", content["construction_progress"])

    def test_04_archive_folder_mapping(self):
        """归档映射：form_code 与 doc_type 都指向「监理日记」目录。"""
        self.assertEqual(FOLDER_BY_FORM_CODE.get("jianli_riji"), "监理日记")
        self.assertEqual(FOLDER_BY_DOC_TYPE.get("监理日记"), "监理日记")


if __name__ == "__main__":
    unittest.main()