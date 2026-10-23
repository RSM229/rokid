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
from app.models.parallel import ParallelInspection
from app.models.work_record import WorkRecord, WorkRecordStatus
from app.services import agent_form_requirements, agent_review_plan, form_registry, record_field_spec, record_fields
from app.services.watermark_ocr import match_form_field

RECORD_PERMISSIONS = {
    "巡视": "inspection", "验收": "acceptance", "见证": "witness",
    "旁站": "station", "平行检验": "inspection",
    "起重机械安拆检查": "inspection",
}
WATERMARK_FIELD_ALIASES = {
    "项目": "project_name", "项目名称": "project_name", "工程名称": "project_name", "建设项目": "project_name",
    "建设单位名称": "owner_unit", "施工单位名称": "construction_unit", "监理单位名称": "supervision_unit",
    "施工区域": "work_area", "区域": "work_area", "施工部位": "section", "部位": "section",
    "地点": "location", "位置": "location", "地址": "location",
    "拍摄时间": "taken_at", "时间": "taken_at", "日期": "taken_at", "天气": "weather",
    "检查人员": "inspector", "拍摄人": "inspector", "记录人": "inspector", "人员": "inspector",
    "备注": "remark", "备注一": "remark", "备注二": "remark", "备注1": "remark", "备注2": "remark",
    "危大名称": "hazard_name", "危大工程名称": "hazard_name",
    "建设单位": "owner_unit", "施工单位": "construction_unit", "监理单位": "supervision_unit",
}

FIELD_ALIASES = {
    "project": "project_name",
    "contractor": "construction_unit",
    "person": "inspector",
}


def _media_ids_by_evidence(plan: dict[str, Any]) -> dict[str, int]:
    return {
        str(item["id"]): int(item["media_id"])
        for item in plan.get("evidence", [])
        if isinstance(item, dict) and item.get("media_id") is not None
    }


def _flatten_event_fields(raw: Any, form_code: str | None) -> dict[str, Any]:
    """Accept both legacy flat fields and modern form-code keyed selections."""
    if not isinstance(raw, dict):
        return {}
    selected: dict[str, Any] = {}
    if isinstance(raw.get(form_code), dict):
        selected.update(raw[form_code])
    elif all(isinstance(value, dict) for value in raw.values()):
        for value in raw.values():
            selected.update(value)
    else:
        selected.update(raw)
    out: dict[str, Any] = {}
    for key, value in selected.items():
        if isinstance(value, dict) and "value" in value:
            value = value.get("value")
        if value in (None, "") or isinstance(value, (dict, list)):
            continue
        canonical = FIELD_ALIASES.get(str(key), str(key))
        out[canonical] = value
    return out


def _selected_event_fields(event: dict[str, Any], form_code: str | None) -> dict[str, Any]:
    """Merge candidates so form-specific user decisions override entity defaults."""
    return {
        **_flatten_event_fields(event.get("entity_fields"), form_code),
        **_flatten_event_fields(event.get("fields"), form_code),
    }


def _business_content(value: Any, form_fields: dict[str, Any], record_type: str) -> str:
    text = str(value or "").strip()
    if text and not record_fields.is_instruction_only(text):
        return text
    for key in ("work_content", "content", "activity", "work_area"):
        candidate = str(form_fields.get(key) or "").strip()
        if candidate and not record_fields.is_instruction_only(candidate):
            return candidate
    place = str(form_fields.get("section") or form_fields.get("work_area") or "").strip()
    return f"{place}{record_type}检查" if place else f"现场{record_type}检查"


def _business_problem(form_fields: dict[str, Any]) -> str:
    """Return a real inspection problem, never the user's command text."""
    for key in ("problems", "problem", "issue"):
        text = str(form_fields.get(key) or "").strip()
        if text and not record_fields.is_instruction_only(text):
            return text
    return ""


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
        record_type = event.get("record_type") or "待定"
        permission = RECORD_PERMISSIONS.get(record_type)
        if not permission:
            raise HTTPException(422, f"工作记录类型“{record_type}”尚未确认，不能正式建档")
        if not user.has_permission(permission):
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
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "当前项目不存在")

    results: list[dict[str, Any]] = []
    for event in plan["events"]:
        record_type = event.get("record_type") or "待定"
        event_media_ids = [media_by_evidence[item] for item in event["evidence_ids"] if item in media_by_evidence]
        urls = [media_by_id[mid].processed_path or media_by_id[mid].original_path for mid in event_media_ids]
        record_date = date.fromisoformat(event["date"]) if event.get("date") else None
        record_date_text = record_date.isoformat() if record_date else ""
        form_requirements = agent_form_requirements.resolve_event_form(plan, event)
        form_code = form_requirements.get("form_code") or record_field_spec.form_for_record_type(record_type)
        if (
            form_requirements.get("missing_fields")
            or form_requirements.get("conflict_labels")
            or form_requirements.get("invalid_fields")
        ):
            labels = "、".join(dict.fromkeys(
                list(form_requirements.get("missing_labels") or form_requirements["missing_fields"])
                + list(form_requirements.get("conflict_labels") or [])
                + [
                    f"{item.get('label') or item.get('canonical')}（{item.get('message') or '格式无效'}）"
                    for item in form_requirements.get("invalid_fields") or []
                ]
            ))
            raise HTTPException(422, f"事项“{event.get('title') or event['id']}”尚未补全：{labels}")
        selected_fields = _selected_event_fields(event, form_code)
        event_evidence = [
            item for item in plan.get("evidence", [])
            if item.get("id") in event["evidence_ids"]
        ]
        watermark_fields: dict[str, Any] = {}
        for item in event_evidence:
            for field_item in item.get("watermark_items") or []:
                if not isinstance(field_item, dict):
                    continue
                label = str(field_item.get("label") or "").strip()
                value = str(field_item.get("value") or "").strip()
                if not label:
                    continue
                watermark_fields.setdefault("labeled_items", []).append({"label": label, "value": value})
                if value:
                    # 先按当前记录类型的字段表匹配（数据驱动），旧别名仅兜底
                    canonical = match_form_field(label, form_code) or WATERMARK_FIELD_ALIASES.get(label)
                    if canonical:
                        previous = watermark_fields.get(canonical)
                        if previous in (None, ""):
                            watermark_fields[canonical] = value
                        elif canonical == "remark" and value not in str(previous).split("；"):
                            watermark_fields[canonical] = f"{previous}；{value}"
                        elif previous != value:
                            variants = watermark_fields.setdefault(f"{canonical}_variants", [])
                            for candidate in (previous, value):
                                if candidate not in variants:
                                    variants.append(candidate)
                    watermark_fields[f"label:{label}"] = value
            for key, value in (item.get("watermark_fields") or {}).items():
                if value not in (None, ""):
                    watermark_fields.setdefault(key, value)
        # This is the single authoritative snapshot used by record details and
        # every downstream form renderer. Keep raw watermark values separately
        # for audit; they must never override the user's confirmed selection.
        form_fields = {**selected_fields, **form_requirements.get("values", {})}
        for legacy, canonical in FIELD_ALIASES.items():
            if form_fields.get(legacy) not in (None, "") and form_fields.get(canonical) in (None, ""):
                form_fields[canonical] = form_fields[legacy]
        field_sources = event.get("field_sources") or {}
        user_location = event.get("location") if field_sources.get("location") == "user_message" else None
        resolved_section = (
            form_fields.get("section") or form_fields.get("work_area")
            or event.get("section") or event.get("location") or None
        )
        resolved_location = user_location or form_fields.get("location") or event.get("location") or None
        resolved_content = str(
            form_fields.get("content") or form_fields.get("work_content") or ""
        ).strip()
        if record_fields.is_instruction_only(resolved_content):
            resolved_content = ""
        form_spec = form_registry.FORM_BY_CODE.get(form_code)
        if form_spec and form_spec.agent_business_route == "parallel_inspection":
            parallel = ParallelInspection(
                user_id=user.id,
                project_id=project_id,
                inspect_date=record_date_text,
                part=str(form_fields.get("section") or form_fields.get("part") or "").strip(),
                item=str(form_fields.get("acceptance_content") or form_fields.get("item") or "").strip(),
                method=str(form_fields.get("method") or "").strip(),
                basis=str(form_fields.get("basis") or "").strip(),
                result_detail=str(form_fields.get("record") or form_fields.get("measure_data") or resolved_content).strip(),
                conclusion=str(form_fields.get("conclusion") or "").strip(),
                treatment=str(form_fields.get("handling") or form_fields.get("treatment") or "").strip(),
                form_code=form_code,
            )
            db.add(parallel)
            await db.flush()
            for media_id in event_media_ids:
                media = media_by_id[media_id]
                media.business_type = "parallel_inspection"
                media.business_id = parallel.id
            results.append({
                "event_id": event["id"],
                "record_id": parallel.id,
                "record_type": record_type,
                "record_date": parallel.inspect_date or None,
                "media_ids": event_media_ids,
                "business_type": "parallel_inspection",
                "form_code": form_code,
            })
            continue
        problem_text = _business_problem(form_fields)
        project_name = str(form_fields.get("project_name") or form_fields.get("project") or "").strip()
        construction_unit = str(form_fields.get("construction_unit") or "").strip()
        supervision_unit = str(form_fields.get("supervision_unit") or "").strip()
        owner_unit = str(form_fields.get("owner_unit") or "").strip()
        inspector = str(form_fields.get("inspector") or form_fields.get("person") or "").strip()
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
            "field_sources": field_sources,
            "record_type_reason": event.get("record_type_reason") or "",
            "record_type_confidence": event.get("record_type_confidence", 0),
            "date_source": event.get("date_source") or ("event_date" if event.get("date") else "unknown"),
            "evidence_notes": [
                item.get("note", "") for item in plan.get("evidence", [])
                if item.get("id") in event["evidence_ids"] and item.get("note")
            ],
            "evidence_watermark_fields": [
                {
                    "evidence_id": item.get("id"),
                    "fields": item.get("watermark_fields") or {},
                    "observed_date": item.get("observed_date") or "",
                    "observed_date_source": item.get("observed_date_source") or "",
                }
                for item in event_evidence
            ],
            "extracted_watermark_fields": watermark_fields,
            "form_field_candidates": {
                **watermark_fields,
                **selected_fields,
            },
            "extracted_fields": selected_fields,
            "field_resolutions": form_fields,
            "confirmed_fields": form_fields,
            "confirmed_field_metadata": {
                key: {
                    "value": value,
                    "source": form_requirements.get("sources", {}).get(key, "user_confirmed"),
                    "status": "user_confirmed",
                }
                for key, value in form_fields.items()
                if value not in (None, "")
            },
            "prov_fields": form_requirements.get("values", {}),
            "form_requirements": form_requirements,
            "form_code": form_code,
            "form_contract_version": form_requirements.get("contract_version", ""),
            "form_context": {
                "project_name": project_name,
                "owner_unit": owner_unit,
                "construction_unit": construction_unit,
                "supervision_unit": supervision_unit,
                "inspector": inspector,
            },
        }
        record = WorkRecord(
            record_type=record_type,
            status=WorkRecordStatus.DRAFT.value,
            project_id=project_id,
            creator_id=user.id,
            section=resolved_section,
            location=resolved_location,
            work_content=resolved_content[:2000] or None,
            record_date=record_date,
            weather=str(form_fields.get("weather") or "")[:16] or None,
            worker_count=int(form_fields["worker_count"]) if str(form_fields.get("worker_count") or "").isdigit() else None,
            inspection_start_time=_event_datetime(form_fields.get("start_time") or form_fields.get("taken_at"), record_date_text),
            inspection_end_time=_event_datetime(form_fields.get("end_time"), record_date_text),
            inspection_remark=problem_text[:2000] or None if record_type == "巡视" else None,
            station_project_name=project_name[:128] or None if record_type == "旁站" else None,
            station_content=resolved_content[:2000] or None if record_type == "旁站" else None,
            station_location=str(form_fields.get("section") or form_fields.get("station_part") or resolved_section or resolved_location or "")[:256] or None,
            station_process=str(form_fields.get("station_process") or "")[:256] or None,
            station_start_time=_event_datetime(form_fields.get("start_time") or form_fields.get("taken_at"), record_date_text),
            station_end_time=_event_datetime(form_fields.get("end_time"), record_date_text),
            station_conclusion=str(
                selected_fields.get("conclusion") or selected_fields.get("problems") or ""
            )[:2000] or None,
            acceptance_result=str(form_fields.get("acceptance_result") or form_fields.get("supervision_conclusion") or form_fields.get("conclusion") or "")[:32] or None,
            acceptance_participants=str(form_fields.get("acceptance_participants") or form_fields.get("person") or "") or None,
            acceptance_basis=str(form_fields.get("basis") or ""),
            witness_material_name=str(form_fields.get("material_name") or form_fields.get("witness_material_name") or "")[:128] or None,
            witness_material_spec=str(form_fields.get("material_spec") or form_fields.get("witness_material_spec") or form_fields.get("spec") or "")[:256] or None,
            witness_quantity=str(form_fields.get("quantity") or form_fields.get("witness_quantity") or "")[:32],
            witness_unit=str(form_fields.get("unit") or form_fields.get("witness_unit") or "")[:16],
            witness_cert_no=str(form_fields.get("cert_no") or form_fields.get("witness_cert_no") or "")[:64],
            witness_accept_result=str(
                form_fields.get("acceptance_result") or form_fields.get("witness_accept_result") or form_fields.get("accept_result") or ""
            )[:16],
            problems=problem_text,
            treatment=str(form_fields.get("treatment") or form_fields.get("handling") or ""),
            remark=str(form_fields.get("remark") or "") or None,
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
            "record_date": record.record_date.isoformat() if record.record_date else None,
            "media_ids": event_media_ids,
        })

    result = {"plan_id": row.id, "revision": expected_revision, "created_count": len(results), "records": results}
    row.status = "executed"
    row.executed_revision = expected_revision
    row.execution_result_json = json.dumps(result, ensure_ascii=False)
    row.updated_at = datetime.now()
    # The caller owns the transaction so the formal rows, the review-plan
    # terminal state and AgentFlowInstance.COMMITTED commit as one atomic unit.
    return result
