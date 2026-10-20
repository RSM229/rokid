"""内置知识库种子 — 把系统“规范知识库”接入多知识库体系。"""
import logging

from sqlalchemy import select

from app.db import async_session
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User

logger = logging.getLogger(__name__)

BUILTIN_KB_NAME = "规范知识库"


async def ensure_builtin_knowledge_base() -> None:
    """确保存在一个系统内置的“规范知识库”（共享类型，所有用户可见）。"""
    async with async_session() as db:
        existing = (
            await db.execute(select(KnowledgeBase).where(KnowledgeBase.is_builtin.is_(True)))
        ).scalar_one_or_none()
        if existing is not None:
            return
        # 注意：此处是种子归属查询，不是鉴权判据，故保留按用户名取 admin。
        # 超管判据统一走 User.is_super_admin()（org_role == 'super_admin'）。
        owner = (
            await db.execute(select(User).where(User.username == "admin"))
        ).scalar_one_or_none()
        if owner is None:
            owner = (
                await db.execute(select(User).order_by(User.id).limit(1))
            ).scalar_one_or_none()
        if owner is None:
            # 空库首次建表时尚无用户，owner_id 外键会失败；等用户初始化后再次启动即可创建。
            logger.warning("暂无用户，跳过内置知识库种子（用户初始化后将自动创建）")
            return
        kb = KnowledgeBase(
            name=BUILTIN_KB_NAME,
            description="系统内置的监理规范知识库（省统表 / 安全监理用表），文档由规范库维护统一管理。",
            kb_type="shared",
            owner_id=owner.id,
            is_builtin=True,
        )
        db.add(kb)
        await db.commit()
        logger.info("已创建内置知识库：%s", BUILTIN_KB_NAME)
