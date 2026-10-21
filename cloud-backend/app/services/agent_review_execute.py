"""Execute a confirmed review plan as work records, without generating forms."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_draft import AgentReviewPlan
from app.models.miniprogram import MediaRecord, MediaType
from app.models.user import Project, User
from app.models.work_record import WorkRecord, WorkRecordStatus
from app.services import agent_review_plan
from app.services.record_fields import now_local

RECORD_PERMISSIONS = {"巡视": "inspection", "验收": "acceptance", "见证": "witness", "旁站": "station"}


def _media_ids_by_evidence(plan: dict[str, Any]) -> dict[str, int]:
    return {
        str(item["id"]): int(item["media_id"])
        for item in plan.get("evidence", [])
        if isinstance(item, dict) and item.get("media_id") is not None
    }


def _event_datetime(value: Any, event_date: str) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(f"{event_date} {text}")
        except ValueError:
            return None


async def execute_confirmed_plan(
    db: AsyncSession,
    row: AgentReviewPlan,
    user: User,
    expected_revision: int,
) -> dict[str, Any]:
    if row.status == "executed" and row.executed_revision == expected_revision:
        return json.loads(row.execution_result_json or "{}")
    if row.status != "confirmed" or row.confirmed_revision != expected_revision or row.revision != expected_revision:
        raise HTTPException(409, "整理计划尚未确认或版本已变化，请重新核对")

    plan = json.loads(row.plan_json or "{}")
    try:
        agent_review_plan.validate_plan(plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    issues = agent_review_plan.review_issues(plan)
    if issues:
        raise HTTPException(422, {"message": "整理计划仍有待确认项", "issues": issues})

    project_id = getattr(user, "project_id", None)
    if not project_id or row.project_id != project_id:
        raise HTTPException(403, "整理计划不属于当前项目")
    for event in plan["events"]:
        permission = RECORD_PERMISSIONS.get(event.get("record_type") or "待定")
        if permission and not user.has_permission(permission):
            raise HTTPException(403, f"当前角色无权创建{permission}类记录")

    media_by_evidence = _media_ids_by_evidence(plan)
    media_ids = list(dict.fromkeys(media_by_evidence.values()))
    media_rows = []
    if media_ids:
        media_rows = (await db.execute(select(MediaRecord).where(
            MediaRecord.id.in_(media_ids),
            MediaRecord.uploader_id == user.id,
            MediaRecord.project_id == project_id,
            MediaRecord.media_type == MediaType.IMAGE.value,
        ).with_for_update())).scalars().all()
    media_by_id = {item.id: item for item in media_rows}
    if set(media_by_id) != set(media_ids):
        raise HTTPException(403, "部分图片已不存在或不属于当前用户/项目")
    for media in media_rows:
        if media.business_id is not None:
            raise HTTPException(409, f"图片 {media.id} 已关联业务记录，不能重复建档")
    if not await db.get(Project, project_id):
        raise HTTPException(404, "当前项目不存在")

    results: list[dict[str, Any]] = []
    for event in plan["events"]:
        record_type = event.get("record_type") or "待定"
        event_media_ids = [media_by_evidence[item] for item in event["evidence_ids"] if item in media_by_evidence]
        urls = [media_by_id[mid].processed_path or media_by_id[mid].original_path for mid in event_media_ids]
        record_date = date.fromisoformat(event["date"]) if event.get("date") else now_local().date()
        fields = event.get("fields") or {}
        content_json = {
            "source": "agent_review_plan",
            "review_plan_id": row.id,
            "review_plan_revision": expected_revision,
            "event_id": event["id"],
            "title": event["title"],
            "request_text": plan["request_text"],
            "event_content": event["content"],
            "media_ids": event_media_ids,
            "evidence_ids": list(event["evidence_ids"]),
            "field_sources": event.get("field_sources") or {},
            "record_type_reason": event.get("record_type_reason") or "",
            "record_type_confidence": event.get("record_type_confidence", 0),
            "date_source": event.get("date_source") or ("event_date" if event.get("date") else "server_current_date"),
            "evidence_notes": [
                item.get("note", "") for item in plan.get("evidence", [])
                if item.get("id") in event["evidence_ids"] and item.get("note")
            ],
            "extracted_fields": fields if isinstance(fields, dict) else {},
        }
        record = WorkRecord(
            record_type=record_type,
            status=WorkRecordStatus.DRAFT.value,
            project_id=project_id,
            creator_id=user.id,
            section=event.get("location") or None,
            location=event.get("location") or None,
            work_content=event.get("content") or event.get("title") or None,
            record_date=record_date,
            weather=str(fields.get("weather") or "")[:16] or None,
            worker_count=int(fields["worker_count"]) if str(fields.get("worker_count") or "").isdigit() else None,
            inspection_start_time=_event_datetime(fields.get("start_time"), record_date.isoformat()),
            inspection_end_time=_event_datetime(fields.get("end_time"), record_date.isoformat()),
            inspection_remark=event.get("content") or None if record_type == "巡视" else None,
            station_content=event.get("content") or None if record_type == "旁站" else None,
            station_location=str(fields.get("station_part") or fields.get("section") or event.get("location") or "")[:256] or None,
            station_process=str(fields.get("station_process") or "")[:256] or None,
            station_start_time=_event_datetime(fields.get("start_time"), record_date.isoformat()),
            station_end_time=_event_datetime(fields.get("end_time"), record_date.isoformat()),
            station_conclusion=str(fields.get("conclusion") or fields.get("problems") or "")[:2000] or None,
            acceptance_result=str(fields.get("acceptance_result") or fields.get("supervision_conclusion") or fields.get("conclusion") or "")[:32] or None,
            acceptance_participants=str(fields.get("acceptance_participants") or "") or None,
            acceptance_basis=str(fields.get("basis") or ""),
            problems=str(fields.get("problems") or ""),
            treatment=str(fields.get("treatment") or fields.get("handling") or ""),
            inspection_photos=urls if record_type != "见证" else None,
            witness_photos=urls if record_type == "见证" else None,
            content_json=content_json,
        )
        db.add(record)
        await db.flush()
        for media_id in event_media_ids:
            media = media_by_id[media_id]
            media.business_type = "work_record"
            media.business_id = record.id
        results.append({
            "event_id": event["id"],
            "record_id": record.id,
            "record_type": record.record_type,
            "record_date": record.record_date.isoformat(),
            "media_ids": event_media_ids,
        })

    result = {"plan_id": row.id, "revision": expected_revision, "created_count": len(results), "records": results}
    row.status = "executed"
    row.executed_revision = expected_revision
    row.execution_result_json = json.dumps(result, ensure_ascii=False)
    row.updated_at = datetime.now()
    await db.commit()
    return result
