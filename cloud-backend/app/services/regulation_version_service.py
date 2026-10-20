"""规范版本管理服务 - 版本追踪、历史查询、版本回退"""
import hashlib
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.regulation_version import RegulationVersion
from app.services.knowledge_service import iter_knowledge_documents, REGULATION_CATEGORIES

logger = logging.getLogger(__name__)


def _file_checksum(file_path: Path) -> str:
    """计算文件 SHA256 校验和"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_doc_by_name(name: str) -> dict | None:
    """从知识库中按名称/文件名查找文档"""
    for doc in iter_knowledge_documents():
        if doc["name"] == name or doc["filename"] == name:
            return doc
    return None


async def register_version(
    db: AsyncSession,
    doc_name: str,
    filename: str,
    category: str,
    file_path: str,
    version: str = "1.0",
    effective_date: Optional[date] = None,
    expire_date: Optional[date] = None,
    remark: Optional[str] = None,
) -> RegulationVersion:
    """注册或更新规范文档版本记录

    如果同文件名已有活跃版本，则将旧版本置为失效，新增新版本。
    """
    # 将同文件名的旧活跃版本置为失效
    await db.execute(
        update(RegulationVersion)
        .where(
            and_(
                RegulationVersion.filename == filename,
                RegulationVersion.is_active.is_(True),
            )
        )
        .values(is_active=False, expire_date=date.today())
    )

    # 计算校验和
    p = Path(file_path)
    checksum = _file_checksum(p) if p.exists() else None

    record = RegulationVersion(
        doc_name=doc_name,
        filename=filename,
        category=category,
        version=version,
        effective_date=effective_date,
        expire_date=expire_date,
        is_active=True,
        file_path=file_path,
        checksum=checksum,
        remark=remark,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def list_versions(
    db: AsyncSession,
    filename: Optional[str] = None,
    category: Optional[str] = None,
    active_only: bool = False,
) -> list[RegulationVersion]:
    """查询版本列表"""
    stmt = select(RegulationVersion).order_by(RegulationVersion.created_at.desc())
    if filename:
        stmt = stmt.where(RegulationVersion.filename == filename)
    if category:
        stmt = stmt.where(RegulationVersion.category == category)
    if active_only:
        stmt = stmt.where(RegulationVersion.is_active.is_(True))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_version_history(db: AsyncSession, filename: str) -> list[RegulationVersion]:
    """获取指定文档的版本历史"""
    stmt = (
        select(RegulationVersion)
        .where(RegulationVersion.filename == filename)
        .order_by(RegulationVersion.version.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def rollback_version(
    db: AsyncSession,
    version_id: int,
) -> RegulationVersion:
    """版本回退：将指定版本设为活跃，其余同文件名版本设为失效"""
    record = await db.get(RegulationVersion, version_id)
    if not record:
        raise ValueError(f"版本记录不存在: id={version_id}")

    # 将同文件名的其他活跃版本置为失效
    await db.execute(
        update(RegulationVersion)
        .where(
            and_(
                RegulationVersion.filename == record.filename,
                RegulationVersion.id != version_id,
                RegulationVersion.is_active.is_(True),
            )
        )
        .values(is_active=False, expire_date=date.today())
    )

    record.is_active = True
    record.expire_date = None
    record.updated_at = datetime.now()
    await db.commit()
    await db.refresh(record)
    return record


async def sync_versions_from_files(db: AsyncSession) -> dict:
    """扫描知识库文件，为尚未记录的文档自动创建版本记录"""
    existing_files: set[str] = set()
    stmt = select(RegulationVersion.filename).distinct()
    result = await db.execute(stmt)
    for row in result:
        existing_files.add(row[0])

    created = 0
    for doc in iter_knowledge_documents():
        if doc["filename"] in existing_files:
            continue
        await register_version(
            db,
            doc_name=doc["name"],
            filename=doc["filename"],
            category=doc["category"],
            file_path=doc["path"],
            version="1.0",
        )
        created += 1

    return {"scanned": len(iter_knowledge_documents()), "created": created}
