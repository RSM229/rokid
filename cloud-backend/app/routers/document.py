"""监理文书路由 — 8类文书生成/审批/导出，含自动填充、LLM内容生成与PDF导出"""
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Body, Depends, HTTPException, Query

import asyncio
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from app.db import get_db
from app.config import settings
from app.models.user import User, Project
from app.models.document import Document, DocType, DocStatus
from app.models.work_record import WorkRecord, WorkRecordType, WorkRecordStatus
from app.models.hazard import Hazard, HazardStatus
from app.models.rectification import RectificationRecord
from app.routers.auth import (
    get_current_user,
    require_permission,
    require_project_id,
    project_scope_id,
    can_read_project,
)
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
    form_code: Optional[str] = None


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
# N6/N7（2026-11-18）：危大工程巡视记录按原型口径需总监审批（安全责任文书），
# 加入需签发集合——其余记录型文书（旁站/巡视/验收等）仍提交即归档。
NEED_APPROVAL_TYPES = {
    DocType.RECTIFICATION.value,
    DocType.MONTHLY.value,
    DocType.WEEKLY.value,
    "危大工程巡视记录",
}


def _is_diary_doc(doc: Document) -> bool:
    """监理日记（个人工作记录）：doc_type 或 form_code 任一命中即认定。"""
    return doc.doc_type == DocType.SUPERVISION_DIARY.value or doc.form_code == "jianli_riji"


def _ensure_diary_visible(doc: Document, user: User) -> None:
    """日记可见性守卫（原型「仅个人可见」）：非本人且无审批权限 → 404。

    统一在 get/markdown/download/export 各读口校验，防止同项目他人日记串显。
    用 404 而非 403，避免泄露「这个项目里存在谁的日记」。
    """
    if _is_diary_doc(doc) and doc.creator_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(status_code=404, detail="文书不存在")


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
        form_code=req.form_code,
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
    # 表单里拍照/选图上传的照片地址落到 attached_images，文书详情页「关联影像」
    # 与导出 PDF/docx 的附图都从这里取（否则只在 content['photo'] 里会出现裸 URL）
    attached_images = provform_schema.image_field_urls(req.form_code, req.fields)
    doc = Document(
        doc_type=doc_type_for(req.form_code),
        title=req.title or schema["name"],
        content=content,
        form_code=req.form_code,
        project_id=project_id,
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
        attached_images=attached_images,
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
    for key in ("signing", "offline_signoffs"):
        if (doc.content or {}).get(key) is not None:
            new_content[key] = (doc.content or {})[key]
    invalidate_document(doc)
    # 旧照片地址（重拍/换图时从 attached_images 剔除旧图，避免残留）
    old_content = doc.content or {}
    old_schema = provform_schema.get_schema(doc.form_code) if doc.form_code else None
    old_map = provform_schema.CONTENT_MAP.get(doc.form_code, {}) if doc.form_code else {}
    old_images: list[str] = []
    if old_schema:
        for sec in old_schema.get("sections") or []:
            for f in sec.get("fields") or []:
                if f.get("type") != "image":
                    continue
                val = str(old_content.get(old_map.get(f["key"], f["key"])) or "").strip()
                if val.startswith("/uploads/") or val.startswith("http"):
                    old_images.append(val)
    doc.content = new_content
    # 表单重传照片时同步更新影像列表；无照片字段的表不动既有影像（保留聊天补的图）
    form_images = provform_schema.image_field_urls(doc.form_code, req.fields)
    if form_images or old_images:
        existing = [u for u in (doc.attached_images or [])
                    if isinstance(u, str) and u not in old_images]
        doc.attached_images = list(dict.fromkeys(form_images + existing))
    if req.title is not None:
        doc.title = req.title
    await db.commit()
    await _sync_project_archive_copy(db, doc, user)
    return {"doc_id": doc_id, "status": doc.status}


@router.get("/{doc_id}/form-values")
async def get_document_form_values(
    doc_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """通用录入页编辑回显：把文书 content（canonical key）反查为 schema-key → 值。"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Document).where(Document.id == doc_id)
    if scope is not None:
        stmt = stmt.where(Document.project_id == scope)
    result = await db.execute(stmt)
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
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询文书列表"""
    scope = project_scope_id(user, all_projects)
    query = select(Document)
    if scope is not None:
        query = query.where(Document.project_id == scope)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if status:
        query = query.where(Document.status == status)
    query = query.order_by(Document.created_at.desc())
    result = await db.execute(query)
    docs = result.scalars().all()
    # 日记「仅个人可见」：列表层直接滤掉他人日记（有审批权限者可看全部）。
    # 原实现把过滤责任推给前端（只回 creator_id），前端一旦漏滤就跨人串显——
    # 2026-11-18 收紧为后端强制。
    docs = [
        d for d in docs
        if not _is_diary_doc(d)
        or d.creator_id == user.id
        or user.has_permission("document_approve")
    ]
    creator_ids = {d.creator_id for d in docs if d.creator_id}
    # 日记是按作者查自己的汇总（原型「仅个人可见」）：列表带回 creator_id，
    # 调用方据此过滤，再显示 creator_name，避免前端拿 id 反查。
    user_names: dict[int, str] = {}
    if creator_ids:
        rows = await db.execute(
            select(User.id, User.real_name, User.username).where(User.id.in_(creator_ids))
        )
        user_names = {uid: (real or uname) for uid, real, uname in rows.all()}
    project_names: dict[int, str] = {}
    if scope is None and docs:
        rows = await db.execute(
            select(Project.id, Project.name).where(
                Project.id.in_({d.project_id for d in docs})
            )
        )
        project_names = {pid: name for pid, name in rows.all()}
    return [
        {
            "id": d.id,
            "project_id": d.project_id,
            "project_name": project_names.get(d.project_id),
            "doc_type": d.doc_type,
            "title": d.title,
            "status": d.status,
            "section": d.section,
            "location": d.location,
            "creator_id": d.creator_id,
            "creator_name": user_names.get(d.creator_id) if d.creator_id else None,
            "pdf_path": d.pdf_path,
            "created_at": d.created_at.isoformat(),
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        }
        for d in docs
    ]


@router.get("/{doc_id}")
async def get_document(
    doc_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取文书详情"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Document).where(Document.id == doc_id)
    if scope is not None:
        stmt = stmt.where(Document.project_id == scope)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    _ensure_diary_visible(doc, user)
    return {
        "content_revision": doc.content_revision,
        "signed_pdf_path": doc.signed_pdf_path,
        "archive_sync_status": doc.archive_sync_status,
        "archive_sync_error": doc.archive_sync_error,
        "can_retry_archive": (
            doc.creator_id == user.id or user.has_permission("document_approve")
        ),
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
        "markdown_path": doc.markdown_path,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


@router.post("/{doc_id}/archive-sync")
async def retry_project_archive_sync(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    doc = (await db.execute(select(Document).where(
        Document.id == doc_id, Document.project_id == project_id
    ))).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    if doc.creator_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(status_code=403, detail="无权重试资料库归档")
    if not (doc.docx_path or doc.pdf_path or doc.markdown_path or doc.signed_pdf_path or doc.form_code):
        raise HTTPException(status_code=409, detail="文书尚无可归档的正式文件")
    await _sync_project_archive_copy(db, doc, user)
    return {
        "status": doc.archive_sync_status,
        "error": doc.archive_sync_error,
        "revision": doc.archive_synced_revision,
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
        workflow_keys = {
            "review_date", "signoffs", "signed_by", "signed_at", "signing",
            "offline_signoffs", "document_signoffs",
        }
        merged.update({k: v for k, v in req.content.items() if k not in workflow_keys})
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
    await _sync_project_archive_copy(db, doc, user)
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
    approver_rows = []
    if needs_approval:
        from app.services.permissions import project_members_with_permission
        from app.services.collaboration import upsert_action_items
        approver_rows = await project_members_with_permission(
            db, project_id, "document_approve"
        )
        await upsert_action_items(
            db,
            project_id=project_id,
            sender_id=user.id,
            recipient_ids=[approver.id for _, approver in approver_rows],
            object_type="document",
            object_id=doc.id,
            action_type="approve",
            title=f"待审批文书：{doc.title}",
            description=doc.doc_type,
            source_prefix="workflow",
            retire_missing=True,
        )
    await db.commit()
    if needs_approval:
        from app.services.notification import push_to_glasses, push_realtime
        for _, approver in approver_rows:
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
    user: User = Depends(require_permission("document_approve")),
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
    doc.archive_sync_status = "pending"
    doc.archive_sync_error = "审批信息已更新，等待重新生成并同步正式文件"
    # 已签PDF作为签署时快照保留；审阅日期在新导出中可见。
    doc.status = DocStatus.APPROVED.value
    from app.services.collaboration import complete_action_items
    await complete_action_items(
        db, object_type="document", object_id=doc.id, action_type="approve",
        actor_id=user.id, detail="审批通过",
    )
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
            page=f"pages/documents/detail?id={doc.id}",
        )
    except Exception:
        pass
    return {"doc_id": doc_id, "status": doc.status}


@router.post("/{doc_id}/reject")
async def reject_document(
    doc_id: int,
    req: Optional[DocRejectRequest] = None,
    user: User = Depends(require_permission("document_approve")),
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
    if doc.status != DocStatus.PENDING_APPROVAL.value:
        raise HTTPException(status_code=400, detail="仅待审批文书可驳回")
    doc.status = DocStatus.REJECTED.value
    if req and req.reason.strip():
        doc.content = {
            **(doc.content or {}),
            "reject_reason": req.reason.strip(),
        }
    from app.services.collaboration import complete_action_items
    await complete_action_items(
        db, object_type="document", object_id=doc.id, action_type="approve",
        actor_id=user.id, detail="审批驳回",
    )
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
            page=f"pages/documents/detail?id={doc.id}",
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


async def _sync_project_archive_copy(db: AsyncSession, doc: Document, user: User) -> None:
    from app.services.project_archive import sync_document_to_project_archive

    await sync_document_to_project_archive(db, doc, user)
    await db.commit()


@router.get("/{doc_id}/export-pdf")
async def export_pdf(
    doc_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出PDF"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Document).where(Document.id == doc_id)
    if scope is not None:
        stmt = stmt.where(Document.project_id == scope)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    _ensure_diary_visible(doc, user)

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

    # Generate the PDF from the exact DOCX exposed by the Word download path.
    pdf_filename = f"doc_{doc.id}_{doc.doc_type}.pdf"
    pdf_path = settings.export_dir / pdf_filename
    rendered = False
    # 纯 Markdown 日记：正文直接排进 PDF（打印/归档需要时按需转换）
    if doc.doc_type == DocType.SUPERVISION_DIARY.value and not (
        doc.signed_pdf_path or doc.attached_images
    ):
        from app.services.markdown_pdf import markdown_to_pdf
        from app.services.period_report_service import build_supervision_log_markdown

        try:
            target_date = datetime.strptime(
                str((doc.content or {}).get("date") or doc.created_at.date().isoformat()),
                "%Y-%m-%d",
            ).date()
        except ValueError:
            target_date = doc.created_at.date()
        markdown_text = build_supervision_log_markdown(
            doc.content or {}, target_date, doc_title=doc.doc_type
        )
        if doc.markdown_path:
            from app.services.signature_service import _resolve_file_path
            md_file = _resolve_file_path(doc.markdown_path)
            if md_file and md_file.exists():
                markdown_text = md_file.read_text(encoding="utf-8")
        await asyncio.to_thread(markdown_to_pdf, markdown_text, str(pdf_path))
        doc.pdf_path = f"/exports/{pdf_filename}"
        await db.commit()
        await _sync_project_archive_copy(db, doc, user)
        return {"doc_id": doc_id, "pdf_url": doc.pdf_path, "pdf_path": str(pdf_path)}
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
            docx_out = None
            if doc.docx_path:
                candidate = settings.export_dir / doc.docx_path.replace("/exports/", "")
                if candidate.exists():
                    docx_out = candidate
            if docx_out is None:
                # LibreOffice/docx 渲染是秒级重活，放线程池，防阻塞事件循环拖垮全部请求
                docx_out = await asyncio.to_thread(render_form, code, _merge_doc_images(doc))
                doc.docx_path = f"/exports/{docx_out.name}"
            await asyncio.to_thread(convert_docx_to_pdf, str(docx_out), str(pdf_path))
            rendered = True
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "省统表 docx→PDF 转换失败，回退自绘 PDF（doc_id=%s, form=%s）", doc.id, code
            )
    if not rendered:
        # A known Word form must never silently fall back to a different
        # hand-drawn PDF layout. The caller can retry once Office/LibreOffice
        # is available, and the DOCX remains downloadable meanwhile.
        if code:
            raise HTTPException(
                status_code=501,
                detail="该 Word 表式暂时无法转换为同版式 PDF，请安装/启动 Microsoft Word 或 LibreOffice 后重试",
            )
        generate_pdf(doc, str(pdf_path))

    # 更新文书 PDF 路径
    doc.pdf_path = f"/exports/{pdf_filename}"
    await db.commit()
    await _sync_project_archive_copy(db, doc, user)

    return {
        "doc_id": doc_id,
        "pdf_url": doc.pdf_path,
        "pdf_path": str(pdf_path),
    }


@router.get("/{doc_id}/export-docx")
async def export_docx(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
):
    doc = await db.get(Document, doc_id)
    if not doc or not can_read_project(doc.project_id, user, all_projects):
        raise HTTPException(404, "文书不存在")
    _ensure_diary_visible(doc, user)
    # 监理日记正式产物是 Markdown，不再提供省统表 Word 导出
    if doc.doc_type == DocType.SUPERVISION_DIARY.value:
        raise HTTPException(400, "监理日记以 Markdown 为正式产物，请使用 PDF 导出")
    from app.services.form_renderer import render_form
    from app.routers.form_doc import RECORD_TYPE_TO_FORM
    code = doc.form_code or RECORD_TYPE_TO_FORM.get(doc.doc_type.removesuffix("记录"))
    code = code or {DocType.RECTIFICATION.value: "gdaq_30308_yinhuan", DocType.NOTICE_QUALITY.value: "gd_b_215_tongzhi", DocType.SUPERVISION_LOG.value: "gd_b_220_rizhi"}.get(doc.doc_type)
    if not code:
        raise HTTPException(400, "该文书暂无Word表式，请使用PDF导出")
    output = await asyncio.to_thread(render_form, code, _merge_doc_images(doc))
    doc.docx_path = f"/exports/{output.name}"
    doc.pdf_path = None
    await db.commit()
    await _sync_project_archive_copy(db, doc, user)
    return {"doc_id": doc.id, "docx_url": doc.docx_path}


@router.get("/{doc_id}/download-docx")
async def download_docx(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
):
    doc = await db.get(Document, doc_id)
    if not doc or not can_read_project(doc.project_id, user, all_projects):
        raise HTTPException(404, "文书不存在")
    from app.services.signature_service import _resolve_file_path
    path = _resolve_file_path(doc.docx_path)
    if not path or not path.exists():
        await export_docx(doc_id, user, db, all_projects)
        path = _resolve_file_path(doc.docx_path)
    if not path or not path.exists():
        raise HTTPException(404, "Word文书不存在")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"{doc.title}.docx")


@router.get("/{doc_id}/download-pdf")
async def download_pdf(
    doc_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载PDF文件"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Document).where(Document.id == doc_id)
    if scope is not None:
        stmt = stmt.where(Document.project_id == scope)
    result = await db.execute(stmt)
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


@router.get("/{doc_id}/markdown")
async def get_document_markdown(
    doc_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """读取 Markdown 正文（监理日志等项目级产物不再出 docx/PDF）。"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Document).where(Document.id == doc_id)
    if scope is not None:
        stmt = stmt.where(Document.project_id == scope)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    _ensure_diary_visible(doc, user)
    if not doc.markdown_path:
        raise HTTPException(status_code=404, detail="该文书没有 Markdown 正文")
    from app.services.signature_service import _resolve_file_path
    path = _resolve_file_path(doc.markdown_path)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Markdown 文件不存在")
    return {
        "doc_id": doc.id,
        "title": doc.title,
        "markdown_path": doc.markdown_path,
        "markdown": path.read_text(encoding="utf-8"),
    }


@router.get("/{doc_id}/download-markdown")
async def download_markdown(
    doc_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载 Markdown 文件（监理日志正式产物）。"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Document).where(Document.id == doc_id)
    if scope is not None:
        stmt = stmt.where(Document.project_id == scope)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    _ensure_diary_visible(doc, user)
    if not doc.markdown_path:
        raise HTTPException(status_code=404, detail="该文书没有 Markdown 正文")
    from app.services.signature_service import _resolve_file_path
    path = _resolve_file_path(doc.markdown_path)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Markdown 文件不存在")
    return FileResponse(
        path=str(path),
        media_type="text/markdown; charset=utf-8",
        filename=f"{doc.title}.md",
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

    from app.services.collaboration import cancel_action_items

    await cancel_action_items(
        db,
        object_type="document",
        object_id=doc.id,
        actor_id=user.id,
        detail="业务文书已删除，归档副本按安全策略保留",
    )
    # Keep the project archive copy after the source business document is removed.
    from app.services.project_archive import detach_project_archive_copy

    await detach_project_archive_copy(db, doc.id)

    # 清理关联文件（PDF/DOCX/Markdown/签章件/附件图）
    paths = [doc.pdf_path, doc.docx_path, doc.markdown_path, doc.signed_pdf_path]
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


@router.post("/generate-daily-diary")
async def generate_daily_diary(
    date: str = Query(..., description="日期，格式 YYYY-MM-DD"),
    author_id: Optional[int] = Query(None, description="日记归属人 ID，缺省为当前登录用户"),
    payload: Optional[dict] = Body(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """生成指定作者、指定日期的监理日记（个人维度，PDF，GD-B-220 版式）。

    只聚合该作者当日的工作记录 / 隐患 / 平行检查 / 会议 / 发文，落款为作者本人，
    自动归档到资料库「项目文件夹/工作记录与表单/监理日记」。
    ``author_id`` 缺省为当前登录用户；传入他人 ID 时需是该项目的在读成员
    （跨项目读取受权限网关约束），落款仍用作者本人。
    """
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，需 YYYY-MM-DD")

    project_id = require_project_id(user)
    # 2026-11-18 收紧：日记是「个人工作记录」，只能给本人生成。
    # 原先允许给同项目他人代生成——A 可用 B 的名义造日记（creator/落款都是 B）且 B 不知情。
    # 总监确需查看他人日记走 list（有 document_approve 权限可见）。
    if author_id is not None and author_id != user.id:
        raise HTTPException(status_code=404, detail="日记只能为本人生成")
    target_author = author_id if author_id is not None else user.id
    record_ids = None
    ex = DailyLogExtras()
    if payload and isinstance(payload, dict):
        if isinstance(payload.get("record_ids"), list):
            try:
                record_ids = [int(x) for x in payload["record_ids"] if x is not None]
            except (TypeError, ValueError):
                record_ids = None
        # 兼容旧 body 形态：extras 字段可能直接在顶层
        try:
            ex = DailyLogExtras(
                weather=payload.get("weather") or "",
                temp_low=payload.get("temp_low") or "",
                temp_high=payload.get("temp_high") or "",
            )
        except Exception:
            ex = DailyLogExtras()
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
        author_id=target_author,
        record_ids=record_ids or None,
    )
