"""模型配置服务 — 支持环境变量直接读取"""
import json
import base64
import logging
from typing import Optional, List, Dict, Any
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.model_config import ModelConfiguration

logger = logging.getLogger(__name__)

# ============ 模型类型常量 ============
MODEL_TYPES = ["llm", "tts", "stt", "vlm"]


class ModelConfig:
    """模型配置类 - 优先从环境变量读取"""
    def __init__(self, config_type: str):
        self.config_type = config_type
        self.options: Dict[str, Any] = {}
        self._load_from_env()

    def _load_from_env(self):
        """从环境变量加载配置（通过 app.config settings 统一读取）。"""
        if self.config_type == "stt":
            self.provider = settings.default_stt_provider or ""
            self.api_key = settings.default_stt_api_key or ""
            self.base_url = settings.default_stt_base_url or ""
            self.model_id = settings.default_stt_model_id or ""
        elif self.config_type == "tts":
            self.provider = settings.default_tts_provider or ""
            self.api_key = settings.default_tts_api_key or ""
            self.base_url = settings.default_tts_base_url or ""
            self.model_id = settings.default_tts_model_id or ""
            self.options["voice"] = str(settings.default_tts_voice or "").strip()
        elif self.config_type == "llm":
            self.provider = settings.default_llm_provider or ""
            self.api_key = settings.default_llm_api_key or ""
            self.base_url = settings.default_llm_base_url or ""
            self.model_id = settings.default_llm_model_id or ""
        elif self.config_type == "vlm":
            self.provider = settings.default_vlm_provider or ""
            self.api_key = settings.default_vlm_api_key or ""
            self.base_url = settings.default_vlm_base_url or ""
            self.model_id = settings.default_vlm_model_id or ""
            self.options["org_id"] = str(settings.ai_center_org_id or "").strip()
            self.options["environment"] = str(settings.ai_center_environment or "test").strip()
            self.options["input_name"] = str(
                getattr(settings, "vlm_input_name", None) or "image"
            ).strip()
        else:
            self.provider = "unknown"
            self.api_key = ""
            self.base_url = ""
            self.model_id = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model_id)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "provider": self.provider,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model_id": self.model_id,
            "enabled": self.enabled,
        }


# ============ 核心函数 ============

async def get_runtime_model_config(config_type: str) -> ModelConfig:
    """获取运行时模型配置（直接从环境变量读取，不依赖数据库）"""
    return ModelConfig(config_type)


def encrypt_api_key(value: str) -> str:
    """加密 API Key（备用）"""
    if not value:
        return ""
    try:
        key = settings.model_config_encryption_key.encode("utf-8")
        if len(key) < 32:
            key = key.ljust(32, b'0')
        f = Fernet(base64.urlsafe_b64encode(key[:32]))
        return f.encrypt(value.encode("utf-8")).decode("utf-8")
    except Exception:
        return value


def decrypt_api_key(value: str) -> str:
    """解密 API Key（如果失败，返回原值）"""
    if not value:
        return ""
    try:
        key = settings.model_config_encryption_key.encode("utf-8")
        if len(key) < 32:
            key = key.ljust(32, b'0')
        f = Fernet(base64.urlsafe_b64encode(key[:32]))
        return f.decrypt(value.encode("utf-8")).decode("utf-8")
    except Exception:
        return value


# ============ 模型配置管理函数 ============

async def seed_default_model_configs() -> dict:
    """初始化默认模型配置（环境变量模式下无需操作）"""
    logger.info("环境变量模式，无需初始化模型配置")
    return {"status": "success", "message": "使用环境变量配置"}


async def reset_model_config(
    db: AsyncSession, model_type: str, updated_by: int
) -> ModelConfiguration:
    """重置模型配置到默认值：清除自定义 Key 与扩展参数，回到环境变量默认。"""
    if model_type not in MODEL_TYPES:
        raise ValueError(f"无效的模型类型: {model_type}")

    result = await db.execute(
        select(ModelConfiguration).where(ModelConfiguration.model_type == model_type)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ModelConfiguration(model_type=model_type)
        db.add(row)
    row.api_key_encrypted = None
    row.enabled = True
    row.config_json = {}
    row.updated_by = updated_by
    await db.commit()
    await db.refresh(row)
    return row


def _filter_options(options: dict | None) -> dict:
    """过滤扩展参数中的敏感键（与 API 写入校验保持一致）。"""
    if not options:
        return {}
    forbidden = {"api_key", "apikey", "token", "secret", "password"}
    return {k: v for k, v in options.items() if str(k).lower() not in forbidden}


def serialize_model_config(config) -> dict:
    """序列化模型配置（用于 API 返回）。

    兼容 ModelConfiguration ORM 行与 env 版 ModelConfig 对象；
    API Key 一律不得明文返回，扩展参数也会过滤敏感键。
    """
    if hasattr(config, "api_key_encrypted"):
        return {
            "model_type": config.model_type,
            "provider": config.provider,
            "base_url": config.base_url,
            "model_id": config.model_id,
            "api_key": "**masked**" if config.api_key_encrypted else "",
            "enabled": config.enabled,
            "options": _filter_options(config.config_json),
            "updated_at": (
                config.updated_at.isoformat() if config.updated_at else None
            ),
        }
    return {
        "model_type": config.config_type,
        "provider": config.provider,
        "api_key": config.api_key[:20] + "..." if config.api_key else "",
        "base_url": config.base_url,
        "model_id": config.model_id,
        "enabled": config.enabled,
        "options": _filter_options(getattr(config, "options", None)),
    }


async def upsert_model_config(
    db: AsyncSession,
    *,
    model_type: str,
    provider: str,
    base_url: str,
    model_id: str,
    api_key: str | None,
    clear_api_key: bool,
    enabled: bool,
    options: dict,
    updated_by: int,
) -> ModelConfiguration:
    """更新或插入模型配置（持久化到 model_configurations 表）。"""
    if model_type not in MODEL_TYPES:
        raise ValueError(f"无效的模型类型: {model_type}")

    result = await db.execute(
        select(ModelConfiguration).where(ModelConfiguration.model_type == model_type)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ModelConfiguration(model_type=model_type)
        db.add(row)
    row.provider = provider
    row.base_url = base_url
    row.model_id = model_id
    row.enabled = enabled
    row.config_json = options or {}
    row.updated_by = updated_by
    if clear_api_key:
        row.api_key_encrypted = None
    elif api_key:
        row.api_key_encrypted = encrypt_api_key(api_key)
    await db.commit()
    await db.refresh(row)
    return row


async def save_model_config(model_type: str, config: dict) -> dict:
    """保存模型配置（当前仅支持环境变量，不持久化）"""
    return {"message": "配置已保存（环境变量模式）", "config": config}


async def load_model_config(model_type: str) -> dict:
    """加载模型配置"""
    if model_type not in MODEL_TYPES:
        return {"error": f"无效的模型类型: {model_type}"}
    
    config = ModelConfig(model_type)
    return {
        "provider": config.provider,
        "api_key": config.api_key[:20] + "..." if config.api_key else "",
        "base_url": config.base_url,
        "model_id": config.model_id,
        "enabled": config.enabled,
    }


async def get_model_config_list() -> List[dict]:
    """获取所有模型配置列表"""
    result = []
    for model_type in MODEL_TYPES:
        config = ModelConfig(model_type)
        result.append({
            "model_type": model_type,
            "provider": config.provider,
            "base_url": config.base_url,
            "model_id": config.model_id,
            "has_api_key": bool(config.api_key),
            "enabled": config.enabled,
        })
    return result


async def get_model_config(model_type: str) -> Optional[dict]:
    """获取单个模型配置"""
    if model_type not in MODEL_TYPES:
        return None
    config = ModelConfig(model_type)
    return config.to_dict()
