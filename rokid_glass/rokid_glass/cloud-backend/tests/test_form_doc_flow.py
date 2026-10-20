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
        )
        db.add(record)
        await db.commit()
        return {"user_id": user.id, "project_id": project.id, "record_id": record.id}


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
