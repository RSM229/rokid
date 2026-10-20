"""省统表 schema 路由（P2.1 模板层）。

提供字段级 schema 的目录/详情/校验接口：
- GET  /schemas                 全量目录（registry 29 类，标注哪些已有字段层）
- GET  /schemas/{form_code}     单表完整 schema（供小程序 prov-form 组件渲染）
- POST /schemas/{form_code}/validate  按 schema 校验表单值
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.user import User
from app.routers.auth import get_current_user
from app.services import provform_schema

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


@router.post("/schemas/{form_code}/validate")
async def validate_schema_values(
    form_code: str,
    req: ValidateRequest,
    user: User = Depends(get_current_user),
):
    if provform_schema.get_schema(form_code) is None:
        raise HTTPException(status_code=404, detail="该文书类型暂无字段级 schema")
    return provform_schema.validate_values(form_code, req.values or {})
