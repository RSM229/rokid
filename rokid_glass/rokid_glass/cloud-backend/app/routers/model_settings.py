"""模型调用设置管理 API。"""
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.model_config import ModelConfiguration
from app.models.user import User
from app.routers.auth import require_permission
from app.services.model_config_service import (
    MODEL_TYPES,
    reset_model_config,
    serialize_model_config,
    upsert_model_config,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class ModelConfigUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    provider: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=8, max_length=1024)
    model_id: str = Field(min_length=1, max_length=256)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def reject_secrets_in_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"api_key", "apikey", "token", "secret", "password"}
        if any(str(key).lower() in forbidden for key in value):
            raise ValueError("扩展参数中不能保存密钥")
        return value


@router.get("")
async def list_model_settings(
    user: User = Depends(require_permission("settings")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ModelConfiguration).where(
            ModelConfiguration.model_type.in_(MODEL_TYPES)
        )
    )
    rows = {row.model_type: row for row in result.scalars().all()}
    return {
        "items": [
            serialize_model_config(rows[model_type])
            for model_type in MODEL_TYPES
            if model_type in rows
        ]
    }


@router.put("/{model_type}")
async def update_model_setting(
    model_type: Literal["llm", "tts", "stt", "vlm"],
    request: ModelConfigUpdate,
    user: User = Depends(require_permission("settings")),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await upsert_model_config(
            db,
            model_type=model_type,
            provider=request.provider,
            base_url=request.base_url,
            model_id=request.model_id,
            api_key=request.api_key,
            clear_api_key=request.clear_api_key,
            enabled=request.enabled,
            options=request.options,
            updated_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("用户 %s 更新了 %s 模型配置", user.id, model_type)
    return serialize_model_config(row)


@router.post("/{model_type}/reset")
async def reset_model_setting(
    model_type: Literal["llm", "tts", "stt", "vlm"],
    user: User = Depends(require_permission("settings")),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await reset_model_config(db, model_type, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("用户 %s 恢复了 %s 模型默认配置", user.id, model_type)
    return serialize_model_config(row)
