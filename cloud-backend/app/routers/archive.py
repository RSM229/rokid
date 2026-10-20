"""资料记录/问题发单路由（P3.7）。

一个 router 管理两类资源：
- /records            资料收发记录 CRUD（direction 过滤、keyword 匹配 doc_name/doc_no）
- /issues             问题发单 CRUD（status/issue_type 过滤）
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.archive import ArchiveRecord, IssueTicket
from app.models.user import User
from app.routers.auth import get_current_user

router = APIRouter()

DIRECTIONS = ("收", "发")
ISSUE_TYPES = ("质量", "安全", "进度", "其他")
ISSUE_STATUS = ("待处理", "处理中", "已关闭")


class RecordCreate(BaseModel):
    direction: str = "收"
    doc_name: str = Field(default="", max_length=256)
    doc_no: str = Field(default="", max_length=64)
    unit: str = Field(default="", max_length=128)
    trans_date: str = ""
    handler: str = Field(default="", max_length=64)
    remark: str = ""
    project_id: Optional[int] = None


class RecordUpdate(BaseModel):
    direction: Optional[str] = None
    doc_name: Optional[str] = Field(default=None, max_length=256)
    doc_no: Optional[str] = Field(default=None, max_length=64)
    unit: Optional[str] = Field(default=None, max_length=128)
    trans_date: Optional[str] = None
    handler: Optional[str] = Field(default=None, max_length=64)
    remark: Optional[str] = None


class IssueCreate(BaseModel):
    title: str = Field(default="", max_length=256)
    issue_type: str = "质量"
    part: str = Field(default="", max_length=256)
    description: str = ""
    status: str = "待处理"
    deadline: str = ""
    project_id: Optional[int] = None


class IssueUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    issue_type: Optional[str] = None
    part: Optional[str] = Field(default=None, max_length=256)
    description: Optional[str] = None
    status: Optional[str] = None
    deadline: Optional[str] = None


def _serialize_record(r: ArchiveRecord, with_detail: bool = False) -> dict:
    data = {
        "id": r.id,
        "direction": r.direction,
        "doc_name": r.doc_name,
        "doc_no": r.doc_no,
        "unit": r.unit,
        "trans_date": r.trans_date,
        "handler": r.handler,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
    if with_detail:
        data["remark"] = r.remark
        data["project_id"] = r.project_id
    return data


def _serialize_issue(t: IssueTicket, with_detail: bool = False) -> dict:
    data = {
        "id": t.id,
        "title": t.title,
        "issue_type": t.issue_type,
        "part": t.part,
        "status": t.status,
        "deadline": t.deadline,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if with_detail:
        data["description"] = t.description
        data["project_id"] = t.project_id
    return data


async def _get_record(db: AsyncSession, rid: int, user: User) -> ArchiveRecord:
    r = await db.get(ArchiveRecord, rid)
    if r is None or r.user_id != user.id:
        raise HTTPException(status_code=404, detail="资料记录不存在")
    return r


async def _get_issue(db: AsyncSession, iid: int, user: User) -> IssueTicket:
    t = await db.get(IssueTicket, iid)
    if t is None or t.user_id != user.id:
        raise HTTPException(status_code=404, detail="问题发单不存在")
    return t


# ============ 资料收发记录 /records ============

@router.get("/records")
async def list_records(
    direction: Optional[str] = None,
    keyword: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(ArchiveRecord).where(ArchiveRecord.user_id == user.id)
    if direction and direction in DIRECTIONS:
        query = query.where(ArchiveRecord.direction == direction)
    if keyword:
        kw = f"%{keyword.strip()}%"
        query = query.where(or_(
            ArchiveRecord.doc_name.like(kw),
            ArchiveRecord.doc_no.like(kw),
        ))
    rows = (await db.execute(
        query.order_by(ArchiveRecord.trans_date.desc(), ArchiveRecord.id.desc()).limit(200)
    )).scalars().all()
    return {"total": len(rows), "items": [_serialize_record(r) for r in rows]}


@router.post("/records")
async def create_record(
    req: RecordCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = ArchiveRecord(
        user_id=user.id,
        project_id=req.project_id if req.project_id is not None else user.project_id,
        direction=req.direction if req.direction in DIRECTIONS else "收",
        doc_name=req.doc_name.strip(),
        doc_no=req.doc_no.strip(),
        unit=req.unit.strip(),
        trans_date=req.trans_date.strip(),
        handler=req.handler.strip(),
        remark=req.remark,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _serialize_record(r, with_detail=True)


@router.get("/records/{rid}")
async def get_record(
    rid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_record(db, rid, user)
    return _serialize_record(r, with_detail=True)


@router.put("/records/{rid}")
async def update_record(
    rid: int,
    req: RecordUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_record(db, rid, user)
    if req.direction is not None and req.direction in DIRECTIONS:
        r.direction = req.direction
    if req.doc_name is not None:
        r.doc_name = req.doc_name.strip()
    if req.doc_no is not None:
        r.doc_no = req.doc_no.strip()
    if req.unit is not None:
        r.unit = req.unit.strip()
    if req.trans_date is not None:
        r.trans_date = req.trans_date.strip()
    if req.handler is not None:
        r.handler = req.handler.strip()
    if req.remark is not None:
        r.remark = req.remark
    await db.commit()
    await db.refresh(r)
    return _serialize_record(r, with_detail=True)


@router.delete("/records/{rid}")
async def delete_record(
    rid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_record(db, rid, user)
    await db.delete(r)
    await db.commit()
    return {"detail": "资料记录已删除", "id": rid}


# ============ 问题发单 /issues ============

@router.get("/issues")
async def list_issues(
    status: Optional[str] = None,
    issue_type: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(IssueTicket).where(IssueTicket.user_id == user.id)
    if status and status in ISSUE_STATUS:
        query = query.where(IssueTicket.status == status)
    if issue_type and issue_type in ISSUE_TYPES:
        query = query.where(IssueTicket.issue_type == issue_type)
    rows = (await db.execute(
        query.order_by(IssueTicket.deadline.asc(), IssueTicket.id.desc()).limit(200)
    )).scalars().all()
    return {"total": len(rows), "items": [_serialize_issue(t) for t in rows]}


@router.post("/issues")
async def create_issue(
    req: IssueCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    t = IssueTicket(
        user_id=user.id,
        project_id=req.project_id if req.project_id is not None else user.project_id,
        title=req.title.strip(),
        issue_type=req.issue_type if req.issue_type in ISSUE_TYPES else "质量",
        part=req.part.strip(),
        description=req.description,
        status=req.status if req.status in ISSUE_STATUS else "待处理",
        deadline=req.deadline.strip(),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _serialize_issue(t, with_detail=True)


@router.get("/issues/{iid}")
async def get_issue(
    iid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    t = await _get_issue(db, iid, user)
    return _serialize_issue(t, with_detail=True)


@router.put("/issues/{iid}")
async def update_issue(
    iid: int,
    req: IssueUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    t = await _get_issue(db, iid, user)
    if req.title is not None:
        t.title = req.title.strip()
    if req.issue_type is not None and req.issue_type in ISSUE_TYPES:
        t.issue_type = req.issue_type
    if req.part is not None:
        t.part = req.part.strip()
    if req.description is not None:
        t.description = req.description
    if req.status is not None and req.status in ISSUE_STATUS:
        t.status = req.status
    if req.deadline is not None:
        t.deadline = req.deadline.strip()
    await db.commit()
    await db.refresh(t)
    return _serialize_issue(t, with_detail=True)


@router.delete("/issues/{iid}")
async def delete_issue(
    iid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    t = await _get_issue(db, iid, user)
    await db.delete(t)
    await db.commit()
    return {"detail": "问题发单已删除", "id": iid}
