import asyncio
from types import SimpleNamespace

from app.models.document import Document, DocStatus
from app.models.hazard import HazardStatus
from app.models.rectification import RectificationRecord
from app.models.security_audit import SecurityAudit
from app.routers.hazard import create_rectification_doc


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Db:
    def __init__(self, hazard, project):
        self.hazard = hazard
        self.project = project
        self.rows = []
        self.commits = 0

    async def execute(self, _query):
        return _Result(self.hazard)

    async def get(self, model, _id):
        if model.__name__ == "Project":
            return self.project
        return None

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        for row in self.rows:
            if getattr(row, "id", None) is None:
                row.id = len([item for item in self.rows if getattr(item, "id", None)]) + 1

    async def commit(self):
        self.commits += 1

    async def refresh(self, _row):
        return None


def test_gdaq30308_notice_keeps_hazard_link_and_deferred_receipt_fields():
    hazard = SimpleNamespace(
        id=17, project_id=7, status=HazardStatus.REPORTED.value,
        rectification_doc_id=None, rectify_doc_id=None,
        matched_regulations_json=None, top1_risk="臨边防护缺失",
        evidence=["西侧临边无防护栏"], recheck_suggestion=["安装防护栏并复查"],
        section="3号楼二层", location="西侧", image_path="/uploads/hazard.jpg",
    )
    project = SimpleNamespace(
        id=7, name="项目A", construction_unit="总包A", supervision_org="项目监理部",
    )
    user = SimpleNamespace(
        id=4, username="tester", real_name="陈宇文", supervisor_level="专业监理工程师",
        project_id=7,
    )
    db = _Db(hazard, project)

    result = asyncio.run(create_rectification_doc(
        17, deadline="2026-10-30", user=user, db=db,
    ))

    doc = next(row for row in db.rows if isinstance(row, Document))
    rectification = next(row for row in db.rows if isinstance(row, RectificationRecord))
    audit = next(row for row in db.rows if isinstance(row, SecurityAudit))
    assert result["hazard_id"] == hazard.id
    assert doc.form_code == "gdaq_30308_yinhuan"
    assert doc.content["form_contract_version"]
    assert doc.content["to_unit"] == "总包A"
    assert doc.content["part"] == "3号楼二层 西侧"
    assert "西侧临边无防护栏" in doc.content["problems"]
    assert doc.content["receipt_date"] == ""
    assert doc.status == DocStatus.DRAFT.value
    assert rectification.hazard_id == hazard.id
    assert rectification.notice_doc_id == doc.id
    assert hazard.rectification_doc_id == doc.id
    assert hazard.status == HazardStatus.NOTIFIED.value
    assert audit.action == "hazard_rectification_doc_create"
    # The business document and hazard link commit first; project-archive
    # synchronization then runs in its own post-commit transaction.
    assert db.commits == 2
    assert doc.archive_sync_status == 'pending'
