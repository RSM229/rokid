"""Execute a confirmed review plan as formal work records and documents."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_draft import AgentReviewPlan
from app.models.document import DocStatus, Document
from app.models.miniprogram import MediaRecord, MediaType
from app.models.user import Project, User
from app.models.work_record import WorkRecord, WorkRecordStatus
from app.models.parallel import ParallelInspection
from app.services.form_renderer import render_form
from app.services.record_fields import project_fields
from app.services import agent_review_plan
from app.services.form_registry import FORMS


RECORD_TYPES = {"inspection": "巡视", "acceptance": "验收", "witness": "见证", "station": "旁站"}
RECORD_PERMISSIONS = {"巡视": "inspection", "验收": "acceptance", "见证": "witness", "旁站": "station"}
FORM_RECORD_TYPES = {
    spec.form_code: RECORD_TYPES[record_type]
    for spec in FORMS
    if spec.source == "record"
    for record_type in spec.record_types
    if record_type in RECORD_TYPES
}
PARALLEL_FORM = "gd_b_213_pingxing"
FORM_PERMISSIONS = {PARALLEL_FORM: "inspection"}


def _media_ids_by_evidence(plan: dict[str, Any]) -> dict[str, int]:
    return {
        str(item["id"]): int(item["media_id"])
        for item in plan.get("evidence", [])
        if isinstance(item, dict) and item.get("media_id") is not None
    }


def _event_fields(event: dict[str, Any], form: str) -> dict[str, Any]:
    """Accept schema keys and canonical keys while keeping the raw values auditable."""
    raw = event.get("fields") or {}
    values = raw.get(form) if isinstance(raw, dict) else {}
    if not isinstance(values, dict):
        return {}
    from app.services.provform_schema import CONTENT_MAP
    mapped = dict(values)
    for key, value in values.items():
        mapped.setdefault(CONTENT_MAP.get(form, {}).get(key, key), value)
    return mapped


def _validated_event_fields(event: dict[str, Any], form: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    raw = event.get("fields") or {}
    values = dict(raw.get(form) or {}) if isinstance(raw, dict) and isinstance(raw.get(form), dict) else {}
    defaults = {
        "gd_b_212_pangzhan": {"1_05": event.get("location"), "1_15": event.get("content")},
        "gd_b_214_xunshi": {"3_03": event.get("location"), "3_07": event.get("content")},
        "acceptance_record": {"date": event.get("date"), "6_05": event.get("location"), "6_06": event.get("content")},
        "gdaq_30306_weida": {"10_03": event.get("date"), "10_06": event.get("location"), "10_13": event.get("content")},
        PARALLEL_FORM: {"2_03": event.get("date"), "2_04": event.get("location"), "2_05": event.get("title"), "2_10": event.get("content")},
    }.get(form, {})
    for key, value in defaults.items():
        if value not in (None, "") and values.get(key) in (None, ""):
            values[key] = value
    from app.services.provform_schema import validate_values
    validation = validate_values(form, values)
    format_errors = [error for error in validation.get("errors", []) if error.get("message") != "必填项不能为空"]
    if format_errors:
        raise HTTPException(422, {
            "message": f"{form} 的已填写字段格式无效",
            "event_id": event["id"],
            "form_code": form,
            "errors": format_errors,
        })
    mapped = _event_fields({**event, "fields": {form: values}}, form)
    pending = [error["label"] for error in validation.get("errors", []) if error.get("message") == "必填项不能为空"]
    return values, mapped, pending


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

    pairs = [(event, form) for event in plan["events"] for form in event["forms"]]
    unsupported = sorted({form for _event, form in pairs if form not in FORM_RECORD_TYPES})
    if unsupported:
        raise HTTPException(422, {
            "message": "当前正式记录执行链路暂不支持所选表式",
            "unsupported_forms": unsupported,
        })
    for _event, form in pairs:
        permission = FORM_PERMISSIONS.get(form) or RECORD_PERMISSIONS[FORM_RECORD_TYPES[form]]
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

    validated_fields: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any], list[str]]] = {}
    for event, form in pairs:
        validated_fields[(event["id"], form)] = _validated_event_fields(event, form)

    results: list[dict[str, Any]] = []
    from app.routers.form_doc import generate_form_from_record

    for event, form in pairs:
        if form == PARALLEL_FORM:
            raw_fields, fields, pending_fields = validated_fields[(event["id"], form)]
            part = str(fields.get("part") or event.get("location") or "").strip()
            item = str(fields.get("acceptance_content") or event.get("title") or event.get("content") or "").strip()
            result_detail = str(fields.get("result_detail") or event.get("content") or "").strip()
            if not part or not item or not result_detail:
                raise HTTPException(422, {
                    "message": "平行检验还缺少必填字段",
                    "event_id": event["id"],
                    "missing": [name for name, value in (("检查部位", part), ("检查项目", item), ("检查记录", result_detail)) if not value],
                })
            conclusion = str(fields.get("conclusion") or "").strip()
            if conclusion not in ("合格", "不合格"):
                raise HTTPException(422, {"message": "平行检验结论必须由用户确认合格或不合格", "event_id": event["id"]})
            parallel = ParallelInspection(
                user_id=user.id, project_id=project_id, inspect_date=event["date"],
                part=part, item=item, method=str(fields.get("method") or ""),
                basis=str(fields.get("basis") or ""), result_detail=result_detail,
                conclusion=conclusion, treatment=str(fields.get("handling") or ""),
            )
            db.add(parallel)
            await db.flush()
            content = {
                **project_fields(project), "doc_number": f"GD-B-213-{parallel.id:05d}",
                "date": event["date"], "time": event["date"], "section": part,
                "acceptance_content": item, "method": parallel.method or "", "basis": parallel.basis,
                "record": result_detail, "conclusion": conclusion, "handling": parallel.treatment,
            }
            document = Document(
                doc_type="平行检查记录", title=f"平行检查记录_{parallel.id}", content=content,
                form_code=PARALLEL_FORM, project_id=project_id, creator_id=user.id,
                status=DocStatus.DRAFT.value,
            )
            docx = render_form(PARALLEL_FORM, content)
            document.docx_path = f"/exports/{docx.name}"
            db.add(document)
            await db.flush()
            parallel.generated_doc_id = document.id
            event_media_ids = [media_by_evidence[evidence_id] for evidence_id in event["evidence_ids"] if evidence_id in media_by_evidence]
            document.attached_images = [media_by_id[mid].processed_path or media_by_id[mid].original_path for mid in event_media_ids]
            for media_id in event_media_ids:
                media = media_by_id[media_id]
                if media.business_id is None:
                    media.business_type = "parallel_inspection"
                    media.business_id = parallel.id
            results.append({"event_id": event["id"], "form_code": form, "form_name": "平行检验记录", "record_id": parallel.id, "document_id": document.id, "document_title": document.title, "media_ids": event_media_ids, "pending_fields": pending_fields})
            continue
        record_type = FORM_RECORD_TYPES[form]
        raw_fields, fields, pending_fields = validated_fields[(event["id"], form)]
        event_media_ids = [
            media_by_evidence[evidence_id]
            for evidence_id in event["evidence_ids"]
            if evidence_id in media_by_evidence
        ]
        urls = [media_by_id[mid].processed_path or media_by_id[mid].original_path for mid in event_media_ids]
        prov_fields = dict(fields)
        content_json = {
            "source": "agent_review_plan",
            "review_plan_id": row.id,
            "review_plan_revision": expected_revision,
            "event_id": event["id"],
            "form_code": form,
            "title": event["title"],
            "request_text": plan["request_text"],
            "event_content": event["content"],
            "acceptance_type": event["title"] if record_type == "验收" else None,
            "acceptance_content": event["content"] if record_type == "验收" else None,
            "record": event["content"],
            "media_ids": event_media_ids,
            "evidence_ids": list(event["evidence_ids"]),
            "prov_fields": prov_fields,
            "form_values": raw_fields,
            "field_sources": {
                "record_date": "review_plan_confirmed",
                "section": "review_plan_confirmed",
                "work_content": "user_message_or_review_plan",
            },
        }
        if form == "gdaq_30306_weida":
            content_json.update({
                "hazard_type": fields.get("hazard_name") or "",
                "hazard_category": fields.get("hazard_category") or "",
                "wind": fields.get("wind") or "",
                "subcontract_unit": fields.get("subcontractor") or "",
                "chief_supervisor": fields.get("pm_onsite") or "",
                "checklist_results": fields.get("checklist_results") or {},
            })
        record = WorkRecord(
            record_type=record_type,
            status=WorkRecordStatus.DRAFT.value,
            project_id=project_id,
            creator_id=user.id,
            section=event.get("location") or None,
            location=event.get("location") or None,
            work_content=event.get("content") or event.get("title") or None,
            record_date=date.fromisoformat(event["date"]),
            weather=str(fields.get("weather") or "")[:16] or None,
            worker_count=int(fields["worker_count"]) if str(fields.get("worker_count") or "").isdigit() else None,
            inspection_start_time=_event_datetime(fields.get("start_time"), event["date"]),
            inspection_end_time=_event_datetime(fields.get("end_time"), event["date"]),
            inspection_remark=event.get("content") or None if record_type == "巡视" else None,
            station_content=event.get("content") or None if record_type == "旁站" else None,
            station_location=str(fields.get("station_part") or fields.get("section") or event.get("location") or "")[:256] or None,
            station_process=str(fields.get("station_process") or "")[:256] or None,
            station_start_time=_event_datetime(fields.get("start_time"), event["date"]),
            station_end_time=_event_datetime(fields.get("end_time"), event["date"]),
            station_conclusion=str(fields.get("conclusion") or fields.get("problems") or "")[:2000] or None,
            acceptance_result=str(fields.get("acceptance_result") or fields.get("supervision_conclusion") or fields.get("conclusion") or "")[:32] or None,
            acceptance_participants=str(fields.get("acceptance_participants") or "") or None,
            acceptance_basis=str(fields.get("basis") or ""),
            problems=str(fields.get("problems") or ""),
            treatment=str(fields.get("treatment") or fields.get("handling") or ""),
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
            "pending_fields": pending_fields,
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
