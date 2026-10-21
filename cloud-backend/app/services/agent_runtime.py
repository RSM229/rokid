"""Request tracing and durable execution helpers for agent-owned writes."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.models.agent_draft import AgentFlowInstance, AgentRequestTrace

logger = logging.getLogger(__name__)

_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")
_trace_write_unavailable = False
_trace_retry_after = 0.0
_TRACE_RETRY_SECONDS = 30.0


def _trace_suppressed() -> bool:
    return _trace_write_unavailable and time.monotonic() < _trace_retry_after


def _mark_trace_unavailable() -> None:
    global _trace_write_unavailable, _trace_retry_after
    _trace_write_unavailable = True
    _trace_retry_after = time.monotonic() + _TRACE_RETRY_SECONDS


def _mark_trace_available() -> None:
    global _trace_write_unavailable, _trace_retry_after
    _trace_write_unavailable = False
    _trace_retry_after = 0.0


def normalize_request_id(value: Any = None, prefix: str = "req") -> str:
    """Return a bounded, URL-safe operation id; never trust client text raw."""
    text = _ID_SAFE.sub("-", str(value or "").strip()).strip("-.:")[:80]
    if text:
        return text
    return f"{prefix}-{uuid.uuid4().hex}"


def normalize_execution_id(value: Any = None, prefix: str = "exec") -> str:
    text = _ID_SAFE.sub("-", str(value or "").strip()).strip("-.:")[:160]
    if text:
        return text
    return f"{prefix}-{uuid.uuid4().hex}"


def review_execution_id(plan_id: int, revision: int) -> str:
    """Stable idempotency key for one confirmed review-plan revision."""
    return f"review-plan:{int(plan_id)}:revision:{int(revision)}"


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _excerpt(value: Any, limit: int = 1000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _stage_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "_", value.lower())[:64] or "stage"


def _append_stage(
    stages: list[dict[str, Any]],
    name: str,
    endpoint: str,
    when: datetime,
    status: str = "running",
    detail: Any = None,
) -> list[dict[str, Any]]:
    item = {
        "name": name,
        "endpoint": endpoint,
        "status": status,
        "started_at": when.isoformat(timespec="milliseconds"),
        "finished_at": "",
        "duration_ms": None,
        "detail": detail or {},
    }
    return [*stages, item]


def _finish_stage(
    stages: list[dict[str, Any]],
    name: str,
    endpoint: str,
    when: datetime,
    status: str,
    detail: Any = None,
) -> list[dict[str, Any]]:
    updated = [dict(item) for item in stages]
    target: dict[str, Any] | None = None
    for item in reversed(updated):
        if item.get("name") == name and item.get("endpoint") == endpoint and item.get("status") == "running":
            target = item
            break
    if target is None:
        updated.append({
            "name": name, "endpoint": endpoint, "status": status,
            "started_at": when.isoformat(timespec="milliseconds"),
            "finished_at": when.isoformat(timespec="milliseconds"),
            "duration_ms": 0, "detail": detail or {},
        })
        return updated
    target["status"] = status
    target["finished_at"] = when.isoformat(timespec="milliseconds")
    if detail:
        target["detail"] = detail
    try:
        started = datetime.fromisoformat(str(target.get("started_at") or ""))
        target["duration_ms"] = max(0, int((when - started).total_seconds() * 1000))
    except (TypeError, ValueError):
        target["duration_ms"] = None
    return updated


def trace_payload(row: AgentRequestTrace) -> dict[str, Any]:
    return {
        "request_id": row.request_id,
        "endpoint": row.endpoint,
        "status": row.status,
        "current_stage": row.current_stage,
        "message_excerpt": row.message_excerpt,
        "provider": row.provider,
        "model": row.model,
        "request_plan": _loads(row.request_plan_json, {}),
        "stages": _loads(row.stages_json, []),
        "result": _loads(row.result_json, {}),
        "error_stage": row.error_stage,
        "error_message": row.error_message,
        "review_plan_id": row.review_plan_id,
        "created_record_refs": _loads(row.created_record_refs_json, []),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "duration_ms": row.duration_ms,
    }


async def start_request(
    *,
    request_id: Any,
    user_id: int,
    project_id: int | None,
    conversation_id: str | None,
    endpoint: str,
    stage: str,
    message: Any = "",
    provider: str = "",
    model: str = "",
    session_factory: Callable[[], Any] | None = None,
) -> str:
    """Start or append an operation trace using a session isolated from business work."""
    rid = normalize_request_id(request_id)
    if _trace_suppressed():
        return rid
    now = datetime.now()
    stage_key = _stage_name(stage)
    factory = session_factory or async_session
    try:
        async with factory() as db:
            row = (await db.execute(
                select(AgentRequestTrace).where(AgentRequestTrace.request_id == rid)
            )).scalars().first()
            if row and row.user_id != user_id:
                logger.warning("拒绝复用其他用户的 request_id=%s", rid)
                return rid
            if row is None:
                row = AgentRequestTrace(
                    request_id=rid,
                    user_id=user_id,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    endpoint=endpoint[:80],
                    status="running",
                    current_stage=stage_key,
                    message_excerpt=_excerpt(message),
                    provider=provider[:80],
                    model=model[:160],
                    started_at=now,
                    updated_at=now,
                )
                db.add(row)
                stages: list[dict[str, Any]] = []
            else:
                stages = _loads(row.stages_json, [])
                row.endpoint = endpoint[:80]
                row.status = "running"
                row.current_stage = stage_key
                row.updated_at = now
                row.finished_at = None
                row.duration_ms = None
                if project_id is not None:
                    row.project_id = project_id
                if conversation_id:
                    row.conversation_id = conversation_id
                if provider:
                    row.provider = provider[:80]
                if model:
                    row.model = model[:160]
                if not row.message_excerpt and message:
                    row.message_excerpt = _excerpt(message)
            row.stages_json = _json(_append_stage(stages, stage_key, endpoint[:80], now))
            await db.commit()
            _mark_trace_available()
    except (SQLAlchemyError, OSError) as exc:
        if not _trace_suppressed():
            logger.warning("请求追踪写入不可用，业务请求继续执行：%s", exc)
        _mark_trace_unavailable()
    except Exception:  # noqa: BLE001
        logger.exception("请求追踪意外失败，业务请求继续执行")
    return rid


async def finish_request(
    *,
    request_id: str,
    user_id: int,
    endpoint: str,
    stage: str,
    status: str = "completed",
    request_plan: Any = None,
    result: Any = None,
    error: Any = None,
    review_plan_id: int | None = None,
    created_record_refs: Any = None,
    provider: str = "",
    model: str = "",
    session_factory: Callable[[], Any] | None = None,
) -> None:
    """Finish a trace stage without ever re-raising into the business request."""
    if _trace_suppressed():
        return
    now = datetime.now()
    stage_key = _stage_name(stage)
    factory = session_factory or async_session
    try:
        async with factory() as db:
            row = (await db.execute(
                select(AgentRequestTrace).where(AgentRequestTrace.request_id == request_id)
            )).scalars().first()
            if row is None or row.user_id != user_id:
                return
            detail: dict[str, Any] = {}
            if isinstance(result, dict):
                detail["result_kind"] = str(result.get("kind") or result.get("status") or "")
                detail["created_count"] = int(result.get("created_count") or 0)
            stages = _loads(row.stages_json, [])
            row.stages_json = _json(_finish_stage(stages, stage_key, endpoint[:80], now, status, detail))
            row.status = "failed" if status == "failed" else "completed"
            row.current_stage = stage_key
            row.updated_at = now
            row.finished_at = now
            row.duration_ms = max(0, int((now - row.started_at).total_seconds() * 1000))
            if request_plan is not None:
                row.request_plan_json = _json(request_plan)
            if result is not None:
                row.result_json = _json(result)
            if error is not None:
                row.error_stage = stage_key
                row.error_message = _excerpt(error, 4000)
            if review_plan_id is not None:
                row.review_plan_id = review_plan_id
            if created_record_refs is not None:
                row.created_record_refs_json = _json(created_record_refs)
            if provider:
                row.provider = provider[:80]
            if model:
                row.model = model[:160]
            await db.commit()
            _mark_trace_available()
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("请求追踪收尾失败，已忽略：%s", exc)
    except Exception:  # noqa: BLE001
        logger.exception("请求追踪收尾意外失败，已忽略")


async def get_request_trace(db: AsyncSession, request_id: str, user_id: int) -> AgentRequestTrace | None:
    return (await db.execute(select(AgentRequestTrace).where(
        AgentRequestTrace.request_id == request_id,
        AgentRequestTrace.user_id == user_id,
    ))).scalars().first()


async def list_request_traces(
    db: AsyncSession, user_id: int, project_id: int | None, limit: int = 20,
) -> list[AgentRequestTrace]:
    stmt = select(AgentRequestTrace).where(AgentRequestTrace.user_id == user_id)
    if project_id is not None:
        stmt = stmt.where(AgentRequestTrace.project_id == project_id)
    return list((await db.execute(
        stmt.order_by(AgentRequestTrace.updated_at.desc()).limit(max(1, min(limit, 100)))
    )).scalars().all())


def execution_matches(
    row: AgentFlowInstance,
    *,
    user_id: int,
    project_id: int,
    flow_type: str,
    scope_id: str,
    expected_revision: int | None,
) -> bool:
    return (
        row.user_id == user_id
        and row.project_id == project_id
        and row.flow_type == flow_type
        and row.scope_id == scope_id
        and row.expected_revision == expected_revision
    )


async def prepare_execution(
    db: AsyncSession,
    *,
    execution_id: str,
    request_id: str | None,
    flow_type: str,
    scope_id: str,
    user_id: int,
    project_id: int,
    conversation_id: str | None = None,
    expected_revision: int | None = None,
) -> AgentFlowInstance:
    """Commit a durable RUNNING row before the caller locks business rows."""
    row = (await db.execute(select(AgentFlowInstance).where(
        AgentFlowInstance.execution_id == execution_id,
    ))).scalars().first()
    if row is None:
        row = AgentFlowInstance(
            execution_id=execution_id,
            request_id=request_id,
            flow_type=flow_type,
            scope_id=scope_id,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            expected_revision=expected_revision,
            status="RUNNING",
            stage="PREPARED",
        )
        db.add(row)
        try:
            await db.commit()
            await db.refresh(row)
            return row
        except IntegrityError:
            # Another worker won the unique-key race. Its row is authoritative.
            await db.rollback()
            row = (await db.execute(select(AgentFlowInstance).where(
                AgentFlowInstance.execution_id == execution_id,
            ))).scalars().first()
    if not row or not execution_matches(
        row, user_id=user_id, project_id=project_id, flow_type=flow_type,
        scope_id=scope_id, expected_revision=expected_revision,
    ):
        raise ValueError("execution_id 已被其他执行占用，请重新发起确认")
    return row


async def lock_execution(db: AsyncSession, execution_id: str) -> AgentFlowInstance | None:
    # ``prepare_execution`` may already have loaded this row in the same
    # session. A second worker can commit COMMITTED while this request waits
    # for the row lock, so the identity-map copy must be refreshed; otherwise
    # the waiter could observe a stale RUNNING state and write the batch twice.
    return (await db.execute(
        select(AgentFlowInstance)
        .where(AgentFlowInstance.execution_id == execution_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().first()


def mark_execution_running(row: AgentFlowInstance, stage: str = "EXECUTING") -> None:
    row.status = "RUNNING"
    row.stage = stage[:64]
    row.attempts = int(row.attempts or 0) + 1
    row.result_json = "{}"
    row.error_message = ""
    row.updated_at = datetime.now()
    row.finished_at = None


def mark_execution_committed(row: AgentFlowInstance, result: dict[str, Any]) -> None:
    now = datetime.now()
    row.status = "COMMITTED"
    row.stage = "COMMITTED"
    row.result_json = _json(result)
    row.error_message = ""
    row.updated_at = now
    row.finished_at = now


def execution_result(row: AgentFlowInstance) -> dict[str, Any]:
    value = _loads(row.result_json, {})
    return value if isinstance(value, dict) else {}


def flow_payload(row: AgentFlowInstance) -> dict[str, Any]:
    return {
        "execution_id": row.execution_id,
        "request_id": row.request_id,
        "flow_type": row.flow_type,
        "scope_id": row.scope_id,
        "expected_revision": row.expected_revision,
        "status": row.status,
        "stage": row.stage,
        "attempts": row.attempts,
        "result": execution_result(row),
        "error": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


async def mark_execution_failed(
    *, execution_id: str, error: Any, user_id: int, project_id: int,
    stage: str = "FAILED",
    session_factory: Callable[[], Any] | None = None,
) -> None:
    """Record a known failure in a fresh transaction after business rollback."""
    factory = session_factory or async_session
    try:
        async with factory() as db:
            row = (await db.execute(select(AgentFlowInstance).where(
                AgentFlowInstance.execution_id == execution_id,
                AgentFlowInstance.user_id == user_id,
                AgentFlowInstance.project_id == project_id,
            ))).scalars().first()
            if row is None or row.status == "COMMITTED":
                return
            row.status = "FAILED"
            row.stage = stage[:64]
            row.error_message = _excerpt(error, 4000)
            row.updated_at = datetime.now()
            row.finished_at = datetime.now()
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("记录执行失败状态时出错，已在业务异常路径中忽略")
