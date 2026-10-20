"""规范分类服务 - 规范库跨分类移动、分类统计

作者: cloud-backend
创建日期: 2026-07-18
"""
import logging
import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.regulation_chunk import RegulationChunk
from app.services.knowledge_service import REGULATION_CATEGORIES, iter_knowledge_documents

logger = logging.getLogger(__name__)


def _category_dir(category: str) -> Path:
    """根据分类名返回对应目录路径"""
    if category == "省统表":
        return settings.knowledge_base_path / "省统表word版"
    if category == "安全监理用表":
        return settings.knowledge_base_path / "第三节安全监理用表" / "安全监理用表"
    # 其余分类作为独立目录，便于扩展
    return settings.knowledge_base_path / category


def list_categories() -> list[dict]:
    """列出所有规范分类及各分类下的规范数量"""
    docs = iter_knowledge_documents()
    counts: dict[str, int] = {c: 0 for c in REGULATION_CATEGORIES}
    for d in docs:
        counts[d["category"]] = counts.get(d["category"], 0) + 1
    return [
        {"category": c, "count": counts.get(c, 0)}
        for c in REGULATION_CATEGORIES
    ]


def find_doc_category(filename: str) -> Optional[str]:
    """根据文件名查找当前所在分类"""
    for d in iter_knowledge_documents():
        if d["filename"] == filename:
            return d["category"]
    return None


def move_category(filename: str, target_category: str) -> dict:
    """将规范文件从原分类移动到目标分类。

    返回 {"old_category", "new_category", "filename", "new_path"}。
    """
    if target_category not in REGULATION_CATEGORIES:
        raise ValueError(f"目标分类非法，可选: {', '.join(REGULATION_CATEGORIES)}")

    if Path(filename).name != filename:
        raise ValueError("文件名非法")

    old_category = find_doc_category(filename)
    if old_category is None:
        raise FileNotFoundError(f"规范文件不存在: {filename}")

    if old_category == target_category:
        return {
            "old_category": old_category,
            "new_category": target_category,
            "filename": filename,
            "new_path": str(_category_dir(target_category) / filename),
            "message": "源分类与目标分类相同，无需移动",
        }

    source_dir = _category_dir(old_category)
    target_dir = _category_dir(target_category)
    source_path = (source_dir / filename).resolve()
    target_path = (target_dir / filename).resolve()

    if source_path.parent != source_dir or not source_path.is_file():
        raise FileNotFoundError(f"规范文件不存在: {filename}")

    target_dir.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        raise FileExistsError(f"目标分类下已存在同名文件: {filename}")

    try:
        shutil.move(str(source_path), str(target_path))
    except Exception as exc:
        logger.error(f"[regulation_category] 移动文件失败: {exc}")
        raise RuntimeError(f"移动文件失败: {exc}") from exc

    return {
        "old_category": old_category,
        "new_category": target_category,
        "filename": filename,
        "new_path": str(target_path),
        "message": "规范文件已移动到新分类",
    }


async def update_category_in_db(db: AsyncSession, filename: str, new_category: str) -> int:
    """更新数据库 regulation_chunks 表中该规范的 category 字段。

    返回受影响行数。
    """
    result = await db.execute(
        update(RegulationChunk)
        .where(RegulationChunk.doc_name == filename)
        .values(category=new_category)
    )
    await db.commit()
    return result.rowcount or 0


async def get_category_stats_from_db(db: AsyncSession) -> list[dict]:
    """从数据库 regulation_chunks 聚合分类统计（补充文件系统统计）"""
    result = await db.execute(
        select(RegulationChunk.category, func.count(RegulationChunk.id))
        .group_by(RegulationChunk.category)
    )
    db_stats = {row[0]: row[1] for row in result.all()}

    # 合并：以文件系统统计为主，数据库为补充
    fs_stats = list_categories()
    for item in fs_stats:
        item["db_chunks"] = db_stats.get(item["category"], 0)
    return fs_stats
