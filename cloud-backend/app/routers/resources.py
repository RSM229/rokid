"""资源投入日报路由（P3.3）。

人机料投入的 CRUD + 近 7 日聚合：
- GET  /summary   近 7 日：labor_total、labor_by_day、machine_top（前 5）
- machines/materials 为 JSON 数组，防御性解析（同 meeting 的 topics 写法）
"""
import json
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.resources import ResourceInput
from app.models.user import User
from app.routers.auth import get_current_user

router = APIRouter()


class MachineItem(BaseModel):
    name: str = ""
    count: int = 0


class MaterialItem(BaseModel):
    name: str = ""
    spec: str = ""
    quantity: str = ""
    unit: str = ""


class ResourceCreate(BaseModel):
    record_date: str = ""
    labor_count: int = Field(default=0, ge=0)
    labor_detail: str = ""
    machines: list[MachineItem] = []
    materials: list[MaterialItem] = []
    remark: str = ""
    project_id: Optional[int] = None


class ResourceUpdate(BaseModel):
    record_date: Optional[str] = None
    labor_count: Optional[int] = Field(default=None, ge=0)
    labor_detail: Optional[str] = None
    machines: Optional[list[MachineItem]] = None
    materials: Optional[list[MaterialItem]] = None
    remark: Optional[str] = None


def _parse_json_list(raw: str) -> list[dict]:
    try:
        items = json.loads(raw or "[]")
        return items if isinstance(items, list) else []
    except (ValueError, TypeError):
        return []


def _dump_machines(machines: list[MachineItem]) -> str:
    cleaned = []
    for m in machines:
        name = m.name.strip()
        if not name:
            continue
        cleaned.append({"name": name, "count": max(m.count, 0)})
    return json.dumps(cleaned, ensure_ascii=False)


def _dump_materials(materials: list[MaterialItem]) -> str:
    cleaned = []
    for m in materials:
        name = m.name.strip()
        if not name:
            continue
        cleaned.append({
            "name": name,
            "spec": m.spec.strip(),
            "quantity": m.quantity.strip(),
            "unit": m.unit.strip(),
        })
    return json.dumps(cleaned, ensure_ascii=False)


def _serialize(r: ResourceInput, with_detail: bool = False) -> dict:
    machines = _parse_json_list(r.machines)
    materials = _parse_json_list(r.materials)
    data = {
        "id": r.id,
        "record_date": r.record_date,
        "labor_count": r.labor_count,
        "machine_total": sum(int(m.get("count") or 0) for m in machines),
        "material_total": len(materials),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
    if with_detail:
        data["labor_detail"] = r.labor_detail
        data["machines"] = machines
        data["materials"] = materials
        data["remark"] = r.remark
        data["project_id"] = r.project_id
    return data


async def _get_resource(db: AsyncSession, rid: int, user: User) -> ResourceInput:
    r = await db.get(ResourceInput, rid)
    if r is None or r.user_id != user.id:
        raise HTTPException(status_code=404, detail="资源投入记录不存在")
    return r


@router.get("")
async def list_resources(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(ResourceInput)
        .where(ResourceInput.user_id == user.id)
        .order_by(ResourceInput.record_date.desc(), ResourceInput.id.desc())
        .limit(200)
    )).scalars().all()
    return {"total": len(rows), "items": [_serialize(r) for r in rows]}


@router.get("/summary")
async def resource_summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """近 7 日聚合：人工合计、逐日人工、机械投入前 5。"""
    start = (date.today() - timedelta(days=6)).isoformat()
    rows = (await db.execute(
        select(ResourceInput)
        .where(ResourceInput.user_id == user.id, ResourceInput.record_date >= start)
        .order_by(ResourceInput.record_date.asc())
        .limit(200)
    )).scalars().all()
    labor_by_day: list[dict] = []
    machine_counter: dict[str, int] = {}
    for r in rows:
        labor_by_day.append({"date": r.record_date, "labor": r.labor_count})
        for m in _parse_json_list(r.machines):
            name = str(m.get("name", "")).strip()
            if name:
                machine_counter[name] = machine_counter.get(name, 0) + int(m.get("count") or 0)
    machine_top = sorted(
        ({"name": k, "count": v} for k, v in machine_counter.items()),
        key=lambda x: -x["count"],
    )[:5]
    return {
        "days": 7,
        "labor_total": sum(d["labor"] for d in labor_by_day),
        "labor_by_day": labor_by_day,
        "machine_top": machine_top,
    }


@router.post("")
async def create_resource(
    req: ResourceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = ResourceInput(
        user_id=user.id,
        project_id=req.project_id if req.project_id is not None else user.project_id,
        record_date=req.record_date.strip(),
        labor_count=req.labor_count,
        labor_detail=req.labor_detail,
        machines=_dump_machines(req.machines),
        materials=_dump_materials(req.materials),
        remark=req.remark,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _serialize(r, with_detail=True)


@router.get("/{rid}")
async def get_resource(
    rid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_resource(db, rid, user)
    return _serialize(r, with_detail=True)


@router.put("/{rid}")
async def update_resource(
    rid: int,
    req: ResourceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_resource(db, rid, user)
    if req.record_date is not None:
        r.record_date = req.record_date.strip()
    if req.labor_count is not None:
        r.labor_count = req.labor_count
    if req.labor_detail is not None:
        r.labor_detail = req.labor_detail
    if req.machines is not None:
        r.machines = _dump_machines(req.machines)
    if req.materials is not None:
        r.materials = _dump_materials(req.materials)
    if req.remark is not None:
        r.remark = req.remark
    await db.commit()
    await db.refresh(r)
    return _serialize(r, with_detail=True)


@router.delete("/{rid}")
async def delete_resource(
    rid: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await _get_resource(db, rid, user)
    await db.delete(r)
    await db.commit()
    return {"detail": "资源投入记录已删除", "id": rid}
