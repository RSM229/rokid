"""平行检查：必填校验、项目隔离、详情编辑和独立文书生成。"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.parallel import ParallelInspection
from app.models.document import Document, DocStatus
from app.models.user import User, Project
from app.routers.auth import (
    get_current_user,
    require_project_id,
    project_scope_id,
    can_read_project,
)
from app.services.record_fields import invalidate_document, project_fields
from app.services.form_renderer import render_form

router = APIRouter()
CONCLUSIONS = ("合格", "不合格")


class ParallelCreate(BaseModel):
    inspect_date: str
    part: str = Field(min_length=1, max_length=256)
    item: str = Field(min_length=1, max_length=256)
    method: str = ""
    basis: str = ""
    result_detail: str = Field(min_length=1)
    conclusion: str
    treatment: str = ""
    project_id: Optional[int] = None

    @field_validator("inspect_date")
    @classmethod
    def valid_date(cls, value):
        return date.fromisoformat(value).isoformat()

    @field_validator("part", "item", "result_detail")
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError("必填项不能为空")
        return value.strip()

    @field_validator("conclusion")
    @classmethod
    def valid_conclusion(cls, value):
        if value not in CONCLUSIONS: raise ValueError("结论请人工选择合格或不合格")
        return value


class ParallelUpdate(BaseModel):
    inspect_date: Optional[str] = None
    part: Optional[str] = Field(default=None, min_length=1, max_length=256)
    item: Optional[str] = Field(default=None, min_length=1, max_length=256)
    method: Optional[str] = None
    basis: Optional[str] = None
    result_detail: Optional[str] = Field(default=None, min_length=1)
    conclusion: Optional[str] = None
    treatment: Optional[str] = None


async def _get_inspection(db, pid, user, all_projects: bool = False):
    p = await db.get(ParallelInspection, pid)
    if p is None or not can_read_project(p.project_id, user, all_projects):
        raise HTTPException(404, "平行检查记录不存在")
    return p


def _serialize(p, project=None):
    return {"id": p.id, "project_id": p.project_id, "project_name": project.name if project else "",
            "doc_number": f"GD-B-213-{p.id:05d}", "inspect_date": p.inspect_date, "part": p.part, "item": p.item,
            "method": p.method or "", "basis": p.basis, "result_detail": p.result_detail,
            "conclusion": p.conclusion, "treatment": p.treatment,
            "generated_doc_id": p.generated_doc_id, "form_code": p.form_code,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


async def _editable(db, p, user):
    if p.user_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(403, "无权修改该检查记录")
    doc = await db.get(Document, p.generated_doc_id) if p.generated_doc_id else None
    if doc and doc.status not in (DocStatus.DRAFT.value, DocStatus.REJECTED.value):
        raise HTTPException(409, "已提交文书须驳回后才能修改")
    return doc


def _content(p, project):
    return {**project_fields(project), "doc_number": f"GD-B-213-{p.id:05d}",
            "date": p.inspect_date, "time": p.inspect_date, "section": p.part, "acceptance_content": p.item,
            "method": p.method or "", "basis": p.basis, "record": p.result_detail,
            "conclusion": p.conclusion, "handling": p.treatment}


@router.get("")
async def list_inspections(keyword: Optional[str] = None, all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    scope = project_scope_id(user, all_projects)
    query = select(ParallelInspection)
    if scope is not None:
        query = query.where(ParallelInspection.project_id == scope)
    if keyword:
        kw = f"%{keyword.strip()}%"
        query = query.where(or_(ParallelInspection.part.like(kw), ParallelInspection.item.like(kw)))
    rows = (await db.execute(query.order_by(ParallelInspection.inspect_date.desc(), ParallelInspection.id.desc()).limit(200))).scalars().all()
    projects = {}
    for pid in {p.project_id for p in rows}:
        projects[pid] = await db.get(Project, pid)
    if scope is None:
        items = [_serialize(p, projects.get(p.project_id)) for p in rows]
    else:
        items = [_serialize(p, projects.get(scope)) for p in rows]
    return {"total": len(rows), "items": items}


@router.post("")
async def create_inspection(req: ParallelCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    project_id = require_project_id(user)
    if not user.has_permission("inspection"): raise HTTPException(403, "需要现场检查权限")
    if req.project_id is not None and req.project_id != project_id: raise HTTPException(403, "只能在当前项目创建记录")
    p = ParallelInspection(user_id=user.id, **{**req.model_dump(), "project_id": project_id})
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _serialize(p, await db.get(Project, project_id))


@router.get("/{pid}")
async def get_inspection(pid: int, all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = await _get_inspection(db, pid, user, all_projects)
    return _serialize(p, await db.get(Project, p.project_id))


@router.put("/{pid}")
async def update_inspection(pid: int, req: ParallelUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = await _get_inspection(db, pid, user)
    doc = await _editable(db, p, user)
    changes = req.model_dump(exclude_unset=True)
    try:
        ParallelCreate.model_validate({**_serialize(p), **changes})
    except ValidationError:
        raise HTTPException(400, "日期、部位、项目、检查记录和人工结论必须有效且非空")
    for key, value in changes.items(): setattr(p, key, value.strip() if isinstance(value, str) else value)
    if doc:
        content = _content(p, await db.get(Project, p.project_id))
        for key in ("signing", "offline_signoffs"):
            if (doc.content or {}).get(key) is not None:
                content[key] = (doc.content or {})[key]
        doc.content = content
        doc.status = DocStatus.DRAFT.value
        invalidate_document(doc)
    await db.commit()
    await db.refresh(p)
    return _serialize(p, await db.get(Project, p.project_id))


@router.post("/{pid}/document")
async def generate_document(pid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = await _get_inspection(db, pid, user)
    if p.generated_doc_id:
        existing = await db.get(Document, p.generated_doc_id)
        if existing:
            return {"doc_id": existing.id, "doc_number": (existing.content or {}).get("doc_number"), "idempotent_replay": True}
    project = await db.get(Project, p.project_id)
    try:
        ParallelCreate.model_validate(_serialize(p, project))
    except ValidationError:
        raise HTTPException(400, "请补全检查记录必填内容再生成文书")
    content = _content(p, project)
    doc = Document(doc_type="平行检查记录", title=f"平行检查记录_{p.id}", content=content,
                   form_code=p.form_code, project_id=p.project_id, creator_id=p.user_id, status=DocStatus.DRAFT.value)
    docx = render_form(p.form_code, content)
    doc.docx_path = f"/exports/{docx.name}"
    db.add(doc)
    await db.flush()
    p.generated_doc_id = doc.id
    await db.commit()
    return {"doc_id": doc.id, "doc_number": content["doc_number"], "docx_url": doc.docx_path}


@router.delete("/{pid}")
async def delete_inspection(pid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = await _get_inspection(db, pid, user)
    doc = await _editable(db, p, user)
    if doc and doc.signed_pdf_path: raise HTTPException(409, "已签认的检查记录不能删除")
    await db.delete(p)
    await db.commit()
    return {"detail": "平行检查记录已删除", "id": pid}
