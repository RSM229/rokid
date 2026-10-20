"""验收流程模板路由 - PC 端新增/修改/删除/查询验收流程模板

作者: cloud-backend
创建日期: 2026-07-18
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.acceptance_template import AcceptanceTemplate, AcceptanceType
from app.models.user import User
from app.routers.auth import get_current_user, require_permission
from app.services.acceptance_template_service import get_template_by_id, list_templates

router = APIRouter()

# 合法验收类型集合
_VALID_ACCEPTANCE_TYPES = {t.value for t in AcceptanceType}


class TemplateCreateRequest(BaseModel):
    acceptance_type: str = Field(..., description="验收类型")
    title: str = Field(..., min_length=1, max_length=128)
    steps: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    common_violations: list[str] = Field(default_factory=list)
    regulation_references: list[str] = Field(default_factory=list)
    is_active: bool = True
    remark: Optional[str] = None


class TemplateUpdateRequest(BaseModel):
    acceptance_type: Optional[str] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=128)
    steps: Optional[list[str]] = None
    key_points: Optional[list[str]] = None
    common_violations: Optional[list[str]] = None
    regulation_references: Optional[list[str]] = None
    is_active: Optional[bool] = None
    remark: Optional[str] = None


def _template_to_dict(tpl: AcceptanceTemplate) -> dict:
    return {
        "id": tpl.id,
        "acceptance_type": tpl.acceptance_type,
        "title": tpl.title,
        "steps": tpl.steps_json or [],
        "key_points": tpl.key_points_json or [],
        "common_violations": tpl.common_violations_json or [],
        "regulation_references": tpl.regulation_references_json or [],
        "is_active": tpl.is_active,
        "created_by": tpl.created_by,
        "remark": tpl.remark,
        "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
        "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
    }


@router.get("")
async def list_acceptance_templates(
    acceptance_type: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取验收流程模板列表（支持按验收类型筛选）"""
    items = await list_templates(db, acceptance_type=acceptance_type)
    return {"total": len(items), "templates": [_template_to_dict(t) for t in items]}


@router.get("/types")
async def list_acceptance_types(user: User = Depends(get_current_user)):
    """获取支持的验收类型枚举"""
    return {"types": _VALID_ACCEPTANCE_TYPES}


@router.get("/{template_id}")
async def get_acceptance_template(
    template_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取验收流程模板详情"""
    tpl = await get_template_by_id(db, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return _template_to_dict(tpl)


@router.post("")
async def create_acceptance_template(
    req: TemplateCreateRequest,
    user: User = Depends(require_permission("acceptance_template")),
    db: AsyncSession = Depends(get_db),
):
    """创建验收流程模板（需 acceptance_template 权限）"""
    if req.acceptance_type not in _VALID_ACCEPTANCE_TYPES:
        raise HTTPException(status_code=400, detail=f"验收类型非法，可选: {', '.join(sorted(_VALID_ACCEPTANCE_TYPES))}")

    tpl = AcceptanceTemplate(
        acceptance_type=req.acceptance_type,
        title=req.title,
        steps_json=req.steps,
        key_points_json=req.key_points,
        common_violations_json=req.common_violations,
        regulation_references_json=req.regulation_references,
        is_active=req.is_active,
        created_by=user.id,
        remark=req.remark,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return {"detail": "模板已创建", "template": _template_to_dict(tpl)}


@router.put("/{template_id}")
async def update_acceptance_template(
    template_id: int,
    req: TemplateUpdateRequest,
    user: User = Depends(require_permission("acceptance_template")),
    db: AsyncSession = Depends(get_db),
):
    """更新验收流程模板"""
    tpl = await get_template_by_id(db, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")

    update_data = req.model_dump(exclude_unset=True)
    if "acceptance_type" in update_data:
        if update_data["acceptance_type"] not in _VALID_ACCEPTANCE_TYPES:
            raise HTTPException(status_code=400, detail="验收类型非法")
        tpl.acceptance_type = update_data["acceptance_type"]
    if "title" in update_data:
        tpl.title = update_data["title"]
    if "steps" in update_data:
        tpl.steps_json = update_data["steps"]
    if "key_points" in update_data:
        tpl.key_points_json = update_data["key_points"]
    if "common_violations" in update_data:
        tpl.common_violations_json = update_data["common_violations"]
    if "regulation_references" in update_data:
        tpl.regulation_references_json = update_data["regulation_references"]
    if "is_active" in update_data:
        tpl.is_active = update_data["is_active"]
    if "remark" in update_data:
        tpl.remark = update_data["remark"]
    tpl.updated_at = datetime.now()

    await db.commit()
    await db.refresh(tpl)
    return {"detail": "模板已更新", "template": _template_to_dict(tpl)}


@router.delete("/{template_id}")
async def delete_acceptance_template(
    template_id: int,
    user: User = Depends(require_permission("acceptance_template")),
    db: AsyncSession = Depends(get_db),
):
    """删除验收流程模板（软删除：置 is_active=False；硬删除需管理员额外确认）"""
    tpl = await get_template_by_id(db, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    # 超管可硬删除（普通管理员仅软删除）
    if user.is_super_admin():
        await db.delete(tpl)
    else:
        tpl.is_active = False
        tpl.updated_at = datetime.now()
    await db.commit()
    return {"detail": "模板已删除"}
