"""公司级看板路由（P4 看板分层，穗建通上架路线图）。

GET /api/stats/company —— 跨项目横向对比聚合（管理员/总监视角）：
- projects 表无 status 字段，项目状态按业务数据推导：
  * 竣工：最新形象进度 actual_percent >= 100
  * 在建：有进度记录或隐患记录（尚未竣工）
  * 未开工：无任何进度/隐患记录
- 整改率 = 已闭环隐患 / 隐患总数 * 100（无隐患时为 None）
- 最新形象进度取该项目 record_date 最新的 progress_record.actual_percent
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.db import get_db
from app.models.hazard import Hazard, HazardStatus
from app.models.progress import ProgressRecord
from app.models.user import Project, User
from app.routers.auth import require_permission

router = APIRouter()

CLOSED_STATUS = HazardStatus.CLOSED.value


class CompanyProjectStat(BaseModel):
    """单个项目的对比指标"""
    id: int
    name: str
    status: str
    hazard_total: int
    hazard_pending: int
    rectify_rate: Optional[float] = None
    latest_progress: Optional[int] = None


class CompanyStats(BaseModel):
    """公司级汇总指标"""
    total_projects: int
    active_projects: int
    total_pending_hazards: int
    avg_rectify_rate: Optional[float] = None
    projects: list[CompanyProjectStat]


@router.get("/company", response_model=CompanyStats)
async def company_stats(
    user: User = Depends(require_permission("task_assign")),
    db: AsyncSession = Depends(get_db),
):
    """公司级多项目横向对比：在建数 / 隐患数 / 整改率排名。"""
    projects = (
        (await db.execute(select(Project).order_by(Project.created_at.desc())))
        .scalars()
        .all()
    )

    # 隐患按项目聚合：总数 / 已闭环数
    hazard_rows = (
        await db.execute(
            select(
                Hazard.project_id,
                func.count(Hazard.id).label("total"),
                func.sum(
                    case((Hazard.status == CLOSED_STATUS, 1), else_=0)
                ).label("closed"),
            ).group_by(Hazard.project_id)
        )
    ).all()
    hazard_map = {row.project_id: (row.total or 0, row.closed or 0) for row in hazard_rows}

    # 最新形象进度：按 record_date 倒序取每个项目的第一条
    progress_rows = (
        await db.execute(
            select(
                ProgressRecord.project_id,
                ProgressRecord.actual_percent,
            ).order_by(
                ProgressRecord.record_date.desc(),
                ProgressRecord.id.desc(),
            )
        )
    ).all()
    latest_progress_map: dict[int, int] = {}
    for row in progress_rows:
        if row.project_id is not None and row.project_id not in latest_progress_map:
            latest_progress_map[row.project_id] = row.actual_percent or 0

    items: list[CompanyProjectStat] = []
    active_projects = 0
    total_pending_hazards = 0
    rates: list[float] = []

    for p in projects:
        hazard_total, hazard_closed = hazard_map.get(p.id, (0, 0))
        hazard_pending = hazard_total - hazard_closed
        total_pending_hazards += hazard_pending

        latest_progress = latest_progress_map.get(p.id)
        has_activity = p.id in hazard_map or p.id in latest_progress_map

        if latest_progress is not None and latest_progress >= 100:
            status = "竣工"
        elif has_activity:
            status = "在建"
        else:
            status = "未开工"
        if status == "在建":
            active_projects += 1

        rectify_rate = None
        if hazard_total > 0:
            rectify_rate = round(100.0 * hazard_closed / hazard_total, 1)
            rates.append(rectify_rate)

        items.append(
            CompanyProjectStat(
                id=p.id,
                name=p.name,
                status=status,
                hazard_total=hazard_total,
                hazard_pending=hazard_pending,
                rectify_rate=rectify_rate,
                latest_progress=latest_progress,
            )
        )

    avg_rectify_rate = round(sum(rates) / len(rates), 1) if rates else None

    return CompanyStats(
        total_projects=len(items),
        active_projects=active_projects,
        total_pending_hazards=total_pending_hazards,
        avg_rectify_rate=avg_rectify_rate,
        projects=items,
    )
