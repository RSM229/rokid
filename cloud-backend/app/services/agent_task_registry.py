"""Agent 异步任务登记与状态机（只管状态，不负责调度）。

设计要点
--------
* 所有 DB 操作走 **独立会话** ``app.db.async_session``：后台 runner 不能用请求
  的 ``Depends(get_db)`` 会话，那个会话在响应结束时会关闭。
* 任何写操作出错都静默降级（表没迁移 / DB 抖动时聊天不能挂），返回 False。
* 状态机是纯函数 ``can_transition``，便于单测；终态不可再变（除 retry）。
* 进程内 ``_LIVE_TASKS`` 只用于「取消正在运行的协程」，重启即失效；
  真正的状态以数据库为准。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select

from app.db import async_session
from app.models.agent_task import AgentAsyncTask

logger = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
TIMED_OUT = "timed_out"

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    QUEUED: {RUNNING, CANCELLED, TIMED_OUT, FAILED},
    RUNNING: {DONE, FAILED, CANCELLED, TIMED_OUT},
    DONE: set(),
    FAILED: {QUEUED},
    CANCELLED: {QUEUED},
    TIMED_OUT: {QUEUED},
}

_LIVE_TASKS: dict[str, asyncio.Task] = {}


def timeout_seconds() -> int:
    try:
        return max(30, int(os.getenv("AGENT_TASK_TIMEOUT_SECONDS", "300")))
    except ValueError:
        return 300


def can_transition(src: str, dst: str) -> bool:
    """状态机唯一真源：终态不可变；重试只从 failed/cancelled/timed_out 回 queued。"""
    return dst in ALLOWED_TRANSITIONS.get(str(src or ""), set())


def register_live(task_key: str | None, task: asyncio.Task) -> None:
    if task_key:
        _LIVE_TASKS[task_key] = task


def drop_live(task_key: str | None) -> None:
    if task_key:
        _LIVE_TASKS.pop(task_key, None)


def cancel_live(task_key: str) -> bool:
    task = _LIVE_TASKS.get(task_key)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


def _load(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def to_public(row: AgentAsyncTask) -> dict[str, Any]:
    """对外视图：不返回 request 原文（含用户消息），避免列表接口泄露正文。"""
    return {
        "task_key": row.task_key,
        "kind": row.kind,
        "status": row.status,
        "conversation_id": row.conversation_id,
        "retry_count": int(row.retry_count or 0),
        "error": row.error,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
        "started_at": row.started_at.isoformat(timespec="seconds") if row.started_at else None,
        "finished_at": row.finished_at.isoformat(timespec="seconds") if row.finished_at else None,
        "result": _load(row.result_json),
    }


async def _mutate(task_key: str, fn: Callable[[AgentAsyncTask], bool]) -> bool:
    """在独立会话里改一行；任何异常都只记日志，不向上抛。"""
    try:
        async with async_session() as db:
            row = await db.scalar(
                select(AgentAsyncTask).where(AgentAsyncTask.task_key == task_key)
            )
            if row is None:
                return False
            if not fn(row):
                return False
            await db.commit()
            return True
    except Exception as exc:  # noqa: BLE001 - 任务登记不能影响业务
        logger.warning("agent task mutate failed (%s): %s", task_key, type(exc).__name__)
        return False


async def create_task(
    *, user_id: int, kind: str = "agent_chat", request: dict[str, Any] | None = None,
    project_id: int | None = None, conversation_id: str | None = None,
) -> str | None:
    task_key = uuid.uuid4().hex
    now = datetime.now()
    try:
        async with async_session() as db:
            db.add(AgentAsyncTask(
                task_key=task_key, user_id=int(user_id), project_id=project_id,
                conversation_id=(conversation_id or None),
                kind=str(kind or "agent_chat")[:32], status=QUEUED,
                request_json=_dump(request or {}), created_at=now,
                lease_until=now + timedelta(seconds=timeout_seconds()),
            ))
            await db.commit()
            return task_key
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent task create failed (table missing?): %s", type(exc).__name__)
        return None


async def mark_running(task_key: str | None) -> bool:
    if not task_key:
        return False
    now = datetime.now()

    def _apply(row: AgentAsyncTask) -> bool:
        if not can_transition(row.status, RUNNING):
            return False
        row.status = RUNNING
        row.started_at = now
        row.lease_until = now + timedelta(seconds=timeout_seconds())
        return True

    return await _mutate(task_key, _apply)


async def finish_task(task_key: str | None, result: dict[str, Any] | None) -> bool:
    if not task_key:
        return False
    payload = _dump(result or {})
    now = datetime.now()

    def _apply(row: AgentAsyncTask) -> bool:
        if not can_transition(row.status, DONE):
            return False
        row.status = DONE
        row.result_json = payload
        row.error = None
        row.finished_at = now
        return True

    return await _mutate(task_key, _apply)


async def fail_task(task_key: str | None, error: str) -> bool:
    if not task_key:
        return False
    text = str(error or "未知错误")[:500]
    now = datetime.now()

    def _apply(row: AgentAsyncTask) -> bool:
        if not can_transition(row.status, FAILED):
            return False
        row.status = FAILED
        row.error = text
        row.finished_at = now
        return True

    return await _mutate(task_key, _apply)


async def cancel_task(task_key: str, user_id: int) -> tuple[bool, str]:
    """取消排队/运行中的任务；终态返回 409 语义。"""
    try:
        async with async_session() as db:
            row = await db.scalar(
                select(AgentAsyncTask).where(
                    AgentAsyncTask.task_key == task_key,
                    AgentAsyncTask.user_id == int(user_id),
                )
            )
            if row is None:
                return False, "not_found"
            if not can_transition(row.status, CANCELLED):
                return False, "already_finished"
            row.status = CANCELLED
            row.finished_at = datetime.now()
            row.error = "用户取消"
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent task cancel failed: %s", type(exc).__name__)
        return False, "error"
    cancel_live(task_key)
    return True, "ok"


async def retry_task(task_key: str, user_id: int) -> tuple[bool, str, dict[str, Any] | None]:
    """重置为 queued 并返回原始请求供客户端重放；**不自动重跑**（防重复建档）。"""
    try:
        async with async_session() as db:
            row = await db.scalar(
                select(AgentAsyncTask).where(
                    AgentAsyncTask.task_key == task_key,
                    AgentAsyncTask.user_id == int(user_id),
                )
            )
            if row is None:
                return False, "not_found", None
            if not can_transition(row.status, QUEUED):
                return False, "not_retryable", None
            row.status = QUEUED
            row.retry_count = int(row.retry_count or 0) + 1
            row.error = None
            row.result_json = None
            row.finished_at = None
            row.lease_until = datetime.now() + timedelta(seconds=timeout_seconds())
            replay = _load(row.request_json)
            await db.commit()
            return True, "ok", replay if isinstance(replay, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent task retry failed: %s", type(exc).__name__)
        return False, "error", None


async def get_task(task_key: str, user_id: int) -> dict[str, Any] | None:
    try:
        async with async_session() as db:
            row = await db.scalar(
                select(AgentAsyncTask).where(
                    AgentAsyncTask.task_key == task_key,
                    AgentAsyncTask.user_id == int(user_id),
                )
            )
            return to_public(row) if row is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent task get failed: %s", type(exc).__name__)
        return None


async def list_tasks(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    try:
        async with async_session() as db:
            rows = (await db.scalars(
                select(AgentAsyncTask)
                .where(AgentAsyncTask.user_id == int(user_id))
                .order_by(AgentAsyncTask.id.desc())
                .limit(max(1, min(int(limit or 20), 100)))
            )).all()
            return [to_public(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent task list failed: %s", type(exc).__name__)
        return []


async def sweep_stale(now: datetime | None = None) -> list[str]:
    """60s 守护调用：把租约过期且仍在 queued/running 的任务判超时。

    返回被判超时的 task_key 列表；进程内协程会一并 cancel，避免僵尸任务。
    """
    now = now or datetime.now()
    try:
        async with async_session() as db:
            rows = (await db.scalars(
                select(AgentAsyncTask).where(
                    AgentAsyncTask.status.in_((QUEUED, RUNNING)),
                    AgentAsyncTask.lease_until.is_not(None),
                    AgentAsyncTask.lease_until < now,
                )
            )).all()
            keys: list[str] = []
            for row in rows:
                if not can_transition(row.status, TIMED_OUT):
                    continue
                row.status = TIMED_OUT
                row.error = "执行超时（租约到期）"
                row.finished_at = now
                keys.append(row.task_key)
            if keys:
                await db.commit()
            for key in keys:
                cancel_live(key)
            return keys
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent task sweep failed: %s", type(exc).__name__)
        return []
