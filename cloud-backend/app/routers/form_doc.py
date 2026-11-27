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
import re
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
from app.services.form_routing import (
    extra_record_forms,
    is_municipal,
    municipal_weida_code,
    record_is_weida,
)
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
    project = await db.get(Project, record.project_id) if record.project_id else None
    codes = extra_record_forms(record.record_type, project, is_weida=record_is_weida(record))
    forms = []
    for c in codes:
        spec = FORM_BY_CODE.get(c)
        if spec:
            forms.append(spec)
    # 市政危大记录：按 hazard_type 额外提供 2.5-2~7 具体表（如「基坑」→ 2.5-2）
    if is_municipal(project) and record_is_weida(record):
        cj = record.content_json or {}
        hazard = str(cj.get("hazard_type") or cj.get("hazard_name") or "").strip()
        if hazard:
            code = municipal_weida_code(hazard)
            spec = FORM_BY_CODE.get(code)
            if spec and not any(f.form_code == code for f in forms):
                forms.append(spec)
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


def _strip_pending_markers(text: str) -> str:
    """文书渲染前清掉内部「待核」占位标记。

    记录里带"待核"是给用户看的提示（提示现场该核什么），但正式文书不能出现
    这种内部字样。这里把"（待核）"/"：待核"统一换成待填空位"____"，
    既不虚构"符合要求"结论，也不留刺眼的"待核"（2026-11-27 用户反馈）。
    """
    s = str(text or "")
    s = re.sub(r"（待核）", "____", s)
    s = re.sub(r"：待核", "：____", s)
    s = re.sub(r"\(待核\)", "____", s)
    return s


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
    # 巡视人兜底：水印往往只印「监理」这种岗位名，没有姓名可写；记录由该监理
    # 人员本人创建，此时用创建人姓名落栏，避免文书出现空栏（2026-11-18 用户截图）。
    author_name = str(
        getattr(user, "real_name", "") or getattr(user, "username", "") or ""
    ).strip()
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
            # 巡视人：业务字段/提取值都没有时落到记录人姓名（水印只印岗位名的情况）
            "inspector": base["inspector"] or author_name,
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
            # 市政监-9 第五格「监理情况」读 supervision 键（房建旁站用
            # station_supervision_status，市政填充器读 supervision）。
            "content": _strip_pending_markers(record.station_content or work_content or extracted_content or ""),
            "conclusion": record.station_conclusion or "",
            "problems": problem_text(record),
            "supervision": _strip_pending_markers(record.station_supervision_status or record.station_content or ""),
            "supervision_status": _strip_pending_markers(record.station_supervision_status or ""),
        })
    elif rtype == "验收":
        cj = record.content_json if isinstance(record.content_json, dict) else {}
        prov = cj.get("prov_fields") if isinstance(cj.get("prov_fields"), dict) else {}
        acceptance_content = (
            str(extracted.get("acceptance_content") or "").strip()
            or str(cj.get("acceptance_content") or "").strip()
            or record.work_content or ""
        )

        def _conclusion(key: str) -> str:
            # 三层兜底：提取候选 → prov_fields（表式必填校验同一数据源）→ content_json 顶层。
            # 2026-11-18 用户反馈「生成文书里没有自检/验收结论」：此前只读 extracted，
            # Agent 归集出的结论落在 prov_fields/顶层，渲染时被丢掉。
            return (
                str(extracted.get(key) or "").strip()
                or str(prov.get(key) or "").strip()
                or str(cj.get(key) or "").strip()
            )

        self_check_conclusion = _conclusion("self_check_conclusion")
        supervision_conclusion = _conclusion("supervision_conclusion")
        base.update({
            "section": record.section or "",
            "acceptance_content": acceptance_content,
            "content": acceptance_content,
            # 监理验收结论优先用归集出的完整结论；没有才退回人工确认的枚举结果。
            "conclusion": supervision_conclusion or (
                f"验收结果：{record.acceptance_result}" if record.acceptance_result else ""
            ),
            "record": acceptance_content,
            "acceptance_result": record.acceptance_result or "",
            "acceptance_type": record.acceptance_type if hasattr(record, "acceptance_type") else str(extracted.get("acceptance_type") or (record.content_json or {}).get("acceptance_type") or ""),
            "acceptance_participants": record.acceptance_participants or "",
            "basis": record.acceptance_basis or str(extracted.get("basis") or ""),
            "self_check_conclusion": self_check_conclusion,
            "supervision_conclusion": supervision_conclusion,
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

    命名规则：工作记录_<现场业务日期>，让列表一眼看出是何时的记录，
    不再用「表式_类型_序号」这种机器味命名。
    """
    return f"工作记录_{_record_business_date(record)}"


def _record_business_date(record: WorkRecord) -> str:
    """取工作记录的业务时间：优先现场业务日期，其次巡视开始时间，再其次创建时间。"""
    if record.record_date is not None:
        if isinstance(record.record_date, str):
            return record.record_date
        return record.record_date.strftime("%Y-%m-%d")
    if getattr(record, "inspection_start_time", None) is not None:
        return record.inspection_start_time.strftime("%Y-%m-%d")
    return record.created_at.strftime("%Y-%m-%d")


async def generate_form_from_record(
    db: AsyncSession,
    user: User,
    project: Project,
    record: WorkRecord,
    form_code: str | None = None,
    doc_number: str | None = None,
) -> tuple[Document, object, dict]:
    """按记录渲染省统表 docx 并落库（复用于生成 / 上传 / 转发）。"""
    # 确定表式：不用 RECORD_TYPE_TO_FORM 硬编码，统一走工程类型路由。
    # 显式 form_code 优先；否则用记录里保存的 code；再按记录类型路由（市政换表）。
    if form_code:
        spec = FORM_BY_CODE.get(form_code)
        if spec is None:
            raise HTTPException(400, f"未知表式 {form_code}")
    else:
        from app.services.form_routing import effective_record_form_code, record_is_weida

        saved_code = (
            (record.content_json or {}).get("form_code")
            if isinstance(record.content_json, dict) else None
        )
        code = effective_record_form_code(
            saved_code, record.record_type, project=project, record=record,
        )
        spec = FORM_BY_CODE.get(code) if code else None
        if spec is None:
            raise HTTPException(400, f"{record.record_type}记录暂无可用的默认表式，请指定 form_code")

    # The Agent and the direct "record -> form" button must share one required-
    # field gate.  Without this, an old or partially filled record can silently
    # produce a polished Word/PDF with blank mandatory cells.
    from app.services.agent_form_requirements import resolve_record_form

    requirements = resolve_record_form(record, project, spec.form_code)

    # 🔴 2026-11-27：旁站「19 类工序范本预填」豁免。
    # 建记录时已按工序把"施工情况要点 + 监理情况检查项"预填进
    # station_content / station_supervision_status（每项标"待核"由现场补充）。
    # 这些检查项本质覆盖了范本里 worker_count/material_info/qa_onsite/content
    # 等细项的核查意图，但它们不是独立列、resolve_record_form 从记录列取值时
    # 判缺失 → 生成文书被 422 拦住（实测"缺监理情况"）。
    # 既然预填文本已承载这些检查项，就把它们从必填阻断中剔除，允许生成文书；
    # 文书渲染层仍会把 station_supervision_status 写进"监理情况"栏（form_doc.py:367）。
    if (
        record.record_type == "旁站"
        and str(record.station_supervision_status or "").strip()
    ):
        # 范本已带出整套检查项（每项"待核"），相当于该填什么都已列出；
        # 现场具体数值由用户在记录详情里补，不应阻断文书生成。
        requirements["missing_labels"] = []
        requirements["missing_fields"] = []
        requirements["conflict_labels"] = []
        requirements["invalid_fields"] = []

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

    # 危大巡视（GDAQ30306 / 市政 AQSZ-J-2.5-1~7）：栏位比普通巡视多
    # （风力/专业分包/危大类型/逐项检查结果）。市政危大表同一套字段补全。
    if spec.form_code == "gdaq_30306_weida" or spec.form_code.startswith("aqsz_2_5_"):
        cj = record.content_json or {}
        content.update({
            "hazard_type": cj.get("hazard_type") or cj.get("hazard_name") or content.get("hazard_type") or "",
            "wind": cj.get("wind") or cj.get("wind_force") or content.get("wind") or "",
            "subcontract_unit": cj.get("subcontract_unit") or content.get("subcontract_unit") or "",
            "chief_supervisor": cj.get("chief_supervisor") or content.get("chief_supervisor") or "",
            "checklist_results": cj.get("checklist_results") or content.get("checklist_results") or {},
            "checklist": cj.get("checklist") or content.get("checklist") or [],
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
# 批量生成（N5/N6：同类合并一张表，异类分表）
# ---------------------------------------------------------------------------

class GenerateBatchRequest(BaseModel):
    record_ids: list[int]
    form_code: Optional[str] = None   # 不传则逐条自动匹配


def _merge_record_fields(records: list[WorkRecord], content: dict) -> dict:
    """N5：多条同类记录合并进一张表的内容字段。

    原型口径：合并一张市政监-9，栏内分多条，时间起止齐全。做法——
    - 多值栏（部位/内容/问题/结论）用「（1）（2）…」分条拼接；
    - 时段栏取并集（最早开始、最晚结束），每条起止写进分条文本；
    - 单值栏（天气/施工区域/参建单位等）取第一条非空值。
    """
    def join_multi(key: str) -> str:
        parts = []
        for i, rec in enumerate(records, 1):
            val = str(content_of(rec).get(key) or "").strip()
            if val:
                parts.append(f"（{i}）{val}")
        return "\n".join(parts)

    def content_of(rec: WorkRecord) -> dict:
        # 单条记录 → 该表式字段（轻量重算，不走完整 _content_from_record，
        # 避免 N 次照片/权限附件查询；这里只取文本字段做合并展示）。
        cj = rec.content_json if isinstance(rec.content_json, dict) else {}
        return {
            "content": str(rec.work_content or cj.get("work_content") or cj.get("content") or "").strip(),
            "station_part": str(rec.station_location or rec.section or "").strip(),
            "station_process": str(rec.station_process or "").strip(),
            "problems": str(rec.inspection_remark or cj.get("problems") or "").strip(),
            "conclusion": str(rec.station_conclusion or cj.get("conclusion") or "").strip(),
            "start_time": (rec.inspection_start_time or rec.station_start_time or None),
            "end_time": (rec.inspection_end_time or rec.station_end_time or None),
            "worker_count": rec.worker_count,
            "section": str(rec.section or "").strip(),
        }

    contents = [content_of(r) for r in records]
    rtype = records[0].record_type if records else ""

    # 时段并集：最早开始 → 最晚结束
    starts = [c["start_time"] for c in contents if c["start_time"]]
    ends = [c["end_time"] for c in contents if c["end_time"]]
    start_str = min(starts).isoformat(sep=" ", timespec="minutes") if starts else ""
    end_str = max(ends).isoformat(sep=" ", timespec="minutes") if ends else ""

    # 分条文本：每条「部位 · 工序 · 起止时间 · 内容」
    entries = []
    for i, (rec, c) in enumerate(zip(records, contents), 1):
        seg = []
        place = c["station_part"] or c["section"]
        if place and rtype == "旁站":
            seg.append(place)
        proc = c["station_process"]
        if proc and rtype == "旁站":
            seg.append(proc)
        if c["start_time"]:
            t0 = c["start_time"].strftime("%H:%M")
            t1 = c["end_time"].strftime("%H:%M") if c["end_time"] else ""
            seg.append(f"{t0}{'-' + t1 if t1 else ''}")
        if c["content"]:
            seg.append(c["content"])
        entries.append(f"（{i}）" + "，".join(x for x in seg if x))

    return {
        "entries": entries,
        "start_time": start_str,
        "end_time": end_str,
        "worker_count": sum(c["worker_count"] or 0 for c in contents) or None,
        "problems": join_multi("problems") if len(records) > 1 else (contents[0].get("problems") or ""),
        "problems_parts": [c["problems"] for c in contents if c["problems"]],
    }


@router.post("/generate-batch")
async def generate_docs_batch(
    req: GenerateBatchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """N5/N6：勾选多条记录 → 同表式合并一份文书，异表式分表生成。

    原型口径：
    - N5：勾 2 条同部位旁站 → 合并一张市政监-9，栏内分两条，时间起止齐全；
    - N6：勾旁站 + 危大巡视 → 分出两张表（危大表标需审批，旁站表确认归档）。

    实现分叉：
    - 单条记录或组内仅 1 条 → 走现有 ``generate_form_from_record``（保持
      照片附件、必填闸、既有覆盖语义完全不变）；
    - 组内 ≥ 2 条 → 合并渲染：多值栏分条拼接、时段取并集、单值栏取首个
      非空值，渲染为一张统表 docx。
    """
    if not req.record_ids:
        raise HTTPException(400, "请至少勾选一条记录")
    project_id = require_project_id(user)
    rows = (await db.execute(
        select(WorkRecord).where(
            WorkRecord.id.in_(req.record_ids),
            WorkRecord.project_id == project_id,
        )
    )).scalars().all()
    if not rows:
        raise HTTPException(404, "工作记录不存在")
    by_id = {r.id: r for r in rows}
    records = [by_id[i] for i in req.record_ids if i in by_id]
    # 权限：本人记录或 task_manage（与单条生成同闸）
    for r in records:
        if r.creator_id != user.id and not user.has_permission("task_manage"):
            raise HTTPException(403, "无权基于该记录生成文书")

    project = await db.get(Project, project_id)

    # 分组：form_code 相同 → 同组（N5 合并）；不同 → 分表（N6）
    groups: dict[str, list[WorkRecord]] = {}
    for rec in records:
        if req.form_code:
            code = req.form_code
        else:
            from app.services.form_routing import effective_record_form_code
            saved = (rec.content_json or {}).get("form_code") if isinstance(rec.content_json, dict) else None
            code = effective_record_form_code(saved, rec.record_type, project=project, record=rec) or ""
        groups.setdefault(code, []).append(rec)

    results = []
    errors = []
    for code, recs in groups.items():
        if not code:
            errors.append(f"{recs[0].record_type}记录无可匹配表式，请手选")
            continue
        try:
            if len(recs) == 1:
                doc, spec, content = await generate_form_from_record(
                    db, user, project, recs[0], code
                )
                await db.commit()
                await sync_document_to_project_archive(db, doc, user)
                await db.commit()
                results.append({
                    "doc_id": doc.id,
                    "title": doc.title,
                    "form_code": spec.form_code,
                    "form_name": spec.name,
                    "record_ids": [recs[0].id],
                    "record_type": recs[0].record_type,
                    "status": doc.status,
                    "merged": False,
                })
            else:
                doc_payload = await _generate_merged_doc(
                    db, user, project, code, recs
                )
                results.append(doc_payload)
                await db.commit()
        except HTTPException as exc:
            await db.rollback()
            errors.append(f"[{code}] {exc.detail}")
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.exception("批量生成失败（表式 %s）", code)
            errors.append(f"[{code}] {str(exc)[:200]}")

    return {"results": results, "errors": errors}


async def _generate_merged_doc(
    db: AsyncSession, user: User, project: Project | None,
    code: str, records: list[WorkRecord],
) -> dict:
    """组内 ≥2 条：合并渲染一张统表（N5 栏内分条 + 时段并集）。"""
    spec = FORM_BY_CODE.get(code)
    if spec is None:
        raise HTTPException(400, f"未知表式 {code}")

    # 必填闸：逐条校验（合并不能掩盖缺字段）
    from app.services.agent_form_requirements import resolve_record_form
    for rec in records:
        requirements = resolve_record_form(rec, project, code)
        if requirements.get("missing_labels") or requirements.get("conflict_labels"):
            blockers = list(requirements.get("missing_labels") or []) + list(requirements.get("conflict_labels") or [])
            raise HTTPException(
                422,
                f"记录 #{rec.id} 缺少必填项：" + "、".join(dict.fromkeys(blockers)),
            )

    merged = _merge_record_fields(records, {})
    author = await db.get(User, records[0].creator_id) or user
    content = _content_from_record(records[0], author, project)
    # 合并字段覆盖
    content["doc_number"] = f"{spec.form_number}-{records[0].id:05d}"
    if merged["start_time"]:
        content["start_time"] = merged["start_time"]
        content["time"] = merged["start_time"]
    if merged["end_time"]:
        content["end_time"] = merged["end_time"]
    content["content"] = "\n".join(merged["entries"])
    if merged["worker_count"]:
        content["worker_count"] = merged["worker_count"]
    if merged["problems"]:
        content["problems"] = merged["problems"]
    # 旁站合并：部位/工序分条
    if records[0].record_type == "旁站":
        places = []
        for i, rec in enumerate(records, 1):
            p = str(rec.station_location or rec.section or "").strip()
            proc = str(rec.station_process or "").strip()
            if p:
                places.append(f"（{i}）{p}" + (f"·{proc}" if proc else ""))
        if places:
            content["station_part"] = "\n".join(places)
        procs = [str(rec.station_process or "").strip() for rec in records if str(rec.station_process or "").strip()]
        if procs:
            content["station_process"] = "；".join(procs)

    # 照片：各条记录附件并集
    photos: list[str] = []
    for rec in records:
        photos.extend(await _record_attach_photos(db, rec))
    content["attached_images"] = photos

    # 既有文书覆盖语义：组内任一条挂了 generated_doc_id 且非草稿 → 拒绝
    existing_ids = {r.generated_doc_id for r in records if r.generated_doc_id}
    for did in existing_ids:
        old = await db.get(Document, did)
        if old and old.status != DocStatus.DRAFT.value:
            raise HTTPException(409, "已提交文书不能覆盖生成，请先驳回")

    try:
        docx_file = await asyncio.to_thread(render_form, code, content)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"模板文件缺失：{exc}") from exc
    except Exception as exc:
        logger.exception("合并渲染省统表失败（%s）", code)
        raise HTTPException(500, f"合并渲染失败：{exc}") from exc

    doc = Document(
        doc_type=_doc_type_for(code, records[0].record_type),
        title=f"工作记录_{_record_business_date(records[0])}（{len(records)}条合并）",
        content=content,
        form_code=code,
        section=records[0].section,
        location=records[0].location,
        project_id=project.id if project else records[0].project_id,
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
        attached_images=photos,
        pdf_path=None,
        docx_path=f"/exports/{docx_file.name}",
    )
    db.add(doc)
    await db.flush()
    # 合并文书与各条记录挂钩（首条写 generated_doc_id，其余记录进 content）
    records[0].generated_doc_id = doc.id
    content["merged_record_ids"] = [r.id for r in records]
    await sync_document_to_project_archive(db, doc, user)

    return {
        "doc_id": doc.id,
        "title": doc.title,
        "form_code": spec.form_code,
        "form_name": spec.name,
        "record_ids": [r.id for r in records],
        "record_type": records[0].record_type,
        "status": doc.status,
        "merged": True,
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
