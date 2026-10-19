"""进度记录路由（P3.4）。

按日施工进度记录的 CRUD + 最新进度：
- GET  /latest   当前用户当前项目下 record_date 最新一条（进度看板用）
- 其余为标准 CRUD；plan/actual 限 0~100，列表支持 date_from/date_to 过滤
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.progress import ProgressRecord
from app.models.user import User
from app.routers.auth import get_current_user

router = APIRouter()


class ProgressCreate(BaseModel):
    record_date: str = ""
    part: str = Field(default="", max_length=256)
    plan_percent: int = Field(default=0, ge=0, le=100)
    actual_percent: int = Field(default=0, ge=0, le=100)
    description: str = ""
    project_id: Optional[int] = None


class ProgressUpdate(BaseModel):
    record_date: Optional[str] = None
    part: Optional[str] = Field(default=None, max_length=256)
    plan_percent: Optional[int] = Field(default=None, ge=0, le=100)
    actual_percent: Optional[int] = Field(default=None, ge=0, le=100)
    description: Optional[str] = None


def _serialize(r: ProgressRecord, with_detail: bool = False) -> dict:
    data = {
        "id": r.id,
        "record_date": r.record_date,
        "part": r.part,
        "plan_percent": r.plan_percent,
        "actual_percent": r.actual_percent,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
    if with_detail:
        data["description"] = r.description
        data["project_id"] = r.project_id
    return data


async def _get_record(db: AsyncSession, rid: int, user: User) -> ProgressRecord:
    r = await db.get(ProgressRecord, rid)
    if r is None or r.user_id != user.id:
        raise HTTPException(status_code=404, detail="进度记录不存在")
    return r


@router.get("")
async def list_records(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(ProgressRecord).where(ProgressRecord.user_id == user.id)
    if date_from:
        query = query.where(ProgressRecord.record_date >= date_from.strip())
    if date_to:
        query = query.where(ProgressRecord.record_date <= date_to.strip())
    rows = (await db.execute(
        query.order_by(ProgressRecord.record_date.desc(), ProgressRecord.id.desc()).limit(200)
    )).scalars().all()
    return {"total": len(rows), "items": [_serialize(r) for r in rows]}


@router.get("/latest")
async def latest_record(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户当前项目下 record_date 最新一条；无记录返回 {actual_percent: null}。"""
    query = select(ProgressRecord).where(ProgressRecord.user_id == user.id)
    if user.project_id is not None:
        query = query.where(ProgressRecord.project_id == user.project_id)
    r = (await db.execute(
        query.order_by(ProgressRecord.record_date.desc(), ProgressRecord.id.desc()).limit(1)
    )).scalars().first()
    if r is None:
        return {"actual_percent": None}
    return {
        "actual_percent": r.actual_percent,
        "plan_percent": r.plan_percent,
        "record_date": r.record_date,
        "part": r.part,
    }


@router.post("")
async def create_record(
    req: ProgressCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = ProgressRecord(
        user_id=user.id,
        project_id=req.project_id if req.project_id is not None else user.project_id,
        record_date=req.record_date.strip(),
        part=req.part.strip(),
        plan_percent=req.plan_percent,
        actual_percent=req.actual_percent,
        description=req.description,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _serialize(r, with_detail=True)


@router.get("/{rid}")
async def get_record(
    rid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_record(db, rid, user)
    return _serialize(r, with_detail=True)


@router.put("/{rid}")
async def update_record(
    rid: int,
    req: ProgressUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_record(db, rid, user)
    if req.record_date is not None:
        r.record_date = req.record_date.strip()
    if req.part is not None:
        r.part = req.part.strip()
    if req.plan_percent is not None:
        r.plan_percent = req.plan_percent
    if req.actual_percent is not None:
        r.actual_percent = req.actual_percent
    if req.description is not None:
        r.description = req.description
    await db.commit()
    await db.refresh(r)
    return _serialize(r, with_detail=True)


@router.delete("/{rid}")
async def delete_record(
    rid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_record(db, rid, user)
    await db.delete(r)
    await db.commit()
    return {"detail": "进度记录已删除", "id": rid}
