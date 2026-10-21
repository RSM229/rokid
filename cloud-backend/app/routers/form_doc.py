"""省统表文书路由。

- GET  /api/form-docs/forms           可生成的表式清单（按分类）
- GET  /api/form-docs/record/{id}/forms  某条工作记录可生成的表式
- POST /api/form-docs/generate         把一条工作记录渲染为省统表 docx 文书
- POST /api/form-docs/aggregate        按 日/周/月 汇总生成 监理日志/周报/月报
- POST /api/form-docs/{doc_id}/export  导出 docx / pdf
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.document import DocStatus, DocType, Document
from app.models.hazard import Hazard
from app.models.rectification import RectificationRecord
from app.models.user import Project, User
from app.models.work_record import WorkRecord
from app.routers.auth import (
    get_current_user,
    require_permission,
    require_project_id,
    project_scope_id,
)
from app.services.form_registry import FORMS, FORM_BY_CODE, form_groups
from app.services.form_renderer import render_form
from app.services.period_report_service import (
    doc_type_for as _doc_type_for,
    generate_aggregate_doc,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# 记录类型 -> 默认表式 code
RECORD_TYPE_TO_FORM = {
    "巡视": "gd_b_214_xunshi",
    "旁站": "gd_b_212_pangzhan",
    "验收": "acceptance_record",   # 独立业务验收记录
    "见证": "gd_b_219_cailiao",    # 见证取样记录：材料台账版式（无独立见证模板时）
}
# 见证类缺少独立模板 → 允许指定其他表式；默认给出可选项
RECORD_TYPE_EXTRA_FORMS = {
    "巡视": ["gd_b_214_xunshi", "gdaq_30306_weida", "gd_b_215_tongzhi"],
    "验收": ["acceptance_record"],
    "见证": ["gd_b_219_cailiao", "gd_b_213_pingxing"],
    "旁站": ["gd_b_212_pangzhan", "gdaq_30305_qizhong"],
}


class GenerateFromRecordRequest(BaseModel):
    record_id: int
    form_code: Optional[str] = None   # 不传则按记录类型给默认
    doc_number: Optional[str] = None
    remark: Optional[str] = None


class AggregateRequest(BaseModel):
    period: Literal["day", "week", "month"]
    date: Optional[str] = None        # day: YYYY-MM-DD；week: 周内任一天；month: YYYY-MM
    form_code: Optional[str] = None
    use_llm: bool = True              # 汇总内容是否走 LLM 润色（失败自动降级为规则汇总）


@router.get("/forms")
async def list_forms(user: User = Depends(get_current_user)):
    """省统表清单（前端用于“记录→文书”选择）。"""
    return {
        "groups": form_groups(),
        "total": len(FORMS),
    }


@router.get("/record/{record_id}/forms")
async def list_record_forms(
    record_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """给定工作记录可生成的表式。"""
    scope = project_scope_id(user, all_projects)
    stmt = select(WorkRecord).where(WorkRecord.id == record_id)
    if scope is not None:
        stmt = stmt.where(WorkRecord.project_id == scope)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "工作记录不存在")
    codes = RECORD_TYPE_EXTRA_FORMS.get(record.record_type, [])
    forms = [FORM_BY_CODE[c] for c in codes if c in FORM_BY_CODE]
    return {
        "record": {
            "id": record.id,
            "record_type": record.record_type,
            "section": record.section,
            "location": record.location,
            "status": record.status,
        },
        "forms": [
            {
                "form_code": f.form_code,
                "name": f.name,
                "form_number": f.form_number,
                "has_template": bool(f.template),
            }
            for f in forms
        ],
    }


def _record_attach_photos(record: WorkRecord) -> list:
    """文书附加影像：只取现场照片；见证记录现场照片为空时回落其见证照片（该记录唯一的拍照来源）。"""
    photos = list(record.inspection_photos or [])
    if not photos and record.record_type == "见证":
        photos = list(record.witness_photos or [])
    return photos


def _content_from_record(record: WorkRecord, user: User) -> dict:
    """工作记录 → 表单内容字段。"""
    from app.services.record_fields import business_date, problem_text
    now = record.created_at or datetime.now()
    base = {
        "project_name": "",
        "doc_number": "",
        "date": business_date(record).isoformat(),
        "time": now.strftime("%Y-%m-%d %H:%M"),
        "supervisor": user.real_name,
        "section": record.section or "",
        "location": record.location or "",
        "weather": record.weather or "",
        "remark": record.remark or "",
        "construction_unit": "",
        "problems": problem_text(record),
        "handling": record.treatment or "",
        "treatment": record.treatment or "",
    }
    rtype = record.record_type
    if rtype == "巡视":
        base.update({
            "section": record.section or "",
            "content": record.work_content or record.inspection_remark or "",
            # 房建 GD-B-214 的问题栏：有独立问题/处理用问题，否则回落现场描述（内容不再塞进预印清单）
            "problems": problem_text(record) or record.inspection_remark or record.work_content or "",
            "worker_count": record.worker_count,
            "start_time": record.inspection_start_time.isoformat(sep=" ", timespec="minutes") if record.inspection_start_time else "",
            "end_time": record.inspection_end_time.isoformat(sep=" ", timespec="minutes") if record.inspection_end_time else "",
            "time": record.inspection_start_time.isoformat(sep=" ", timespec="minutes") if record.inspection_start_time else business_date(record).isoformat(),
        })
    elif rtype == "旁站":
        start = record.station_start_time
        end = record.station_end_time
        base.update({
            "section": "；".join(x for x in [record.station_location or record.section or "", record.station_process or ""] if x),
            "station_part": record.station_location or record.section or "",
            "station_process": record.station_process or "",
            # JSON 列不能存 datetime，统表 time 字段用 HH:MM
            "start_time": start.isoformat(sep=" ", timespec="minutes") if start else "",
            "end_time": end.isoformat(sep=" ", timespec="minutes") if end else "",
            "content": record.station_content or record.work_content or "",
            "conclusion": record.station_conclusion or "",
            "problems": problem_text(record),
            "supervision_status": record.station_supervision_status or "",
        })
    elif rtype == "验收":
        base.update({
            "section": record.section or "",
            "acceptance_content": (record.content_json or {}).get("acceptance_content")
            or record.work_content or "",
            "content": (record.content_json or {}).get("acceptance_content")
            or record.work_content or "",
            "conclusion": f"验收结果：{record.acceptance_result or ''}",
            "record": (record.content_json or {}).get("acceptance_content") or "",
            "acceptance_result": record.acceptance_result or "",
            "acceptance_type": (record.content_json or {}).get("acceptance_type") or "",
            "acceptance_participants": record.acceptance_participants or "",
            "basis": record.acceptance_basis or "",
        })
    elif rtype == "见证":
        cj = record.content_json or {}
        base.update({
            "section": record.section or "",
            "material_name": record.witness_material_name or "",
            "material_spec": record.witness_material_spec or "",
            "manufacturer": cj.get("manufacturer") or "",
            "batch_no": cj.get("batch_no") or "",
            "content": (
                f"见证取样材料：{record.witness_material_name or ''} "
                f"{record.witness_material_spec or ''}"
            ).strip(),
            "time": (record.created_at or now).strftime("%Y-%m-%d %H:%M"),
        })
    else:
        base["content"] = record.work_content or ""
    # 字段表追问收集到的栏目（content_json.prov_fields，键=字段表 canonical）
    # 文书模板有对应栏位就带出来；已有值的以记录本体为准，不覆盖
    prov = (record.content_json or {}).get("prov_fields")
    if isinstance(prov, dict):
        confirmed_plan = (record.content_json or {}).get("source") == "agent_review_plan"
        for key, value in prov.items():
            text = str(value if value is not None else "").strip()
            if text and (confirmed_plan or not str(base.get(key) or "").strip()):
                base[str(key)] = text
    return base


def _record_doc_title(record: WorkRecord, spec, content: dict) -> str:
    """工作记录生成文书的标题（在「工作文书」列表里展示的就是它）。

    几类记录借用了别的统表版式（验收借平行检验、见证借材料台账），
    标题仍应按记录本身命名，否则列表里看不懂。
    """
    if record.record_type == "见证":
        return f"见证取样记录_{record.witness_material_name or record.id}"
    if record.record_type == "验收":
        return f"验收记录_{(record.content_json or {}).get('acceptance_type') or record.id}"
    return f"{spec.name}_{record.record_type}_{record.id}"


async def generate_form_from_record(
    db: AsyncSession,
    user: User,
    project: Project,
    record: WorkRecord,
    form_code: str | None = None,
    doc_number: str | None = None,
) -> tuple[Document, object, dict]:
    """按记录渲染省统表 docx 并落库（复用于生成 / 上传 / 转发）。"""
    # 确定表式
    if form_code:
        spec = FORM_BY_CODE.get(form_code)
        if spec is None:
            raise HTTPException(400, f"未知表式 {form_code}")
    else:
        code = RECORD_TYPE_TO_FORM.get(record.record_type)
        spec = FORM_BY_CODE.get(code) if code else None
        if spec is None:
            raise HTTPException(400, f"{record.record_type}记录暂无可用的默认表式，请指定 form_code")

    author = await db.get(User, record.creator_id) or user
    content = _content_from_record(record, author)
    from app.services.record_fields import project_fields
    content.update(project_fields(project))
    content["doc_number"] = doc_number or (f"{spec.form_number}-{record.id:05d}")

    # 危大巡视（GDAQ30306）：栏位比普通巡视多（风力/专业分包/危大类型/逐项检查结果）
    if spec.form_code == "gdaq_30306_weida":
        cj = record.content_json or {}
        content.update({
            "hazard_type": cj.get("hazard_type") or cj.get("hazard_name") or "",
            "wind": cj.get("wind") or cj.get("wind_force") or "",
            "subcontract_unit": cj.get("subcontract_unit") or "",
            "chief_supervisor": cj.get("chief_supervisor") or "",
            "checklist_results": cj.get("checklist_results") or {},
            "start_time": record.inspection_start_time.isoformat(sep=" ", timespec="minutes")
            if record.inspection_start_time else content.get("start_time", ""),
            "end_time": record.inspection_end_time.isoformat(sep=" ", timespec="minutes")
            if record.inspection_end_time else content.get("end_time", ""),
            "problems": content.get("problems") or record.inspection_remark or "",
        })

    existing = await db.get(Document, record.generated_doc_id) if record.generated_doc_id else None
    old_content = {k: v for k, v in (existing.content or {}).items() if k != "review_date"} if existing else None
    if existing and old_content == content and existing.form_code == spec.form_code and existing.docx_path:
        # 单纯查看/再次生成同一内容不能清掉已完成的签认。
        return existing, spec, existing.content
    if existing and existing.status != DocStatus.DRAFT.value:
        raise HTTPException(409, "已提交文书不能覆盖生成，请先驳回")
    if existing and existing.creator_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(403, "无权修订该文书")

    # 渲染 docx
    try:
        docx_file = render_form(spec.form_code, content)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"模板文件缺失：{exc}") from exc
    except Exception as exc:
        logger.exception("渲染省统表失败")
        raise HTTPException(500, f"省统表渲染失败：{exc}") from exc

    if existing:
        from app.services.record_fields import invalidate_document
        invalidate_document(existing)
        existing.content = content
        existing.attached_images = _record_attach_photos(record)
        existing.docx_path = f"/exports/{docx_file.name}"
        await db.flush()
        return existing, spec, content
    doc = Document(
        doc_type=_doc_type_for(spec.form_code, record.record_type),
        title=_record_doc_title(record, spec, content),
        content=content,
        form_code=spec.form_code,
        section=record.section,
        location=record.location,
        project_id=project.id if project else record.project_id,
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
        attached_images=_record_attach_photos(record),
        pdf_path=None,
        docx_path=f"/exports/{docx_file.name}",
    )
    db.add(doc)
    await db.flush()
    record.generated_doc_id = doc.id
    return doc, spec, content


@router.post("/generate")
async def generate_doc_from_record(
    req: GenerateFromRecordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把工作记录渲染成省统表文书并落库（docx 产物）。"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == req.record_id,
            WorkRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "工作记录不存在")
    if record.creator_id != user.id and not user.has_permission("task_manage"):
        raise HTTPException(403, "无权基于该记录生成文书")

    project = await db.get(Project, project_id)
    doc, spec, content = await generate_form_from_record(
        db, user, project, record, req.form_code, req.doc_number
    )
    await db.commit()

    return {
        "doc_id": doc.id,
        "title": doc.title,
        "form_code": spec.form_code,
        "form_name": spec.name,
        "doc_number": content["doc_number"],
        "docx_url": doc.docx_path,
        "status": doc.status,
    }


# ---------------------------------------------------------------------------
# 日/周/月聚合生成
# ---------------------------------------------------------------------------

@router.post("/aggregate")
async def aggregate_generate(
    req: AggregateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按日/周/月聚合工作记录、隐患与整改，AI 润色后生成监理日志/周报/月报 docx。

    实现位于 period_report_service，与「每日 08:00 自动生成」共用同一套聚合口径，
    避免两条路径各写一份而日后漂移。手动触发保持既有行为（每次新建一份）；
    系统自动生成走 force=False，同周期已有则不重复建。
    """
    project_id = require_project_id(user)
    try:
        return await generate_aggregate_doc(
            db,
            project_id,
            req.period,
            req.date,
            form_code=req.form_code,
            use_llm=req.use_llm,
            supervisor=user,
            force=True,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc


@router.get("/{doc_id}/export")
async def export_form_doc(
    doc_id: int,
    fmt: Literal["docx", "pdf"] = Query("docx"),
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载 docx（或转换 pdf）。"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Document).where(Document.id == doc_id)
    if scope is not None:
        stmt = stmt.where(Document.project_id == scope)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "文书不存在")
    file_url = doc.docx_path if fmt == "docx" else doc.pdf_path
    if not file_url:
        if not doc.docx_path:
            raise HTTPException(400, "文书尚未生成 docx")
        file_url = doc.docx_path
    rel = file_url.replace("/exports/", "")
    path = settings.export_dir / rel
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    if fmt == "pdf" and not doc.pdf_path:
        from app.services.form_renderer import docx_to_pdf
        pdf = docx_to_pdf(path)
        if pdf is None:
            raise HTTPException(501, "服务器未安装 Word/LibreOffice，暂不支持 docx→pdf")
        doc.pdf_path = f"/exports/{pdf.name}"
        await db.commit()
        return FileResponse(str(pdf), media_type="application/pdf", filename=f"{doc.title}.pdf")
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(str(path), media_type=media, filename=f"{doc.title}.docx")
