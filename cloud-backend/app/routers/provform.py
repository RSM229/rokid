"""省统表 schema 路由（P2.1 模板层）。

提供字段级 schema 的目录/详情/校验接口：
- GET  /schemas                 全量目录（registry 29 类，标注哪些已有字段层）
- GET  /schemas/{form_code}     单表完整 schema（供小程序 prov-form 组件渲染）
- GET  /schemas/{form_code}/steps  分步表单（字段表决定只读/必填，可按草稿/记录预填）
- POST /schemas/{form_code}/validate  按 schema 校验表单值
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.agent_draft import AgentDraft
from app.models.user import User
from app.models.work_record import WorkRecord
from app.routers.auth import get_current_user
from app.services import provform_schema, record_steps

router = APIRouter()


class ValidateRequest(BaseModel):
    values: dict = {}


@router.get("/schemas")
async def list_schemas(user: User = Depends(get_current_user)):
    items = provform_schema.schema_catalog()
    return {"total": len(items), "items": items}


@router.get("/schemas/{form_code}")
async def get_schema(form_code: str, user: User = Depends(get_current_user)):
    if form_code not in provform_schema.known_form_codes():
        raise HTTPException(status_code=404, detail="未知文书类型")
    schema = provform_schema.get_schema(form_code)
    if schema is None:
        raise HTTPException(status_code=404, detail="该文书类型暂无字段级 schema")
    return schema


@router.get("/schemas/{form_code}/steps")
async def get_schema_steps(
    form_code: str,
    record_id: int = 0,
    draft_id: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分步表单：字段表口径（kind/required）+ 预填值 + 缺项清单。

    预填优先级：草稿（聊天追问暂存）> 已有工作记录 > 空。
    只读接口，不写库、不触发 LLM、不生成文书。
    """
    if form_code not in provform_schema.known_form_codes():
        raise HTTPException(status_code=404, detail="未知文书类型")

    prefill: dict = {}
    source = "none"
    if draft_id:
        prefill, source = await _prefill_from_draft(db, user, draft_id)
    elif record_id:
        prefill, source = await _prefill_from_record(db, user, record_id)

    data = record_steps.build_steps(form_code, prefill, source)
    if data is None:
        raise HTTPException(status_code=404, detail="该文书类型暂无字段级 schema")
    return data


async def _prefill_from_draft(db: AsyncSession, user: User, draft_id: int) -> tuple[dict, str]:
    row = (await db.execute(
        select(AgentDraft).where(AgentDraft.id == draft_id, AgentDraft.user_id == user.id)
    )).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="草稿不存在")
    try:
        args = json.loads(row.args_json or "{}")
    except (TypeError, ValueError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    merged: dict = {}
    extra = args.get("extra_fields")
    if isinstance(extra, dict):
        merged.update(extra)
    merged.update({k: v for k, v in args.items() if k != "extra_fields"})
    return merged, "draft"


async def _prefill_from_record(db: AsyncSession, user: User, record_id: int) -> tuple[dict, str]:
    row = await db.get(WorkRecord, record_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if row.creator_id != user.id and not user.is_super_admin:
        raise HTTPException(status_code=403, detail="无权查看该记录")
    content = row.content_json or {}
    merged: dict = {}
    if isinstance(content.get("prov_fields"), dict):
        merged.update(content["prov_fields"])
    for key, value in content.items():
        if key == "prov_fields":
            continue
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            merged.setdefault(key, value)
    return merged, "record"


@router.post("/schemas/{form_code}/validate")
async def validate_schema_values(
    form_code: str,
    req: ValidateRequest,
    user: User = Depends(get_current_user),
):
    if provform_schema.get_schema(form_code) is None:
        raise HTTPException(status_code=404, detail="该文书类型暂无字段级 schema")
    return provform_schema.validate_values(form_code, req.values or {})
