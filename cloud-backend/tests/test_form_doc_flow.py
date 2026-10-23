"""省统表文书流功能测试（文件型 SQLite，通过 app 自身 engine）。

环境变量 DATABASE_URL 指向临时 SQLite 文件 → import app.main（模块级 engine
直接使用该 URL）→ init_db() 建表并播种项目/用户/巡视记录 → TestClient 调用
/api/form-docs/* 验证清单、记录→docx、日聚合→docx、导出接口。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_WORKDIR = tempfile.mkdtemp(prefix="gdjl_formdoc_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(
    Path(_WORKDIR) / "test.db"
).replace("\\", "/")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# app.db 在导入时即按 DATABASE_URL 创建 engine；若先前被其他测试模块导入，
# 会沿用 .env 的 MySQL URL 导致本模块连库失败。这里强制本模块自己全新导入。
for _name in list(sys.modules):
    if _name == "app" or _name.startswith("app."):
        del sys.modules[_name]

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import async_session, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import Project, User  # noqa: E402
from app.models.work_record import WorkRecord, WorkRecordType, WorkRecordStatus  # noqa: E402


def _complete_form_fields(form_code: str, overrides: dict | None = None) -> dict[str, str]:
    """Give renderer-flow fixtures a complete reviewed contract, not fake legacy blanks."""
    from app.services.record_field_spec import fields_of, options_for

    values: dict[str, str] = {}
    for field in fields_of(form_code):
        if not field.get("required") or field.get("kind") == "signoff":
            continue
        canonical = field["canonical"]
        table_type = field.get("table_type") or ""
        options = options_for(field)
        if options:
            value = str(options[0])
        elif "日期时间" in table_type:
            value = "2025-01-02 09:00"
        elif "日期" in table_type:
            value = "2025-01-02"
        elif "数字" in table_type or "序号" in table_type:
            value = "1"
        else:
            value = f"测试{field.get('label') or canonical}"
        values[canonical] = value
    values.update(overrides or {})
    return values


async def _seed() -> dict:
    await init_db()
    async with async_session() as db:
        project = Project(name="测试工程A", code="TEST-A")
        db.add(project)
        await db.flush()
        user = User(
            username="formtest",
            real_name="测试监理",
            role="专业监理工程师",
            password_hash="$2b$12$unused-hash-for-test",
            project_id=project.id,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        record = WorkRecord(
            record_type=WorkRecordType.INSPECTION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=user.id,
            project_id=project.id,
            section="3#楼二层",
            location="3#楼",
            work_content="梁板钢筋绑扎检查，间距符合要求。",
            inspection_remark="发现一处垫块缺失，已要求整改。",
            content_json={"prov_fields": _complete_form_fields("gd_b_214_xunshi", {
                "project_name": "测试工程A", "construction_unit": "测试施工单位",
                "section": "3#楼二层", "content": "梁板钢筋绑扎检查，间距符合要求。",
                "problems": "发现一处垫块缺失，已要求整改。",
            })},
        )
        db.add(record)
        await db.commit()
        return {"user_id": user.id, "project_id": project.id, "record_id": record.id}


async def _seed_agent_record(ids: dict, user_id: int) -> int:
    async with async_session() as db:
        record = WorkRecord(
            record_type=WorkRecordType.INSPECTION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=user_id,
            project_id=ids["project_id"],
            section="安全巡视",
            location="广州市南沙区·广东省中医院南沙",
            work_content="记录这个工作",
            inspection_remark="记录这个工作",
            # Agent records use content_json.media_ids as their immutable
            # evidence snapshot; this mutable legacy field must be ignored.
            inspection_photos=["/uploads/unrelated-history-photo.jpg"],
            # 防止测试数据混入 test_aggregate_day 的当日统计。
            created_at=datetime(2025, 1, 2, 9, 0),
            content_json={
                "prov_fields": _complete_form_fields("gd_b_214_xunshi", {
                    "project_name": "广东省中西医结合应急救治中心项目",
                    "construction_unit": "广州一建建设集团有限公司",
                    "inspector": "陈宇文", "section": "安全巡视",
                    "content": "临边防护检查符合要求。", "problems": "符合要求",
                }),
                "source": "agent_review_plan",
                "media_ids": [],
                "form_context": {
                    "project_name": "广东省中西医结合应急救治中心项目",
                    "owner_unit": "广州市南沙区建设中心",
                    "construction_unit": "广州一建建设集团有限公司",
                    "supervision_unit": "广州珠江监理咨询集团有限公司",
                    "inspector": "陈宇文",
                },
                "form_field_candidates": {
                    "project_name": "广东省中西医结合应急救治中心项目",
                    "construction_unit": "广州一建建设集团有限公司",
                    "inspector": "陈宇文",
                },
            },
        )
        db.add(record)
        await db.commit()
        return record.id


async def _seed_legacy_agent_record(ids: dict, user_id: int) -> int:
    """Record saved before project_name aliases; raw OCR must still recover it."""
    async with async_session() as db:
        record = WorkRecord(
            record_type=WorkRecordType.INSPECTION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=user_id,
            project_id=ids["project_id"],
            section="安全巡视",
            location="广州市南沙区·广东省中医院南沙",
            work_content="记录这个工作",
            inspection_remark="记录这个工作",
            created_at=datetime(2025, 1, 2, 9, 0),
            content_json={
                "prov_fields": _complete_form_fields("gd_b_214_xunshi", {
                    "project_name": "广东省中西医结合应急救治中心项目",
                    "construction_unit": "广州一建建设集团有限公司",
                    "inspector": "陈宇文", "section": "安全巡视",
                    "content": "临边防护检查符合要求。", "problems": "符合要求",
                }),
                "source": "agent_review_plan",
                "media_ids": [],
                "evidence_notes": [
                    "施工单位：广州一建建设集团有限公司 · OCR文字：广东省中西医结合应急救治；"
                    "中心项目；珠江监理；施工区域：安全巡视；检查人员：陈宇文；"
                    "建设单位：广州市南沙区建设中心；监理单位：广州珠江监理咨询集团有限公司；"
                    "施工单位：广州一建建设集团有限公司 · 图片观察：施工现场",
                ],
                "evidence_watermark_fields": [{
                    "evidence_id": "legacy-1",
                    "fields": {"person": "陈宇文", "contractor": "广州一建建设集团有限公司"},
                }],
            },
        )
        db.add(record)
        await db.commit()
        return record.id


async def _seed_typed_agent_record(ids: dict, user_id: int, record_type: str) -> int:
    async with async_session() as db:
        common_json = {
            "source": "agent_review_plan",
            "media_ids": [],
            "form_context": {
                "project_name": "广东省中西医结合应急救治中心项目",
                "construction_unit": "广州一建建设集团有限公司",
                "supervision_unit": "广州珠江监理咨询集团有限公司",
                "inspector": "陈宇文",
            },
            "form_field_candidates": {
                "project_name": "广东省中西医结合应急救治中心项目",
                "construction_unit": "广州一建建设集团有限公司",
                "inspector": "陈宇文",
                "weather": "晴",
            },
        }
        if record_type == WorkRecordType.STATION.value:
            record = WorkRecord(
                record_type=record_type,
                status=WorkRecordStatus.SUBMITTED.value,
                creator_id=user_id,
                project_id=ids["project_id"],
                record_date=datetime(2025, 1, 2).date(),
                created_at=datetime(2025, 1, 2, 10, 0),
                section="西楼A区十层墙柱",
                station_location="西楼A区十层墙柱",
                station_process="混凝土浇筑",
                station_content="现场按专项方案连续浇筑，人员机械到位。",
                station_conclusion="符合要求",
                content_json=common_json,
            )
        else:
            record = WorkRecord(
                record_type=record_type,
                status=WorkRecordStatus.SUBMITTED.value,
                creator_id=user_id,
                project_id=ids["project_id"],
                record_date=datetime(2025, 1, 2).date(),
                created_at=datetime(2025, 1, 2, 11, 0),
                section="地下室底板",
                work_content="钢筋原材见证取样",
                witness_material_name="热轧带肋钢筋",
                witness_material_spec="HRB400E φ20",
                witness_quantity="3",
                witness_unit="组",
                witness_cert_no="CERT-2025-001",
                witness_accept_result="合格",
                content_json={
                    **common_json,
                    "prov_fields": _complete_form_fields("witness_record", {
                        "project_name": "广东省中西医结合应急救治中心项目",
                        "construction_unit": "广州一建建设集团有限公司",
                        "inspector": "陈宇文", "material_name": "热轧带肋钢筋",
                        "material_spec": "HRB400E φ20", "quantity": "3", "unit": "组",
                        "cert_no": "CERT-2025-001", "acceptance_result": "合格",
                    }),
                    "form_field_candidates": {
                        **common_json["form_field_candidates"],
                        "witness_quantity": "3",
                        "witness_unit": "组",
                        "witness_cert_no": "CERT-2025-001",
                        "witness_accept_result": "合格",
                    },
                },
            )
        if record_type == WorkRecordType.STATION.value:
            record.content_json = {
                **common_json,
                "prov_fields": _complete_form_fields("gd_b_212_pangzhan", {
                    "project_name": "广东省中西医结合应急救治中心项目",
                    "construction_unit": "广州一建建设集团有限公司",
                    "inspector": "陈宇文", "section": "西楼A区十层墙柱",
                    "station_process": "混凝土浇筑", "content": "现场按专项方案连续浇筑，人员机械到位。",
                    "conclusion": "符合要求",
                }),
            }
        db.add(record)
        await db.commit()
        return record.id


@pytest.fixture(scope="module")
def ctx():
    ids = asyncio.run(_seed())

    def _auth_header():
        from app.routers.auth import create_access_token
        token = create_access_token(
            {"sub": str(ids["user_id"]), "role": "专业监理工程师",
             "permissions": ["inspection", "document_approve"]}
        )
        return {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        yield {"client": client, "auth_headers": _auth_header, **ids}


def test_forms_list(ctx):
    resp = ctx["client"].get("/api/form-docs/forms", headers=ctx["auth_headers"]())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 25
    codes = [f["form_code"] for g in body["groups"] for f in g["forms"]]
    assert "gd_b_214_xunshi" in codes
    assert "gd_b_212_pangzhan" in codes


def test_sheng_tong_biao_templates_are_blank_source_documents():
    """正式省统表模板必须是空白源表，不能夹带样例照片或已填写记录。"""
    import zipfile

    from app.config import settings
    from app.services.form_registry import FORMS

    folder = settings.knowledge_base_path / "省统表word版"
    checked = 0
    for spec in FORMS:
        if spec.folder != "sheng_tong_biao" or not spec.template:
            continue
        path = folder / spec.template
        assert path.exists(), f"模板不存在：{path}"
        with zipfile.ZipFile(path) as archive:
            media = [name for name in archive.namelist() if name.startswith("word/media/")]
        assert not media, f"正式模板混入图片：{path.name} -> {media}"
        checked += 1
    assert checked >= 20


def test_crane_safety_form_fields_fill_the_matching_word_cells(tmp_path):
    from docx import Document as ReadDocx
    from app.services.form_renderer import render_form

    output = render_form("gdaq_30305_qizhong", {
        "machine_name": "MACHINE-A",
        "machine_number": "SERIAL-B",
        "machine_model": "MODEL-C",
        "location": "LOCATION-D",
        "work_type": "安装",
        "construction_unit": "CONTRACTOR-E",
        "user_unit": "USER-F",
        "install_unit": "INSTALLER-G",
        "filing_status": "有",
        "general_contractor_staff": "STAFF-H",
        "operator_qualification": "OPERATOR-I",
        "warning_measures": "WARNING-J",
        "plan_execution": "PLAN-K",
        "other_checks": "OTHER-L",
    }, tmp_path)
    doc = ReadDocx(str(output))
    rendered = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    rendered += "\n" + "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    for value in ("MACHINE-A", "SERIAL-B", "MODEL-C", "LOCATION-D", "CONTRACTOR-E",
                  "USER-F", "INSTALLER-G", "STAFF-H", "OPERATOR-I", "WARNING-J",
                  "PLAN-K", "OTHER-L"):
        assert value in rendered, value


def test_record_forms(ctx):
    resp = ctx["client"].get(
        f"/api/form-docs/record/{ctx['record_id']}/forms",
        headers=ctx["auth_headers"](),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["record_type"] == "巡视"
    assert body["forms"][0]["form_code"] == "gd_b_214_xunshi"


def test_generate_from_record(ctx):
    resp = ctx["client"].post(
        "/api/form-docs/generate",
        json={"record_id": ctx["record_id"]},
        headers=ctx["auth_headers"](),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["docx_url"] and body["docx_url"].endswith(".docx")
    assert body["form_code"] == "gd_b_214_xunshi"

    exports = Path(__file__).resolve().parent.parent / "exports"
    f = exports / Path(body["docx_url"]).name
    assert f.exists(), "docx 产物应写入 exports/"

    import docx
    d = docx.Document(str(f))
    table_text = " ".join(c.text for t in d.tables for r in t.rows for c in r.cells)
    assert "巡视的工程部位" in table_text
    assert "3#楼二层" in table_text
    assert "发现一处垫块缺失" in table_text


def test_generate_agent_record_keeps_watermark_identity_and_ignores_instruction_as_problem(ctx):
    record_id = asyncio.run(_seed_agent_record(ctx, ctx["user_id"]))
    resp = ctx["client"].post(
        "/api/form-docs/generate",
        json={"record_id": record_id},
        headers=ctx["auth_headers"](),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    export = Path(__file__).resolve().parent.parent / "exports" / Path(body["docx_url"]).name
    import docx
    document = docx.Document(str(export))
    text = "\n".join(p.text for p in document.paragraphs)
    text += "\n" + " ".join(c.text for t in document.tables for r in t.rows for c in r.cells)
    assert "广东省中西医结合应急救治中心项目" in text
    assert "广州一建建设集团有限公司" in text
    assert "陈宇文" not in text
    assert "符合要求" in text
    assert "记录这个工作" not in text
    assert "111" not in text
    tail_text = document.tables[0].cell(3, 0).text
    assert "巡视发现的问题及处理情况：符合要求" in tail_text
    assert "巡视监理人员（签名）" in tail_text
    assert "陈宇文" not in tail_text
    import zipfile
    with zipfile.ZipFile(export) as archive:
        embedded = [name for name in archive.namelist() if name.startswith("word/media/")]
    assert embedded == []


def test_generate_legacy_agent_record_recovers_split_watermark_project_title(ctx):
    record_id = asyncio.run(_seed_legacy_agent_record(ctx, ctx["user_id"]))
    resp = ctx["client"].post(
        "/api/form-docs/generate",
        json={"record_id": record_id},
        headers=ctx["auth_headers"](),
    )
    assert resp.status_code == 200, resp.text
    export = Path(__file__).resolve().parent.parent / "exports" / Path(resp.json()["docx_url"]).name
    import docx
    document = docx.Document(str(export))
    text = "\n".join(p.text for p in document.paragraphs)
    text += "\n" + " ".join(c.text for t in document.tables for r in t.rows for c in r.cells)
    assert "广东省中西医结合应急救治中心项目" in text
    assert "广州一建建设集团有限公司" in text
    assert "陈宇文" not in text
    assert "111" not in text
    assert "记录这个工作" not in text


def test_generate_pangzhan_keeps_units_inspector_and_conclusion(ctx):
    record_id = asyncio.run(_seed_typed_agent_record(ctx, ctx["user_id"], WorkRecordType.STATION.value))
    resp = ctx["client"].post(
        "/api/form-docs/generate",
        json={"record_id": record_id},
        headers=ctx["auth_headers"](),
    )
    assert resp.status_code == 200, resp.text
    export = Path(__file__).resolve().parent.parent / "exports" / Path(resp.json()["docx_url"]).name
    import docx
    document = docx.Document(str(export))
    text = "\n".join(p.text for p in document.paragraphs)
    text += "\n" + " ".join(c.text for t in document.tables for r in t.rows for c in r.cells)
    for expected in (
        "广东省中西医结合应急救治中心项目", "广州一建建设集团有限公司",
            "西楼A区十层墙柱", "混凝土浇筑", "符合要求",
    ):
        assert expected in text


def test_generate_witness_keeps_material_quantity_certificate_and_acceptance(ctx):
    record_id = asyncio.run(_seed_typed_agent_record(ctx, ctx["user_id"], WorkRecordType.WITNESS.value))
    resp = ctx["client"].post(
        "/api/form-docs/generate",
        json={"record_id": record_id},
        headers=ctx["auth_headers"](),
    )
    assert resp.status_code == 200, resp.text
    export = Path(__file__).resolve().parent.parent / "exports" / Path(resp.json()["docx_url"]).name
    import docx
    document = docx.Document(str(export))
    text = "\n".join(p.text for p in document.paragraphs)
    text += "\n" + " ".join(c.text for t in document.tables for r in t.rows for c in r.cells)
    for expected in (
        "热轧带肋钢筋", "HRB400E φ20", "取样数量：3", "计量单位：组", "合格",
        "广东省中西医结合应急救治中心项目",
    ):
        assert expected in text


def test_aggregate_day(ctx):
    from datetime import date as _date
    resp = ctx["client"].post(
        "/api/form-docs/aggregate",
        json={"period": "day", "date": _date.today().isoformat(), "use_llm": False},
        headers=ctx["auth_headers"](),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["docx_url"].endswith(".docx")
    assert body["form_code"] == "gd_b_220_rizhi"
    assert body["summary"]["work_records"] == 1


def test_export_docx(ctx):
    # 先用 generate 得到 doc_id
    resp = ctx["client"].post(
        "/api/form-docs/generate",
        json={"record_id": ctx["record_id"]},
        headers=ctx["auth_headers"](),
    )
    doc_id = resp.json()["doc_id"]
    resp2 = ctx["client"].get(
        f"/api/form-docs/{doc_id}/export?fmt=docx", headers=ctx["auth_headers"]()
    )
    assert resp2.status_code == 200
    assert resp2.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )


def test_registered_forms_have_explicit_versioned_agent_contract_routes():
    from app.services import form_registry, provform_schema, record_field_spec
    from app.services.agent_review_plan import EXECUTABLE_RECORD_FORMS
    from app.services.form_contracts import contract_for

    codes = [spec.form_code for spec in form_registry.FORMS]
    assert len(codes) == len(set(codes)), "表式代码必须唯一"
    for spec in form_registry.FORMS:
        contract = contract_for(spec.form_code)
        assert contract["version"], f"{spec.form_code} 缺少版本化字段合同"
        assert contract["fields"], f"{spec.form_code} 缺少字段定义"
        assert all(
            field.get("canonical") and field.get("label")
            for field in contract["fields"]
            if field.get("kind") != "signoff"
        ), f"{spec.form_code} 存在没有标准字段名或标签的字段"

        if spec.form_code in EXECUTABLE_RECORD_FORMS:
            assert spec.form_code in provform_schema.SCHEMAS or spec.form_code in record_field_spec.FORMS
            assert spec.agent_work_record_type or spec.agent_record_type or spec.agent_business_route
        if spec.source == "aggregate":
            assert spec.form_code in provform_schema.SCHEMAS, f"汇总表 {spec.form_code} 缺字段合同"

    # These tables intentionally do not masquerade as generic record forms:
    # witness uses its own schema, while GDAQ30309 awaits the approved source template.
    assert form_registry.FORM_BY_CODE["witness_record"].agent_work_record_type == "见证"
    assert form_registry.FORM_BY_CODE["gdaq_30309_huifu"].source == "manual"
    assert form_registry.FORM_BY_CODE["gdaq_30309_huifu"].template is None
