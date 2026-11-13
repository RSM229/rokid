"""Agent 异步任务查询/取消/重试端点（与 /api/agent 同前缀）。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.user import User
from app.routers.auth import get_current_user
from app.services import agent_task_registry as registry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/tasks")
async def list_agent_tasks(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    """当前用户的后台任务（最新在前），用于「说一句就走」后回来查结果。"""
    items = await registry.list_tasks(int(user.id), limit)
    return {"items": items, "total": len(items)}


@router.get("/quality-guard/latest")
async def latest_quality_guard(user: User = Depends(get_current_user)):
    """最近一次「每日待补汇总 / 每周冒烟」结果（系统态任务，按 kind 直取）。"""
    from sqlalchemy import select
    from app.db import async_session
    from app.models.agent_task import AgentAsyncTask

    out: dict[str, Any] = {"missing_summary": None, "smoke_check": None}
    try:
        async with async_session() as db:
            for kind in ("missing_summary", "smoke_check"):
                row = await db.scalar(
                    select(AgentAsyncTask)
                    .where(AgentAsyncTask.kind == kind)
                    .order_by(AgentAsyncTask.id.desc())
                    .limit(1)
                )
                if row is not None:
                    out[kind] = registry.to_public(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("quality guard latest failed: %s", type(exc).__name__)
    return out


@router.get("/tasks/{task_key}")
async def get_agent_task(
    task_key: str,
    user: User = Depends(get_current_user),
):
    item = await registry.get_task(task_key, int(user.id))
    if item is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return item


@router.post("/tasks/{task_key}/cancel")
async def cancel_agent_task(
    task_key: str,
    user: User = Depends(get_current_user),
):
    ok, reason = await registry.cancel_task(task_key, int(user.id))
    if not ok:
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="任务不存在")
        if reason == "already_finished":
            raise HTTPException(status_code=409, detail="任务已结束，不能取消")
        raise HTTPException(status_code=503, detail="任务服务暂不可用")
    return {"ok": True, "status": "cancelled"}


@router.post("/tasks/{task_key}/retry")
async def retry_agent_task(
    task_key: str,
    user: User = Depends(get_current_user),
):
    """重置为 queued 并返回原始请求；**不自动重跑**（防重复建档）。"""
    ok, reason, replay = await registry.retry_task(task_key, int(user.id))
    if not ok:
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="任务不存在")
        if reason == "not_retryable":
            raise HTTPException(status_code=409, detail="该任务状态不能重试")
        raise HTTPException(status_code=503, detail="任务服务暂不可用")
    return {"ok": True, "status": "queued", "replay": replay}
