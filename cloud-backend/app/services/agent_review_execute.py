"""Execute a confirmed review plan as formal work records and documents."""
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


FORM_RECORD_TYPES = {
    "gd_b_212_pangzhan": "旁站",
    "gd_b_213_pingxing": "验收",
    "gd_b_214_xunshi": "巡视",
    "acceptance_record": "验收",
    "gdaq_30306_weida": "巡视",
}
FORM_PERMISSIONS = {
    "gd_b_212_pangzhan": "station",
    "gd_b_213_pingxing": "acceptance",
    "gd_b_214_xunshi": "inspection",
    "acceptance_record": "acceptance",
    "gdaq_30306_weida": "inspection",
}


def _media_ids_by_evidence(plan: dict[str, Any]) -> dict[str, int]:
    return {
        str(item["id"]): int(item["media_id"])
        for item in plan.get("evidence", [])
        if isinstance(item, dict) and item.get("media_id") is not None
    }


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

    pairs = [(event, form) for event in plan["events"] for form in event["forms"]]
    unsupported = sorted({form for _event, form in pairs if form not in FORM_RECORD_TYPES})
    if unsupported:
        raise HTTPException(422, {
            "message": "当前正式记录执行链路暂不支持所选表式",
            "unsupported_forms": unsupported,
        })
    for _event, form in pairs:
        permission = FORM_PERMISSIONS[form]
        if not user.has_permission(permission):
            raise HTTPException(403, f"当前角色无权创建{permission}类记录")

    project_id = getattr(user, "project_id", None)
    if not project_id or row.project_id != project_id:
        raise HTTPException(403, "整理计划不属于当前项目")
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

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "当前项目不存在")

    results: list[dict[str, Any]] = []
    from app.routers.form_doc import generate_form_from_record

    for event, form in pairs:
        record_type = FORM_RECORD_TYPES[form]
        event_media_ids = [
            media_by_evidence[evidence_id]
            for evidence_id in event["evidence_ids"]
            if evidence_id in media_by_evidence
        ]
        urls = [media_by_id[mid].processed_path or media_by_id[mid].original_path for mid in event_media_ids]
        content_json = {
            "source": "agent_review_plan",
            "review_plan_id": row.id,
            "review_plan_revision": expected_revision,
            "event_id": event["id"],
            "form_code": form,
            "title": event["title"],
            "request_text": plan["request_text"],
            "event_content": event["content"],
            "media_ids": event_media_ids,
            "evidence_ids": list(event["evidence_ids"]),
            "field_sources": {
                "record_date": "review_plan_confirmed",
                "section": "review_plan_confirmed",
                "work_content": "user_message_or_review_plan",
            },
        }
        record = WorkRecord(
            record_type=record_type,
            status=WorkRecordStatus.DRAFT.value,
            project_id=project_id,
            creator_id=user.id,
            section=event.get("location") or None,
            location=event.get("location") or None,
            work_content=event.get("content") or event.get("title") or None,
            record_date=date.fromisoformat(event["date"]),
            inspection_remark=event.get("content") or None if record_type == "巡视" else None,
            station_content=event.get("content") or None if record_type == "旁站" else None,
            inspection_photos=urls if record_type != "见证" else None,
            content_json=content_json,
        )
        db.add(record)
        await db.flush()
        for media_id in event_media_ids:
            media = media_by_id[media_id]
            if media.business_id is None:
                media.business_type = "work_record"
                media.business_id = record.id
        document, spec, _content = await generate_form_from_record(
            db, user, project, record, form_code=form,
        )
        results.append({
            "event_id": event["id"], "form_code": form, "form_name": spec.name,
            "record_id": record.id, "document_id": document.id,
            "document_title": document.title, "media_ids": event_media_ids,
        })

    result = {
        "plan_id": row.id,
        "revision": expected_revision,
        "created_count": len(results),
        "records": results,
    }
    row.status = "executed"
    row.executed_revision = expected_revision
    row.execution_result_json = json.dumps(result, ensure_ascii=False)
    row.updated_at = datetime.now()
    await db.commit()
    return result
