"""省统表文书路由。

- GET  /api/form-docs/forms           可生成的表式清单（按分类）
- GET  /api/form-docs/record/{id}/forms  某条工作记录可生成的表式
- POST /api/form-docs/generate         把一条工作记录渲染为省统表 docx 文书
- POST /api/form-docs/aggregate        按 日/周/月 汇总生成 监理日志/周报/月报
- POST /api/form-docs/{doc_id}/export  导出 docx / pdf
"""
from __future__ import annotations

import asyncio
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
from app.models.miniprogram import MediaRecord, MediaType
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
from app.services.form_renderer import docx_to_pdf, render_form
from app.services.project_archive import sync_document_to_project_archive
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
    "见证": "witness_record",      # 见证取样记录：独立业务 schema，不借用材料台账
    "起重机械安拆检查": "gdaq_30305_qizhong",
}
# 见证类缺少独立模板 → 允许指定其他表式；默认给出可选项
RECORD_TYPE_EXTRA_FORMS = {
    "巡视": ["gd_b_214_xunshi", "gdaq_30306_weida", "gd_b_215_tongzhi"],
    "验收": ["acceptance_record"],
    "见证": ["witness_record"],
    # GDAQ30305 has a dedicated equipment-inspection contract and must not be
    # generated from a generic station WorkRecord until its business route is wired.
    "旁站": ["gd_b_212_pangzhan"],
    "起重机械安拆检查": ["gdaq_30305_qizhong"],
}


class GenerateFromRecordRequest(BaseModel):
    record_id: int
    form_code: Optional[str] = None   # 不传则按记录类型给默认
    doc_number: Optional[str] = None
    remark: Optional[str] = None


class AggregateRequest(BaseModel):
    period: Literal["day", "week", "month"]
    date: Optional[str] = None        # day: YYYY-MM-DD；week: 周内任一天；month: YYYY-MM
    form_code: Optional[str] = None   # day 可传 anquan_jianli_rizhi 生成安全监理日志
    use_llm: bool = True              # 汇总内容是否走 LLM 润色（失败自动降级为规则汇总）
    # D02：用户当场补充、系统取不到的字段（天气 / 上级质监站检查 / 其他有关事项）。
    # 键用表字段 key 或 canonical（如 7_12、supervision_check），服务端负责映射到版式槽位。
    extra_fields: Optional[dict] = None


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


async def _record_attach_photos(db: AsyncSession, record: WorkRecord) -> list:
    """Only attach media that belong to this record's confirmed evidence set.

    Agent-created records can be amended later. In that case the mutable photo
    array may contain images added for another event. ``content_json.media_ids``
    is the immutable evidence snapshot for the record and therefore wins.
    """
    cj = record.content_json or {}
    agent_snapshot = cj.get("source") == "agent_review_plan"
    media_ids = [
        int(value) for value in (cj.get("media_ids") or [])
        if isinstance(value, (int, str)) and str(value).isdigit()
    ]
    if agent_snapshot:
        # An empty snapshot is authoritative too. ``inspection_photos`` is a
        # mutable convenience array and may later contain photos from another
        # event; it must never leak into an Agent-created record's form.
        if not media_ids:
            return []
        rows = (await db.execute(select(MediaRecord).where(
            MediaRecord.id.in_(media_ids),
            MediaRecord.business_type == "work_record",
            MediaRecord.business_id == record.id,
            MediaRecord.media_type == MediaType.IMAGE.value,
        ))).scalars().all()
        by_id = {row.id: row for row in rows}
        return [
            by_id[media_id].processed_path or by_id[media_id].original_path
            for media_id in media_ids if media_id in by_id
        ]
    photos = list(record.inspection_photos or [])
    if not photos and record.record_type == "见证":
        photos = list(record.witness_photos or [])
    return photos


def _record_extracted_fields(record: WorkRecord) -> dict:
    """Flatten source-aware fields saved by the agent execution layer."""
    cj = record.content_json if isinstance(record.content_json, dict) else {}
    out: dict = {}
    sources = (
        cj.get("form_context") if isinstance(cj.get("form_context"), dict) else {},
        cj.get("form_field_candidates") if isinstance(cj.get("form_field_candidates"), dict) else {},
        cj.get("field_resolutions") if isinstance(cj.get("field_resolutions"), dict) else {},
        cj.get("confirmed_fields") if isinstance(cj.get("confirmed_fields"), dict) else {},
    )
    for source in sources:
        for key, value in source.items():
            if isinstance(value, dict) and "value" in value:
                value = value.get("value")
            if value not in (None, "") and not isinstance(value, (dict, list)):
                out[str(key)] = value
    for legacy, canonical in {
        "project": "project_name", "contractor": "construction_unit",
        "person": "inspector", "work_area": "section",
    }.items():
        if out.get(legacy) not in (None, "") and out.get(canonical) in (None, ""):
            out[canonical] = out[legacy]
    for key, value in _legacy_watermark_fields(cj).items():
        if not str(out.get(key) or "").strip():
            out[key] = value
    return out


def _legacy_watermark_fields(content: dict) -> dict[str, str]:
    """Recover watermark fields from records created before canonical aliases.

    Older Agent records stored the raw OCR transcript and watermark fields but
    did not persist ``project_name``. Re-parsing that immutable evidence keeps
    regeneration correct without replacing it with the logged-in system
    project. New records take the normal structured path above.
    """
    aliases = {
        "project_name": ("project_name", "project", "engineering_name"),
        "owner_unit": ("owner_unit", "owner_unit_name"),
        "construction_unit": ("construction_unit", "contractor", "contractor_unit"),
        "supervision_unit": ("supervision_unit", "supervision_unit_name"),
        "inspector": ("inspector", "person", "checker", "inspection_person"),
        "section": ("section", "work_area"),
        "location": ("location", "address"),
        "weather": ("weather",),
    }
    recovered: dict[str, str] = {}

    def absorb(fields: dict) -> None:
        for canonical, source_keys in aliases.items():
            if recovered.get(canonical):
                continue
            value = next(
                (str(fields.get(key) or "").strip() for key in source_keys
                 if str(fields.get(key) or "").strip()),
                "",
            )
            if value:
                recovered[canonical] = value

    for key in ("extracted_watermark_fields", "form_field_candidates"):
        fields = content.get(key)
        if not isinstance(fields, dict):
            continue
        absorb(fields)

    evidence_fields = content.get("evidence_watermark_fields")
    if isinstance(evidence_fields, list):
        for item in evidence_fields:
            fields = item.get("fields") if isinstance(item, dict) else None
            if isinstance(fields, dict):
                absorb(fields)

    from app.services.watermark_ocr import parse_watermark_fields
    for note in content.get("evidence_notes") or []:
        text = str(note or "")
        marker = "OCR文字："
        if marker not in text:
            continue
        transcript = text.split(marker, 1)[1].split(" · ", 1)[0]
        lines = [part.strip() for part in transcript.split("；") if part.strip()]
        absorb(parse_watermark_fields(lines))
    return recovered


def _content_from_record(
    record: WorkRecord,
    user: User,
    project: Project | None = None,
) -> dict:
    """工作记录 → 表单内容字段。"""
    from app.services.record_fields import (
        business_date,
        business_record_fields,
        is_instruction_only,
        problem_text,
    )
    now = record.created_at or datetime.now()
    extracted = _record_extracted_fields(record)
    business = business_record_fields(record, project)
    explicit_problems = problem_text(record)
    legacy_remark = str(record.inspection_remark or "").strip()
    if not explicit_problems and legacy_remark and not is_instruction_only(legacy_remark):
        explicit_problems = legacy_remark
    work_content = str(record.work_content or "").strip()
    if is_instruction_only(work_content):
        work_content = ""
    extracted_content = str(
        extracted.get("work_content") or extracted.get("content") or extracted.get("activity") or ""
    ).strip()
    if is_instruction_only(extracted_content):
        extracted_content = ""
    base = {
        "project_name": "",
        "doc_number": "",
        "date": business_date(record).isoformat(),
        "time": now.strftime("%Y-%m-%d %H:%M"),
        # Creator identity is not proof of signing. Signature slots are handled
        # by the document signoff workflow, not populated from the account name.
        "supervisor": "",
        "section": record.section or "",
        "location": record.location or "",
        "weather": record.weather or "",
        "remark": record.remark or "",
        "construction_unit": str(business.get("construction_unit") or ""),
        "problems": problem_text(record),
        "handling": record.treatment or "",
        "treatment": record.treatment or "",
        "owner_unit": str(business.get("owner_unit") or extracted.get("owner_unit") or ""),
        "construction_unit": str(business.get("construction_unit") or extracted.get("construction_unit") or ""),
        "supervision_unit": str(business.get("supervision_unit") or extracted.get("supervision_unit") or ""),
        "supervision_org": str(
            business.get("supervision_org")
            or business.get("supervision_unit")
            or extracted.get("supervision_org")
            or extracted.get("supervision_unit")
            or ""
        ),
        "inspector": str(
            business.get("inspector")
            or extracted.get("inspector")
            or ""
        ),
    }
    rtype = record.record_type
    if rtype == "巡视":
        base.update({
            "section": record.section or "",
            "content": work_content or extracted_content or f"{record.section or '现场'}巡视检查",
            # 问题栏只接受业务问题。没有记录问题时保持空白，不能把“记录”
            # 指令或系统推断伪装成“符合要求”的现场结论。
            "problems": explicit_problems or str(extracted.get("problems") or ""),
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
            "content": record.station_content or work_content or extracted_content or "",
            "conclusion": record.station_conclusion or "",
            "problems": problem_text(record),
            "supervision_status": record.station_supervision_status or "",
        })
    elif rtype == "验收":
        acceptance_content = (
            str(extracted.get("acceptance_content") or "").strip()
            or str((record.content_json or {}).get("acceptance_content") or "").strip()
            or record.work_content or ""
        )
        base.update({
            "section": record.section or "",
            "acceptance_content": acceptance_content,
            "content": acceptance_content,
            "conclusion": f"验收结果：{record.acceptance_result or ''}",
            "record": acceptance_content,
            "acceptance_result": record.acceptance_result or "",
            "acceptance_type": record.acceptance_type if hasattr(record, "acceptance_type") else str(extracted.get("acceptance_type") or (record.content_json or {}).get("acceptance_type") or ""),
            "acceptance_participants": record.acceptance_participants or "",
            "basis": record.acceptance_basis or str(extracted.get("basis") or ""),
        })
    elif rtype == "见证":
        cj = record.content_json or {}
        material_name = record.witness_material_name or str(extracted.get("material_name") or extracted.get("witness_material_name") or "")
        material_spec = record.witness_material_spec or str(extracted.get("material_spec") or extracted.get("witness_material_spec") or "")
        base.update({
            "section": record.section or "",
            "material_name": material_name,
            "material_spec": material_spec,
            "witness_quantity": record.witness_quantity or str(extracted.get("quantity") or extracted.get("witness_quantity") or ""),
            "witness_unit": record.witness_unit or str(extracted.get("unit") or extracted.get("witness_unit") or ""),
            "witness_cert_no": record.witness_cert_no or str(extracted.get("witness_cert_no") or extracted.get("cert_no") or ""),
            "witness_accept_result": record.witness_accept_result or str(extracted.get("witness_accept_result") or extracted.get("acceptance_result") or ""),
            "sampler": str(extracted.get("inspector") or base.get("inspector") or ""),
            "sample_date": business_date(record).isoformat(),
            "acceptance_date": business_date(record).isoformat(),
            "manufacturer": cj.get("manufacturer") or "",
            "batch_no": cj.get("batch_no") or "",
            "content": (
                f"见证取样材料：{material_name} {material_spec}"
            ).strip(),
            "time": (record.created_at or now).strftime("%Y-%m-%d %H:%M"),
        })
    else:
        base["content"] = work_content or extracted_content or ""
    # Watermark/selected fields fill columns with no dedicated WorkRecord
    # column, while explicit record values remain authoritative.
    for key, value in extracted.items():
        text = str(value if value is not None else "").strip()
        if not text or is_instruction_only(text):
            continue
        if key in {"project", "contractor", "person", "work_area"}:
            continue
        if not str(base.get(key) or "").strip():
            base[key] = text
    base["project_name"] = str(
        business.get("business_project_name") or business.get("project_name") or ""
    ).strip()
    base["supervisor"] = ""
    base["system_project_name"] = str(business.get("system_project_name") or "")
    base["project_mismatch"] = bool(business.get("project_mismatch"))
    base["business_field_conflicts"] = business.get("field_conflicts") or {}
    # Explicit edits are the latest record values. Empty edits are retained as
    # tombstones so an old OCR/Agent candidate cannot silently refill a field.
    field_edits = (record.content_json or {}).get("record_field_edits")
    edited_keys: set[str] = set()
    if isinstance(field_edits, dict):
        for key, raw in field_edits.items():
            value = raw.get("value") if isinstance(raw, dict) else raw
            base[str(key)] = str(value if value is not None else "").strip()
            edited_keys.add(str(key))

    # 字段表追问收集到的栏目只补空项；记录列和后续人工修改始终优先。
    prov = (record.content_json or {}).get("prov_fields")
    if isinstance(prov, dict):
        for key, value in prov.items():
            text = str(value if value is not None else "").strip()
            if text and key not in edited_keys and not str(base.get(key) or "").strip():
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
        saved_code = (record.content_json or {}).get("form_code") if isinstance(record.content_json, dict) else None
        code = saved_code or RECORD_TYPE_TO_FORM.get(record.record_type)
        spec = FORM_BY_CODE.get(code) if code else None
        if spec is None:
            raise HTTPException(400, f"{record.record_type}记录暂无可用的默认表式，请指定 form_code")

    # The Agent and the direct "record -> form" button must share one required-
    # field gate.  Without this, an old or partially filled record can silently
    # produce a polished Word/PDF with blank mandatory cells.
    from app.services.agent_form_requirements import resolve_record_form

    requirements = resolve_record_form(record, project, spec.form_code)
    if (
        requirements.get("missing_labels")
        or requirements.get("conflict_labels")
        or requirements.get("invalid_fields")
    ):
        blockers = (
            list(requirements.get("missing_labels") or [])
            + list(requirements.get("conflict_labels") or [])
            + [
                f"{item.get('label') or item.get('canonical')}（{item.get('message') or '格式无效'}）"
                for item in requirements.get("invalid_fields") or []
            ]
        )
        raise HTTPException(
            422,
            "记录尚未满足表式必填项，请回到 Agent 工作记录草稿补充："
            + "、".join(dict.fromkeys(blockers)),
        )

    record_content = record.content_json if isinstance(record.content_json, dict) else {}
    saved_contract = record_content.get("form_contract_version")
    if record_content.get("source") == "agent_review_plan" and saved_contract:
        if record_content.get("form_code") == spec.form_code and saved_contract != requirements.get("contract_version"):
            raise HTTPException(
                409,
                "该记录依据的表式规则已更新，请回到 Agent 重新核对字段后再生成文书",
            )

    author = await db.get(User, record.creator_id) or user
    content = _content_from_record(record, author, project)
    content["form_contract_version"] = requirements.get("contract_version", "")
    from app.services.record_fields import project_fields
    for key, value in project_fields(project).items():
        if not str(content.get(key) or "").strip():
            content[key] = value
    content["doc_number"] = doc_number or (f"{spec.form_number}-{record.id:05d}")
    attached_photos = await _record_attach_photos(db, record)
    content["attached_images"] = attached_photos

    # 危大巡视（GDAQ30306）：栏位比普通巡视多（风力/专业分包/危大类型/逐项检查结果）
    if spec.form_code == "gdaq_30306_weida":
        cj = record.content_json or {}
        content.update({
            "hazard_type": cj.get("hazard_type") or cj.get("hazard_name") or content.get("hazard_type") or "",
            "wind": cj.get("wind") or cj.get("wind_force") or content.get("wind") or "",
            "subcontract_unit": cj.get("subcontract_unit") or content.get("subcontract_unit") or "",
            "chief_supervisor": cj.get("chief_supervisor") or content.get("chief_supervisor") or "",
            "checklist_results": cj.get("checklist_results") or content.get("checklist_results") or {},
            "start_time": record.inspection_start_time.isoformat(sep=" ", timespec="minutes")
            if record.inspection_start_time else content.get("start_time", ""),
            "end_time": record.inspection_end_time.isoformat(sep=" ", timespec="minutes")
            if record.inspection_end_time else content.get("end_time", ""),
            "problems": content.get("problems") or record.inspection_remark or "",
        })

    existing = await db.get(Document, record.generated_doc_id) if record.generated_doc_id else None
    if existing:
        for key in ("signing", "offline_signoffs"):
            if (existing.content or {}).get(key) is not None:
                content[key] = (existing.content or {})[key]
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
        docx_file = await asyncio.to_thread(render_form, spec.form_code, content)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"模板文件缺失：{exc}") from exc
    except Exception as exc:
        logger.exception("渲染省统表失败")
        raise HTTPException(500, f"省统表渲染失败：{exc}") from exc

    if existing:
        from app.services.record_fields import invalidate_document
        invalidate_document(existing)
        existing.content = content
        existing.attached_images = attached_photos
        existing.docx_path = f"/exports/{docx_file.name}"
        existing.archive_sync_status = "pending"
        # A PDF is always a conversion of the current DOCX. Never keep a PDF
        # generated from the previous revision of the same document.
        existing.pdf_path = None
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
        attached_images=attached_photos,
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

    # Archive synchronization is post-commit so an archive outage cannot roll back
    # the generated business document. Re-generation updates the stable archive row.
    await sync_document_to_project_archive(db, doc, user)
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
        result = await generate_aggregate_doc(
            db,
            project_id,
            req.period,
            req.date,
            form_code=req.form_code,
            use_llm=req.use_llm,
            supervisor=user,
            force=True,
            extra_fields=req.extra_fields,
        )
        await db.commit()
        if isinstance(result, dict) and result.get("doc_id"):
            doc = await db.get(Document, int(result["doc_id"]))
            if doc:
                await sync_document_to_project_archive(db, doc, user)
                await db.commit()
        return result
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
    if fmt == "pdf":
        if doc.pdf_path:
            cached_pdf = settings.export_dir / doc.pdf_path.replace("/exports/", "")
            if cached_pdf.exists():
                await sync_document_to_project_archive(db, doc, user)
                await db.commit()
                return FileResponse(
                    str(cached_pdf), media_type="application/pdf",
                    filename=f"{doc.title}.pdf",
                )
        if not doc.docx_path:
            raise HTTPException(400, "文书尚未生成 docx")
        docx_path = settings.export_dir / doc.docx_path.replace("/exports/", "")
        if not docx_path.exists():
            raise HTTPException(404, "Word 文件不存在")
        pdf = await asyncio.to_thread(docx_to_pdf, docx_path)
        if pdf is None:
            raise HTTPException(501, "服务器未安装 Word/LibreOffice，暂不支持 docx→pdf")
        doc.pdf_path = f"/exports/{pdf.name}"
        await db.commit()
        await db.refresh(doc)
        await sync_document_to_project_archive(db, doc, user)
        await db.commit()
        return FileResponse(str(pdf), media_type="application/pdf", filename=f"{doc.title}.pdf")
    if not doc.docx_path:
        raise HTTPException(400, "文书尚未生成 docx")
    path = settings.export_dir / doc.docx_path.replace("/exports/", "")
    if not path.exists():
        raise HTTPException(404, "Word 文件不存在")
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(str(path), media_type=media, filename=f"{doc.title}.docx")
