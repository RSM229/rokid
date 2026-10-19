"""工程签证路由（P3.5）。

现场签证的 CRUD + 审批流转：
- POST /{vid}/submit   草稿/已驳回 → 待审批
- POST /{vid}/review   待审批 → 已批准/已驳回（body {action, opinion}）
- 仅 草稿/已驳回 状态可 PUT 修改与 DELETE 删除，其他状态返回 400
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import User
from app.models.visa import Visa
from app.routers.auth import get_current_user

router = APIRouter()

VISA_STATUS = ("草稿", "待审批", "已批准", "已驳回")
EDITABLE_STATUS = ("草稿", "已驳回")


class VisaCreate(BaseModel):
    title: str = Field(default="", max_length=256)
    visa_no: str = Field(default="", max_length=64)
    apply_date: str = ""
    reason: str = ""
    content: str = ""
    amount: float = Field(default=0.0, ge=0)
    project_id: Optional[int] = None


class VisaUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    visa_no: Optional[str] = Field(default=None, max_length=64)
    apply_date: Optional[str] = None
    reason: Optional[str] = None
    content: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)


class ReviewRequest(BaseModel):
    action: str  # approve / reject
    opinion: str = ""


def _serialize(v: Visa, with_detail: bool = False) -> dict:
    data = {
        "id": v.id,
        "title": v.title,
        "visa_no": v.visa_no,
        "apply_date": v.apply_date,
        "amount": v.amount,
        "status": v.status,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }
    if with_detail:
        data["reason"] = v.reason
        data["content"] = v.content
        data["approver"] = v.approver
        data["approve_date"] = v.approve_date
        data["approve_opinion"] = v.approve_opinion
        data["project_id"] = v.project_id
    return data


async def _get_visa(db: AsyncSession, vid: int, user: User) -> Visa:
    v = await db.get(Visa, vid)
    if v is None or v.user_id != user.id:
        raise HTTPException(status_code=404, detail="签证记录不存在")
    return v


@router.get("")
async def list_visas(
    status: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Visa).where(Visa.user_id == user.id)
    if status and status in VISA_STATUS:
        query = query.where(Visa.status == status)
    rows = (await db.execute(
        query.order_by(Visa.apply_date.desc(), Visa.id.desc()).limit(200)
    )).scalars().all()
    return {"total": len(rows), "items": [_serialize(v) for v in rows]}


@router.post("")
async def create_visa(
    req: VisaCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v = Visa(
        user_id=user.id,
        project_id=req.project_id if req.project_id is not None else user.project_id,
        title=req.title.strip() or "工程签证",
        visa_no=req.visa_no.strip(),
        apply_date=req.apply_date.strip(),
        reason=req.reason,
        content=req.content,
        amount=req.amount,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return _serialize(v, with_detail=True)


@router.get("/{vid}")
async def get_visa(
    vid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v = await _get_visa(db, vid, user)
    return _serialize(v, with_detail=True)


@router.put("/{vid}")
async def update_visa(
    vid: int,
    req: VisaUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v = await _get_visa(db, vid, user)
    if v.status not in EDITABLE_STATUS:
        raise HTTPException(status_code=400, detail=f"当前状态（{v.status}）不允许修改")
    if req.title is not None:
        v.title = req.title.strip() or "工程签证"
    if req.visa_no is not None:
        v.visa_no = req.visa_no.strip()
    if req.apply_date is not None:
        v.apply_date = req.apply_date.strip()
    if req.reason is not None:
        v.reason = req.reason
    if req.content is not None:
        v.content = req.content
    if req.amount is not None:
        v.amount = req.amount
    await db.commit()
    await db.refresh(v)
    return _serialize(v, with_detail=True)


@router.delete("/{vid}")
async def delete_visa(
    vid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v = await _get_visa(db, vid, user)
    if v.status not in EDITABLE_STATUS:
        raise HTTPException(status_code=400, detail=f"当前状态（{v.status}）不允许删除")
    await db.delete(v)
    await db.commit()
    return {"detail": "签证记录已删除", "id": vid}


@router.post("/{vid}/submit")
async def submit_visa(
    vid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """提交审批：草稿/已驳回 → 待审批。"""
    v = await _get_visa(db, vid, user)
    if v.status not in EDITABLE_STATUS:
        raise HTTPException(status_code=400, detail=f"当前状态（{v.status}）不允许提交")
    v.status = "待审批"
    await db.commit()
    await db.refresh(v)
    return _serialize(v, with_detail=True)


@router.post("/{vid}/review")
async def review_visa(
    vid: int,
    req: ReviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """审批：待审批 → 已批准/已驳回，记录审批人、日期与意见。"""
    v = await _get_visa(db, vid, user)
    if v.status != "待审批":
        raise HTTPException(status_code=400, detail=f"当前状态（{v.status}）不允许审批")
    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 只能为 approve 或 reject")
    v.status = "已批准" if req.action == "approve" else "已驳回"
    v.approver = user.real_name or user.username
    v.approve_date = date.today().isoformat()
    v.approve_opinion = req.opinion
    await db.commit()
    await db.refresh(v)
    return _serialize(v, with_detail=True)
