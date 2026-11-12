"""监理日记（个人维度）生成测试 —— 作者过滤 + 落款 + 发文归集 + 归档映射。

与 test_safety_log_aggregate 同构：文件型 SQLite 端到端真跑，确认：
1. author_id 只聚合本人当日记录/隐患（别人的记录不进个人日记）；
2. 文种为「监理日记」（DocType.SUPERVISION_DIARY）、form_code=jianli_riji；
3. 落款回到作者本人；
4. 当天发出的通知单类文书进「重要发文」栏；
5. 归档文件夹映射到「监理日记」；
6. 真渲染 docx：日记抬头为「监理日记」+ 内部编号 + 记录人签名，日志保持省统表原样；
7. export-pdf 按 form_code 重渲染时 jianli_riji 有专用填充器，栏目不丢。

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
        # 编号带作者：同一天多人各写各的日记，不能重号
        self.assertTrue(content["doc_number"].startswith("GD-JL-RJ-"))
        self.assertTrue(content["doc_number"].endswith(f"-{self.ids['me']}"))
        # 记录人姓名进渲染数据（日记正文可辨识）
        self.assertEqual(content["recorder_name"], "日记本人")
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
        # 项目日志不带个人标识、不改成日记编号
        self.assertNotIn("recorder_name", content)
        self.assertTrue(content["doc_number"].startswith("GD-JL-"))
        self.assertNotIn("GD-JL-RJ", content["doc_number"])
        self.assertIn("1#楼三层梁板模板支撑", content["construction_progress"])
        self.assertIn("2#楼砌体", content["construction_progress"])

    def test_04_archive_folder_mapping(self):
        """归档映射：form_code 与 doc_type 都指向「监理日记」目录。"""
        self.assertEqual(FOLDER_BY_FORM_CODE.get("jianli_riji"), "监理日记")
        self.assertEqual(FOLDER_BY_DOC_TYPE.get("监理日记"), "监理日记")

    def test_05_diary_render_has_own_header(self):
        """真渲染 docx：日记抬头/编号/记录人与日志不同，且栏目不丢。"""
        from docx import Document as DocxDocument
        from app.services.form_registry import get_form
        from app.services.form_renderer import FILLERS, render_form

        # export-pdf 按 form_code 重渲染，未注册填充器会掉进 fill_generic 丢栏目
        self.assertIs(FILLERS.get("jianli_riji"), FILLERS.get("gd_b_220_rizhi"))
        self.assertEqual(get_form("jianli_riji").template, "21监理日志-房建新.docx")

        data = {
            "project_name": "监理日记测试工程",
            "doc_number": "GD-JL-RJ-1-20261021-1",
            "date": "2026-10-21",
            "weekday": "星期三",
            "temperature_high": "30",
            "temperature_low": "20",
            "weather_morning": "晴",
            "construction_progress": "1#楼三层梁板模板支撑。",
            "problems_section": "外架连墙件缺失。",
            "recorder_name": "日记本人",
        }
        diary_docx = render_form("jianli_riji", dict(data), output_dir=Path(_WORKDIR))
        d = DocxDocument(str(diary_docx))
        heads = [p.text for p in d.paragraphs]
        self.assertIn("监理日记", heads)
        self.assertFalse(any("GD-B-220" in t for t in heads))
        cells = "\n".join(c.text for r in d.tables[0].rows for c in r.cells)
        self.assertIn("记录人（签名）：日记本人", cells)
        self.assertIn("1#楼三层梁板模板支撑", cells)
        self.assertIn("外架连墙件缺失", cells)

        log_data = {k: v for k, v in data.items() if k != "recorder_name"}
        log_data["doc_number"] = "GD-JL-1-20261021"
        log_docx = render_form("gd_b_220_rizhi", log_data, output_dir=Path(_WORKDIR))
        ld = DocxDocument(str(log_docx))
        log_heads = [p.text for p in ld.paragraphs]
        self.assertIn("监理日志", log_heads)
        self.assertTrue(any("GD-B-220" in t for t in log_heads))
        self.assertNotIn(
            "记录人（签名）：日记本人",
            "\n".join(c.text for r in ld.tables[0].rows for c in r.cells),
        )

    def test_06_fallback_pdf_title(self):
        """省统表转换失败走自绘 PDF 时，日记抬头仍是「监理日记」。"""
        from app.services.doc_generator import _log_title_for

        class _FakeDoc:
            def __init__(self, doc_type, form_code):
                self.doc_type = doc_type
                self.form_code = form_code

        self.assertEqual(_log_title_for(_FakeDoc("监理日记", "jianli_riji")), "监理日记")
        self.assertEqual(_log_title_for(_FakeDoc("监理日志", "gd_b_220_rizhi")), "监理日志")


if __name__ == "__main__":
    unittest.main()