"""测量设备台账路由（P3.6）。

测量/试验设备的 CRUD + 临期检定提醒：
- GET /expiring?days=30   status=在用 且 calib_due 非空 且 calib_due <= 今天+days，
  按 calib_due 升序，每项附 days_left（YYYY-MM-DD 字符串可直接比较）
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.equipment import MeasureDevice
from app.models.user import User
from app.routers.auth import get_current_user

router = APIRouter()

DEVICE_STATUS = ("在用", "停用", "维修")


class DeviceCreate(BaseModel):
    name: str = Field(default="", max_length=128)
    model_no: str = Field(default="", max_length=64)
    serial_no: str = Field(default="", max_length=64)
    unit: str = Field(default="", max_length=32)
    calib_date: str = ""
    calib_due: str = ""
    status: str = "在用"
    remark: str = ""
    project_id: Optional[int] = None


class DeviceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    model_no: Optional[str] = Field(default=None, max_length=64)
    serial_no: Optional[str] = Field(default=None, max_length=64)
    unit: Optional[str] = Field(default=None, max_length=32)
    calib_date: Optional[str] = None
    calib_due: Optional[str] = None
    status: Optional[str] = None
    remark: Optional[str] = None


def _serialize(d: MeasureDevice, with_detail: bool = False) -> dict:
    data = {
        "id": d.id,
        "name": d.name,
        "model_no": d.model_no,
        "serial_no": d.serial_no,
        "calib_date": d.calib_date,
        "calib_due": d.calib_due,
        "status": d.status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }
    if with_detail:
        data["unit"] = d.unit
        data["remark"] = d.remark
        data["project_id"] = d.project_id
    return data


async def _get_device(db: AsyncSession, did: int, user: User) -> MeasureDevice:
    d = await db.get(MeasureDevice, did)
    if d is None or d.user_id != user.id:
        raise HTTPException(status_code=404, detail="测量设备不存在")
    return d


@router.get("")
async def list_devices(
    status: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(MeasureDevice).where(MeasureDevice.user_id == user.id)
    if status and status in DEVICE_STATUS:
        query = query.where(MeasureDevice.status == status)
    rows = (await db.execute(
        query.order_by(MeasureDevice.calib_due.asc(), MeasureDevice.id.desc()).limit(200)
    )).scalars().all()
    return {"total": len(rows), "items": [_serialize(d) for d in rows]}


@router.get("/expiring")
async def expiring_devices(
    days: int = 30,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """临期检定：在用且有效期至 <= 今天+days，按 calib_due 升序并附 days_left。"""
    today = date.today()
    deadline = (today + timedelta(days=max(days, 0))).isoformat()
    rows = (await db.execute(
        select(MeasureDevice)
        .where(
            MeasureDevice.user_id == user.id,
            MeasureDevice.status == "在用",
            MeasureDevice.calib_due != "",
            MeasureDevice.calib_due <= deadline,
        )
        .order_by(MeasureDevice.calib_due.asc())
        .limit(200)
    )).scalars().all()
    items = []
    for d in rows:
        try:
            due = date.fromisoformat(d.calib_due)
            days_left = (due - today).days
        except ValueError:
            continue
        item = _serialize(d, with_detail=True)
        item["days_left"] = days_left
        items.append(item)
    return {"total": len(items), "items": items}


@router.post("")
async def create_device(
    req: DeviceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = MeasureDevice(
        user_id=user.id,
        project_id=req.project_id if req.project_id is not None else user.project_id,
        name=req.name.strip(),
        model_no=req.model_no.strip(),
        serial_no=req.serial_no.strip(),
        unit=req.unit.strip(),
        calib_date=req.calib_date.strip(),
        calib_due=req.calib_due.strip(),
        status=req.status if req.status in DEVICE_STATUS else "在用",
        remark=req.remark,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return _serialize(d, with_detail=True)


@router.get("/{did}")
async def get_device(
    did: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = await _get_device(db, did, user)
    return _serialize(d, with_detail=True)


@router.put("/{did}")
async def update_device(
    did: int,
    req: DeviceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = await _get_device(db, did, user)
    if req.name is not None:
        d.name = req.name.strip()
    if req.model_no is not None:
        d.model_no = req.model_no.strip()
    if req.serial_no is not None:
        d.serial_no = req.serial_no.strip()
    if req.unit is not None:
        d.unit = req.unit.strip()
    if req.calib_date is not None:
        d.calib_date = req.calib_date.strip()
    if req.calib_due is not None:
        d.calib_due = req.calib_due.strip()
    if req.status is not None and req.status in DEVICE_STATUS:
        d.status = req.status
    if req.remark is not None:
        d.remark = req.remark
    await db.commit()
    await db.refresh(d)
    return _serialize(d, with_detail=True)


@router.delete("/{did}")
async def delete_device(
    did: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = await _get_device(db, did, user)
    await db.delete(d)
    await db.commit()
    return {"detail": "测量设备已删除", "id": did}
