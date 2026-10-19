"""验收流程模板服务 - 数据库优先，无则回退内置 ACCEPTANCE_GUIDES

作者: cloud-backend
创建日期: 2026-07-18
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.acceptance_template import AcceptanceTemplate
from app.services.knowledge_service import ACCEPTANCE_GUIDES

logger = logging.getLogger(__name__)


async def get_guide_with_fallback(db: AsyncSession, acceptance_type: str) -> dict:
    """优先从数据库查询验收模板，无则回退到内置 ACCEPTANCE_GUIDES。

    匹配规则：先精确匹配 acceptance_type，再尝试关键词包含匹配。
    返回结构与 knowledge_service.get_acceptance_guide 一致。
    """
    # 1. 数据库精确匹配（按类型取最近一条启用模板）
    try:
        result = await db.execute(
            select(AcceptanceTemplate).where(
                AcceptanceTemplate.acceptance_type == acceptance_type,
                AcceptanceTemplate.is_active.is_(True),
            ).order_by(AcceptanceTemplate.updated_at.desc()).limit(1)
        )
        tpl = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[acceptance_template] 查询数据库失败，回退内置模板：{exc}")
        tpl = None

    # 2. 关键词包含匹配
    if tpl is None:
        try:
            result = await db.execute(
                select(AcceptanceTemplate).where(
                    AcceptanceTemplate.is_active.is_(True),
                ).order_by(AcceptanceTemplate.updated_at.desc())
            )
            for item in result.scalars().all():
                if item.acceptance_type in acceptance_type or acceptance_type in item.acceptance_type:
                    tpl = item
                    break
        except Exception as exc:
            logger.warning(f"[acceptance_template] 关键词匹配失败：{exc}")

    if tpl is not None:
        return {
            "keyword": acceptance_type,
            "acceptance_type": tpl.acceptance_type,
            "title": tpl.title,
            "steps": tpl.steps_json or [],
            "key_points": tpl.key_points_json or [],
            "common_violations": tpl.common_violations_json or [],
            "regulation_basis": tpl.regulation_references_json or [],
            "source": "db",
            "template_id": tpl.id,
        }

    # 3. 回退到内置 ACCEPTANCE_GUIDES
    for key, guide in ACCEPTANCE_GUIDES.items():
        if key in acceptance_type:
            return {
                "keyword": acceptance_type,
                "steps": guide["steps"],
                "key_points": guide["key_points"],
                "common_violations": guide["common_violations"],
                "regulation_basis": guide["regulation_basis"],
                "source": "builtin",
            }

    # 4. 默认指引
    return {
        "keyword": acceptance_type,
        "steps": ["1. 施工前准备检查", "2. 材料进场验收", "3. 施工过程检查", "4. 质量验收", "5. 资料归档"],
        "key_points": ["施工方案审批", "材料合格证", "施工记录", "验收标准", "隐蔽工程记录"],
        "common_violations": ["未按方案施工", "材料不合格", "记录缺失", "验收不规范"],
        "regulation_basis": ["请查询对应专业施工质量验收规范"],
        "source": "default",
    }


async def get_template_by_id(db: AsyncSession, template_id: int) -> Optional[AcceptanceTemplate]:
    return await db.get(AcceptanceTemplate, template_id)


async def list_templates(
    db: AsyncSession,
    acceptance_type: Optional[str] = None,
    include_inactive: bool = False,
) -> list[AcceptanceTemplate]:
    query = select(AcceptanceTemplate)
    if not include_inactive:
        query = query.where(AcceptanceTemplate.is_active.is_(True))
    if acceptance_type:
        query = query.where(AcceptanceTemplate.acceptance_type == acceptance_type)
    query = query.order_by(AcceptanceTemplate.updated_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())
