"""监理文书路由 — 8类文书生成/审批/导出，含自动填充、LLM内容生成与PDF导出"""
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from app.db import get_db
from app.config import settings
from app.models.user import User, UserRole, Project
from app.models.document import Document, DocType, DocStatus
from app.models.work_record import WorkRecord, WorkRecordType, WorkRecordStatus
from app.models.hazard import Hazard, HazardStatus
from app.models.rectification import RectificationRecord
from app.routers.auth import get_current_user, require_role, require_project_id
from app.services.doc_generator import generate_pdf, generate_daily_log_pdf
from app.services.period_report_service import generate_supervision_log, doc_type_for
from app.services import provform_schema
from app.services.record_fields import invalidate_document
from app.services.ai_client import ai_client

router = APIRouter()


class DocCreateRequest(BaseModel):
    doc_type: DocType
    title: str
    content: dict = {}
    section: Optional[str] = None
    location: Optional[str] = None
    weather: Optional[str] = None
    attached_images: list[str] = []


class DocUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[dict] = None
    section: Optional[str] = None
    location: Optional[str] = None
    weather: Optional[str] = None
    attached_images: Optional[list[str]] = None


class LLMDocGenerateRequest(BaseModel):
    doc_type: str
    title: str
    section: Optional[str] = None
    location: Optional[str] = None
    hazard_id: Optional[int] = None
    extra_context: str = ""


class DocRejectRequest(BaseModel):
    reason: str = ""


class DailyLogExtras(BaseModel):
    """监理日志补充信息（GD-B-220 schema 对齐：天气/气温由当日填报）"""
    weather: str = ""
    temp_low: str = ""
    temp_high: str = ""


# 需要总监签发的文书类型
NEED_APPROVAL_TYPES = {DocType.RECTIFICATION.value, DocType.MONTHLY.value, DocType.WEEKLY.value}


def _auto_fill_content(doc_type: str, user: User, req: DocCreateRequest) -> dict:
    """根据文书类型自动填充基础字段"""
    now = datetime.now()
    base = {
        "project_name": "",
        "doc_number": f"GD-B-{now.strftime('%Y%m%d%H%M%S')}",
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "supervisor": user.real_name,
        "supervisor_level": getattr(user, "supervisor_level", "") or "",
        "section": req.section or "",
        "location": req.location or "",
        "weather": req.weather or "",
        "attached_images": req.attached_images,
    }
    # 合并用户传入的 content
    base.update(req.content)
    return base


@router.post("/create")
async def create_document(
    req: DocCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建监理文书（自动填充基础字段）"""
    content = _auto_fill_content(req.doc_type.value, user, req)
    doc = Document(
        doc_type=req.doc_type.value,
        title=req.title,
        content=content,
        section=req.section,
        location=req.location,
        weather=req.weather,
        attached_images=req.attached_images,
        project_id=require_project_id(user),
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return {"doc_id": doc.id, "status": doc.status, "doc_number": content.get("doc_number")}


class SchemaDocCreateRequest(BaseModel):
    form_code: str
    title: str = ""
    fields: dict = {}


class SchemaDocUpdateRequest(BaseModel):
    title: Optional[str] = None
    fields: dict = {}


def _validate_schema_fields(form_code: str, fields: dict):
    """服务端校验通用录入页提交的字段（跳过 system 字段），不通过则抛 400。"""
    vres = provform_schema.validate_values(form_code, fields or {})
    if not vres["ok"]:
        msgs = "；".join(e["message"] for e in vres["errors"][:5])
        raise HTTPException(status_code=400, detail=f"表单校验未通过：{msgs}")


@router.post("/create-from-schema")
async def create_document_from_schema(
    req: SchemaDocCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """通用录入页：按 form_code 的省统表 schema 创建文书（一次性覆盖 11 类记录）。

    字段值以 schema-key 提交，后端经 CONTENT_MAP 映射为 canonical content key；
    项目派生 system 字段由项目上下文覆盖，前端只读。
    """
    if req.form_code not in provform_schema.known_form_codes():
        raise HTTPException(status_code=404, detail="未知文书类型")
    schema = provform_schema.get_schema(req.form_code)
    if schema is None:
        raise HTTPException(status_code=400, detail="该文书暂无字段级 schema")
    _validate_schema_fields(req.form_code, req.fields)

    project_id = require_project_id(user)
    project = await db.get(Project, project_id)
    content = provform_schema.build_content_from_fields(req.form_code, req.fields, project)
    doc = Document(
        doc_type=doc_type_for(req.form_code),
        title=req.title or schema["name"],
        content=content,
        form_code=req.form_code,
        project_id=project_id,
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return {"doc_id": doc.id, "form_code": req.form_code, "status": doc.status, "doc_number": content.get("doc_number")}


@router.put("/{doc_id}/from-schema")
async def update_document_from_schema(
    doc_id: int,
    req: SchemaDocUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """通用录入页：更新按 schema 创建的草稿文书。"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    if doc.creator_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(status_code=403, detail="无权编辑该文书")
    if doc.status != DocStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="仅草稿状态可编辑")
    if not doc.form_code:
        raise HTTPException(status_code=400, detail="该文书无关联表式，无法按 schema 更新")
    _validate_schema_fields(doc.form_code, req.fields)

    project = await db.get(Project, project_id)
    new_content = provform_schema.build_content_from_fields(doc.form_code, req.fields, project)
    invalidate_document(doc)
    doc.content = new_content
    if req.title is not None:
        doc.title = req.title
    await db.commit()
    return {"doc_id": doc_id, "status": doc.status}


@router.get("/{doc_id}/form-values")
async def get_document_form_values(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """通用录入页编辑回显：把文书 content（canonical key）反查为 schema-key → 值。"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    code = doc.form_code
    schema = provform_schema.get_schema(code) if code else None
    mp = provform_schema.CONTENT_MAP.get(code, {}) if code else {}
    fields: dict = {}
    if schema:
        for sec in schema["sections"]:
            for f in sec["fields"]:
                ck = mp.get(f["key"], f["key"])
                fields[f["key"]] = (doc.content or {}).get(ck, "")
    return {"form_code": code, "title": doc.title, "fields": fields}


@router.get("/list")
async def list_documents(
    doc_type: Optional[str] = None,
    status: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询文书列表"""
    project_id = require_project_id(user)
    query = select(Document).where(Document.project_id == project_id)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if status:
        query = query.where(Document.status == status)
    query = query.order_by(Document.created_at.desc())
    result = await db.execute(query)
    docs = result.scalars().all()
    return [
        {
            "id": d.id,
            "doc_type": d.doc_type,
            "title": d.title,
            "status": d.status,
            "section": d.section,
            "location": d.location,
            "creator_id": d.creator_id,
            "pdf_path": d.pdf_path,
            "created_at": d.created_at.isoformat(),
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        }
        for d in docs
    ]


@router.get("/{doc_id}")
async def get_document(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取文书详情"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    return {
        "content_revision": doc.content_revision,
        "signed_pdf_path": doc.signed_pdf_path,
        "reviewed_at": doc.reviewed_at.isoformat() if doc.reviewed_at else None,
        "id": doc.id,
        "doc_type": doc.doc_type,
        "doc_number": doc.content.get("doc_number", "") if doc.content else "",
        "title": doc.title,
        "status": doc.status,
        "content": doc.content,
        "section": doc.section,
        "location": doc.location,
        "weather": doc.weather,
        "attached_images": doc.attached_images,
        "pdf_path": doc.pdf_path,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


@router.put("/{doc_id}")
async def update_document(
    doc_id: int,
    req: DocUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新文书内容（语音补充）"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    if doc.creator_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(status_code=403, detail="无权编辑该文书")
    if doc.status != DocStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="仅草稿状态可编辑")

    from app.services.record_fields import invalidate_document
    invalidate_document(doc)
    if req.title is not None:
        doc.title = req.title
    if req.content and "acceptance_result" in req.content:
        from app.services.record_fields import ACCEPTANCE_RESULTS
        if req.content["acceptance_result"] not in ACCEPTANCE_RESULTS:
            raise HTTPException(400, "验收结论须为合格、不合格或待整改")
    if req.content is not None:
        merged = dict(doc.content or {})
        merged.update({k: v for k, v in req.content.items() if k not in ("review_date", "signoffs", "signed_by", "signed_at")})
        merged.pop("review_date", None)
        if any(key in (req.content or {}) for key in ("temperature_high", "temperature_low", "weather_morning", "weather_afternoon", "weather_night")):
            merged["weather_source"] = "人工补填/复核"
            merged["weather_missing"] = [key for key in ("temperature_high", "temperature_low", "weather_morning") if merged.get(key) in (None, "")]
        doc.content = merged
    if req.section is not None:
        doc.section = req.section
    if req.location is not None:
        doc.location = req.location
    if req.weather is not None:
        doc.weather = req.weather
    if req.attached_images is not None:
        doc.attached_images = req.attached_images

    await db.commit()
    return {"doc_id": doc_id, "status": doc.status}


@router.post("/{doc_id}/submit")
async def submit_for_approval(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """提交审批（整改单、月报等需总监签发，其余直接归档）"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    if doc.creator_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(status_code=403, detail="无权提交该文书")

    from app.services.record_fields import validate_document
    validate_document(doc)
    from app.services.document_signoffs import assert_required_signoffs
    await assert_required_signoffs(db, doc)
    content = doc.content or {}
    if doc.doc_type == "监理日志":
        missing = [label for key, label in (("temperature_high", "最高气温"), ("temperature_low", "最低气温"), ("weather_morning", "天气")) if content.get(key) in (None, "")]
        if missing:
            raise HTTPException(400, "请补全日志：" + "、".join(missing))
    if "通知单" in doc.doc_type:
        missing = [label for key, label in (("construction_unit", "施工单位"), ("supervision_org", "项目监理机构"), ("section", "问题部位")) if not content.get(key)]
        if missing:
            raise HTTPException(400, "请补全文书：" + "、".join(missing))
    needs_approval = doc.doc_type in NEED_APPROVAL_TYPES
    doc.status = DocStatus.PENDING_APPROVAL.value if needs_approval else DocStatus.ARCHIVED.value
    await db.commit()
    if needs_approval:
        # Step 2（设计说明 §7.8.4）：投递判据由「全局单值角色 + 上下文 project_id」
        # 改为「本项目成员表岗位集合」——修复跨项目兼职者漏发、
        # 以及"已转场但上下文仍挂本项目"的误发。
        from app.services.permissions import member_user_ids_with_roles

        approver_ids = await member_user_ids_with_roles(
            db, project_id, [UserRole.CHIEF_SUPERVISOR.value]
        )
        approvers = await db.execute(
            select(User).where(
                User.id.in_(approver_ids or [-1]),
                User.is_active.is_(True),
            )
        )
        from app.services.notification import push_to_glasses, push_realtime
        for approver in approvers.scalars().all():
            await push_to_glasses(
                user_id=approver.id,
                title=f"待审批文书：{doc.title}",
                body=f"提交人：{user.real_name}",
                payload={"type": "document_pending", "document_id": doc.id},
            )
            await push_realtime(
                user_id=approver.id,
                event_type="document_pending",
                data={
                    "document_id": doc.id,
                    "title": doc.title,
                    "submitted_by": user.real_name,
                    "doc_type": doc.doc_type,
                },
            )
    return {"doc_id": doc_id, "status": doc.status}


@router.post("/{doc_id}/approve")
async def approve_document(
    doc_id: int,
    user: User = Depends(require_role("总监理工程师")),
    db: AsyncSession = Depends(get_db),
):
    """总监审批通过"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    if doc.status != DocStatus.PENDING_APPROVAL.value:
        raise HTTPException(status_code=400, detail="仅待审批文书可审批")
    from app.services.record_fields import now_local, validate_document
    validate_document(doc)
    from app.services.document_signoffs import assert_required_signoffs
    await assert_required_signoffs(db, doc)
    doc.reviewed_at = now_local()
    doc.reviewed_by = user.id
    doc.content = {**(doc.content or {}), "review_date": doc.reviewed_at.date().isoformat()}
    doc.pdf_path = None
    doc.docx_path = None
    # 已签PDF作为签署时快照保留；审阅日期在新导出中可见。
    doc.status = DocStatus.APPROVED.value
    await db.commit()
    from app.services.notification import push_to_glasses, push_realtime, push_to_wechat
    await push_to_glasses(
        user_id=doc.creator_id,
        title=f"文书已审批：{doc.title}",
        body=f"审批人：{user.real_name}",
        payload={"type": "document_approved", "document_id": doc.id},
    )
    await push_realtime(
        user_id=doc.creator_id,
        event_type="document_approved",
        data={
            "document_id": doc.id,
            "title": doc.title,
            "approved_by": user.real_name,
        },
    )
    # 微信服务通知
    try:
        await push_to_wechat(
            db,
            user_id=doc.creator_id,
            template_key="document_approved",
            data={
                "thing1": {"value": doc.title[:20]},
                "thing2": {"value": user.real_name},
            },
            page=f"pages/document/detail?id={doc.id}",
        )
    except Exception:
        pass
    return {"doc_id": doc_id, "status": doc.status}


@router.post("/{doc_id}/reject")
async def reject_document(
    doc_id: int,
    req: Optional[DocRejectRequest] = None,
    user: User = Depends(require_role("总监理工程师")),
    db: AsyncSession = Depends(get_db),
):
    """总监驳回"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    doc.status = DocStatus.REJECTED.value
    if req and req.reason.strip():
        doc.content = {
            **(doc.content or {}),
            "reject_reason": req.reason.strip(),
        }
    await db.commit()
    from app.services.notification import push_to_glasses, push_realtime, push_to_wechat
    await push_to_glasses(
        user_id=doc.creator_id,
        title=f"文书已驳回：{doc.title}",
        body=f"审批人：{user.real_name}",
        payload={"type": "document_rejected", "document_id": doc.id},
    )
    await push_realtime(
        user_id=doc.creator_id,
        event_type="document_rejected",
        data={
            "document_id": doc.id,
            "title": doc.title,
            "rejected_by": user.real_name,
        },
    )
    # 微信服务通知
    try:
        await push_to_wechat(
            db,
            user_id=doc.creator_id,
            template_key="document_rejected",
            data={
                "thing1": {"value": doc.title[:20]},
                "thing2": {"value": user.real_name},
            },
            page=f"pages/document/detail?id={doc.id}",
        )
    except Exception:
        pass
    return {"doc_id": doc_id, "status": doc.status}


def _merge_doc_images(doc) -> dict:
    """渲染数据 = content 副本 + 合并 doc.attached_images（供填充器在文书末尾附加现场影像）"""
    data = dict(doc.content or {})
    merged = [u for u in (data.get("attached_images") or []) if isinstance(u, str) and u]
    for u in (doc.attached_images or []):
        if isinstance(u, str) and u and u not in merged:
            merged.append(u)
    if merged:
        data["attached_images"] = merged
    return data


@router.get("/{doc_id}/export-pdf")
async def export_pdf(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出PDF"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")

    # 影像兜底：attached_images 为空且 content 带 hazard_id 时，从隐患记录现取（老文书/后补照片场景）
    if not doc.attached_images:
        hazard_id = (doc.content or {}).get("hazard_id")
        if hazard_id:
            try:
                from app.models.hazard import Hazard
                h = await db.get(Hazard, int(hazard_id))
                if h and h.image_path:
                    doc.attached_images = [h.image_path]
            except Exception:
                pass

    # 生成 PDF：优先与 Word 同源（省统表模板渲染 docx → 转换 PDF），失败回退自绘
    # （监理日志 GD-B-220 也统一走模板版式，不再使用自绘日报版式）
    pdf_filename = f"doc_{doc.id}_{doc.doc_type}.pdf"
    pdf_path = settings.export_dir / pdf_filename
    rendered = False
    from app.routers.form_doc import RECORD_TYPE_TO_FORM
    code = doc.form_code or RECORD_TYPE_TO_FORM.get(doc.doc_type.removesuffix("记录"))
    code = code or {
        DocType.RECTIFICATION.value: "gdaq_30308_yinhuan",
        DocType.NOTICE_QUALITY.value: "gd_b_215_tongzhi",
        DocType.SUPERVISION_LOG.value: "gd_b_220_rizhi",
    }.get(doc.doc_type)
    if code:
        try:
            from app.services.docx2pdf import convert_docx_to_pdf
            from app.services.form_renderer import render_form
            docx_out = render_form(code, _merge_doc_images(doc))
            convert_docx_to_pdf(str(docx_out), str(pdf_path))
            docx_out.unlink(missing_ok=True)
            rendered = True
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "省统表 docx→PDF 转换失败，回退自绘 PDF（doc_id=%s, form=%s）", doc.id, code
            )
    if not rendered:
        generate_pdf(doc, str(pdf_path))

    # 更新文书 PDF 路径
    doc.pdf_path = f"/exports/{pdf_filename}"
    await db.commit()

    return {
        "doc_id": doc_id,
        "pdf_url": doc.pdf_path,
        "pdf_path": str(pdf_path),
    }


@router.get("/{doc_id}/export-docx")
async def export_docx(doc_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    if not doc or doc.project_id != require_project_id(user):
        raise HTTPException(404, "文书不存在")
    from app.services.form_renderer import render_form
    from app.routers.form_doc import RECORD_TYPE_TO_FORM
    code = doc.form_code or RECORD_TYPE_TO_FORM.get(doc.doc_type.removesuffix("记录"))
    code = code or {DocType.RECTIFICATION.value: "gdaq_30308_yinhuan", DocType.NOTICE_QUALITY.value: "gd_b_215_tongzhi", DocType.SUPERVISION_LOG.value: "gd_b_220_rizhi"}.get(doc.doc_type)
    if not code:
        raise HTTPException(400, "该文书暂无Word表式，请使用PDF导出")
    output = render_form(code, _merge_doc_images(doc))
    doc.docx_path = f"/exports/{output.name}"
    await db.commit()
    return {"doc_id": doc.id, "docx_url": doc.docx_path}


@router.get("/{doc_id}/download-docx")
async def download_docx(doc_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    if not doc or doc.project_id != require_project_id(user):
        raise HTTPException(404, "文书不存在")
    from app.services.signature_service import _resolve_file_path
    path = _resolve_file_path(doc.docx_path)
    if not path or not path.exists():
        await export_docx(doc_id, user, db)
        path = _resolve_file_path(doc.docx_path)
    if not path or not path.exists():
        raise HTTPException(404, "Word文书不存在")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"{doc.title}.docx")


@router.get("/{doc_id}/download-pdf")
async def download_pdf(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载PDF文件"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    if not (doc.signed_pdf_path or doc.pdf_path):
        raise HTTPException(status_code=400, detail="PDF尚未生成，请先导出")

    pdf_file = Path((doc.signed_pdf_path or doc.pdf_path).replace("/exports/", str(settings.export_dir) + "/"))
    if not pdf_file.exists():
        raise HTTPException(status_code=404, detail="PDF文件不存在")

    return FileResponse(
        path=str(pdf_file),
        media_type="application/pdf",
        filename=f"{doc.title}.pdf",
    )


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除文书（仅创建人或具备 document_approve 权限者），并清理关联文件"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    if doc.creator_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(status_code=403, detail="无权删除该文书")

    from app.models.document_signoff import DocumentSignoff
    if (await db.execute(select(DocumentSignoff.id).where(DocumentSignoff.document_id == doc.id).limit(1))).first():
        raise HTTPException(409, "文书有签认履历，请保留归档，不可删除")

    # 清理关联文件（PDF/DOCX/签章件/附件图）
    paths = [doc.pdf_path, doc.docx_path, doc.signed_pdf_path]
    if isinstance(doc.attached_images, list):
        paths += doc.attached_images
    for path in paths:
        if not path or not isinstance(path, str):
            continue
        try:
            if path.startswith("/exports/"):
                (settings.export_dir / path[len("/exports/"):]).unlink(missing_ok=True)
            elif path.startswith("/uploads/"):
                (settings.upload_dir / path[len("/uploads/"):]).unlink(missing_ok=True)
        except OSError:
            pass

    await db.delete(doc)
    await db.commit()
    return {"detail": "文书已删除", "doc_id": doc_id}


@router.post("/generate-with-llm")
async def generate_with_llm(
    req: LLMDocGenerateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """LLM 生成监理文书内容并创建文书

    根据文书类型、隐患信息、巡检信息等，调用 LLM 自动生成文书正文。
    """
    # 准备上下文信息
    hazard_info = None
    if req.hazard_id:
        from app.models.hazard import Hazard
        project_id = require_project_id(user)
        result = await db.execute(
            select(Hazard).where(
                Hazard.id == req.hazard_id,
                Hazard.project_id == project_id,
            )
        )
        hazard = result.scalar_one_or_none()
        if hazard:
            hazard_info = {
                "top1_risk": hazard.top1_risk,
                "risk_category": hazard.risk_category,
                "risk_status": hazard.risk_status,
                "evidence": hazard.evidence,
                "section": hazard.section,
                "location": hazard.location,
            }

    inspection_info = {
        "section": req.section,
        "location": req.location,
        "supervisor": user.real_name,
    }

    # 调用 LLM 生成文书内容
    try:
        llm_content = await ai_client.generate_document_content(
            doc_type=req.doc_type,
            hazard_info=hazard_info,
            inspection_info=inspection_info,
            extra_context=req.extra_context,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM生成失败: {str(e)}")

    # 自动填充基础字段
    now = datetime.now()
    content = {
        "project_name": "",
        "doc_number": f"GD-B-{now.strftime('%Y%m%d%H%M%S')}",
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "supervisor": user.real_name,
        "supervisor_level": user.supervisor_level or "",
        "section": req.section or "",
        "location": req.location or "",
        "summary": llm_content,
        "hazard_id": req.hazard_id,
    }

    # 创建文书
    doc = Document(
        doc_type=req.doc_type,
        title=req.title,
        content=content,
        section=req.section,
        location=req.location,
        project_id=require_project_id(user),
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return {
        "doc_id": doc.id,
        "status": doc.status,
        "doc_number": content["doc_number"],
        "llm_content": llm_content,
        "source": "llm",
    }


# ---- 监理日志自动生成 ----

@router.post("/generate-daily-log")
async def generate_daily_log(
    date: str = Query(..., description="日期，格式 YYYY-MM-DD"),
    extras: Optional[DailyLogExtras] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """根据指定日期自动生成监理日志报告（手动触发，每次新建一份）。

    聚合当日工作记录、安全隐患、整改记录，按 GD-B-220 版式生成并出 PDF。
    实现位于 period_report_service —— 与「每日 08:00 自动生成」共用同一套
    聚合口径，这里只是把落款人固定为当前登录用户、且允许重复生成。
    """
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，需 YYYY-MM-DD")

    project_id = require_project_id(user)
    ex = extras or DailyLogExtras()
    return await generate_supervision_log(
        db,
        project_id,
        target_date,
        supervisor=user,
        extras={
            "weather": ex.weather,
            "temp_low": ex.temp_low,
            "temp_high": ex.temp_high,
        },
        force=True,
    )
