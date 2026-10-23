"""Resolve the fields an Agent review event must collect before record creation.

The field specification is shared by planning, inline confirmation and record
execution.  Watermarks and explicit user choices are converted to the same
canonical field names used by the Word templates.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any, Iterable

from app.services import form_contracts, form_registry, record_field_spec, record_fields
from app.services.watermark_ocr import match_form_field


_INPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "project_name": ("project_name", "project", "name", "engineering_name", "工程名称"),
    "construction_unit": ("construction_unit", "contractor", "contractor_unit", "施工单位"),
    "supervision_unit": ("supervision_unit", "supervision_unit_name", "监理单位"),
    "supervision_org": ("supervision_org", "supervision_department", "项目监理机构"),
    "owner_unit": ("owner_unit", "owner_unit_name", "建设单位", "建设方"),
    "inspector": ("inspector", "person", "sampler"),
    "section": ("section", "work_area", "station_part", "station_location", "acceptance_section"),
    "location": ("location", "address"),
    "content": ("content", "work_content", "station_content", "inspection_content", "activity"),
    "problems": ("problems", "problem", "issue", "inspection_remark"),
    "treatment": ("treatment", "handling"),
    "start_time": ("start_time", "inspection_start_time", "station_start_time", "taken_at"),
    "end_time": ("end_time", "inspection_end_time", "station_end_time"),
    "date": ("date", "record_date", "taken_at"),
    "time": ("time", "date", "record_date", "taken_at"),
    "sample_date": ("sample_date", "record_date", "taken_at"),
    "material_name": ("material_name", "witness_material_name"),
    "material_spec": ("material_spec", "witness_material_spec", "spec"),
    "quantity": ("quantity", "witness_quantity"),
    "unit": ("unit", "witness_unit"),
    "cert_no": ("cert_no", "witness_cert_no", "report_no"),
    "acceptance_result": ("acceptance_result", "witness_accept_result", "accept_result"),
}
_CANONICAL_INPUT_FIELDS = {
    alias: canonical
    for canonical, aliases in _INPUT_ALIASES.items()
    for alias in aliases
}

_IGNORED_REQUIRED_AUTO = {"doc_number", "serial_no"}
_MULTILINE = {"content", "problems", "treatment", "remark", "requirement", "acceptance_content"}
_EMPTY = {"", "null", "none", "unknown", "未知", "未识别", "待定", "n/a"}


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _usable(value: Any) -> bool:
    text = _text(value)
    return bool(text and text.lower() not in _EMPTY and not record_fields.is_instruction_only(text))


def _selected_value(raw: Any) -> tuple[str, str, str]:
    if isinstance(raw, dict):
        return _text(raw.get("value")), _text(raw.get("source")), _text(raw.get("status"))
    return _text(raw), "", ""


def _field_maps(event: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for key in ("entity_fields", "fields"):
        raw = event.get(key)
        if isinstance(raw, dict):
            yield f"event.{key}", raw
            for nested_key, nested in raw.items():
                if isinstance(nested, dict) and "value" not in nested:
                    yield f"event.{key}.{nested_key}", nested


def suggest_form(record_type: str | None, forms: Any = None) -> str | None:
    """Return the form explicitly selected by the user, else the record default."""
    selected = [str(item) for item in (forms or []) if str(item)]
    for form_code in selected:
        if form_code in form_registry.FORM_BY_CODE:
            return form_code
    return record_field_spec.form_for_record_type(str(record_type or ""))


def resolve_event_form(
    plan: dict[str, Any] | None,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Resolve field values, controls and blocking missing fields for one event."""
    record_type = str(event.get("record_type") or "待定")
    form_code = suggest_form(record_type, event.get("forms"))
    if not form_code or form_code not in form_registry.FORM_BY_CODE:
        return {
            "form_code": None,
            "form_name": "",
            "fields": [],
            "values": {},
            "conflicts": [],
            "invalid_fields": [],
            "missing_fields": [],
            "missing_labels": [],
            "contract_version": "",
            "contract_source": "",
            "write_allowed": False,
            "materialization_error": "当前记录类型没有可用的字段合同",
        }

    form_spec = form_registry.FORM_BY_CODE.get(form_code)
    target_record_type = (
        (form_spec.agent_work_record_type or form_spec.agent_record_type)
        if form_spec else None
    )
    materialization_error = ""
    if not target_record_type:
        materialization_error = "该表式使用独立业务或汇总流程，尚未接入 Agent 工作记录生成"
    elif record_type != "待定" and target_record_type != record_type:
        materialization_error = f"该表式生成“{target_record_type}”记录，与当前“{record_type}”类型不一致"

    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    statuses: dict[str, str] = {}
    evidence_candidates: dict[str, list[dict[str, str]]] = {}
    system_project = (plan or {}).get("system_project")
    system_project = system_project if isinstance(system_project, dict) else {}
    # Plans may come from older clients or a different project schema.  Keep
    # the canonical context while accepting aliases such as ``name`` and
    # ``contractor`` instead of making the model know one ORM shape.
    system_project = {
        **system_project,
        **record_fields.project_context(system_project),
    }

    def add(key: str, value: Any, source: str, status: str = "") -> None:
        input_key = str(key or "").strip()
        canonical = _CANONICAL_INPUT_FIELDS.get(input_key, input_key)
        text = _text(value)
        if not canonical or not _usable(text):
            return
        if canonical not in values:
            values[canonical] = text
            sources[canonical] = source or "candidate"
            statuses[canonical] = status or "candidate"

    # Evidence is the objective default. Explicit event selections are applied
    # after it, so a user's confirmed choice can replace a watermark candidate.
    evidence_by_id = {
        str(item.get("id")): item
        for item in (plan or {}).get("evidence", [])
        if isinstance(item, dict)
    }
    for evidence_id in event.get("evidence_ids") or []:
        evidence = evidence_by_id.get(str(evidence_id)) or {}
        for item in evidence.get("watermark_items") or []:
            if not isinstance(item, dict):
                continue
            label = _text(item.get("label"))
            value = item.get("value")
            canonical = match_form_field(label, form_code) if label else None
            if canonical:
                text = _text(value)
                if _usable(text):
                    candidates = evidence_candidates.setdefault(canonical, [])
                    candidate = {"value": text, "label": label, "evidence_id": str(evidence_id)}
                    if candidate not in candidates:
                        candidates.append(candidate)
                add(canonical, value, "watermark", "evidence_extracted")
            add(f"label:{label}", value, "watermark", "evidence_extracted") if label else None
        for key, value in (evidence.get("watermark_fields") or {}).items():
            add(str(key), value, "watermark", "evidence_extracted")

    # The planning model may surface candidates from context beyond literal
    # watermark labels (for example, a role inferred from the surrounding
    # record). Preserve those candidates too; only a user-confirmed field can
    # resolve a multi-value decision.
    raw_candidates = event.get("field_candidates")
    if isinstance(raw_candidates, dict):
        for key, candidates in raw_candidates.items():
            canonical = _CANONICAL_INPUT_FIELDS.get(str(key), str(key))
            if not isinstance(candidates, list):
                continue
            rows = evidence_candidates.setdefault(canonical, [])
            for candidate in candidates:
                value = candidate.get("value") if isinstance(candidate, dict) else candidate
                text = _text(value)
                if _usable(text):
                    label = _text(candidate.get("label")) if isinstance(candidate, dict) else ""
                    entry = {"value": text, "label": label, "evidence_id": ""}
                    if entry not in rows:
                        rows.append(entry)

    field_sources = event.get("field_sources") if isinstance(event.get("field_sources"), dict) else {}
    for map_name, raw_map in _field_maps(event):
        for key, raw in raw_map.items():
            if isinstance(raw, dict) and "value" not in raw:
                continue
            value, source, status = _selected_value(raw)
            if not _usable(value):
                continue
            source = source or _text(field_sources.get(str(key))) or "candidate"
            status = status or ("user_confirmed" if source in {"user_message", "user_confirmed"} else "candidate")
            canonical = _CANONICAL_INPUT_FIELDS.get(str(key), str(key))
            values[canonical] = value
            sources[canonical] = source
            statuses[canonical] = status

    # Top-level event facts are also legitimate form inputs.
    for key, value, source in (
        ("date", event.get("date"), event.get("date_source") or "event_date"),
        ("record_date", event.get("date"), event.get("date_source") or "event_date"),
        ("time", event.get("date"), event.get("date_source") or "event_date"),
        ("section", event.get("section"), field_sources.get("section") or "candidate"),
        ("location", event.get("location"), field_sources.get("location") or "candidate"),
        ("content", event.get("content"), "event_content"),
    ):
        if _usable(value) and (key not in values or source in {"user_message", "user_confirmed"}):
            values[key] = _text(value)
            sources[key] = _text(source) or "candidate"
            statuses[key] = "user_confirmed" if source in {"user_message", "user_confirmed"} else "candidate"

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    missing_labels: list[str] = []
    conflicts: list[dict[str, Any]] = []
    invalid_fields: list[dict[str, str]] = []
    contract = form_contracts.contract_for(form_code)
    for spec in contract["fields"]:
        canonical = _text(spec.get("canonical"))
        kind = _text(spec.get("kind")) or "ask"
        required = bool(spec.get("required"))
        if not canonical or kind == "signoff":
            continue
        if kind == "auto" and canonical in _IGNORED_REQUIRED_AUTO:
            continue
        if kind == "auto" and not required:
            continue

        aliases = [canonical, _text(spec.get("key")), _text(spec.get("arg")), *_INPUT_ALIASES.get(canonical, ())]
        value = ""
        source = ""
        status = ""
        for alias in aliases:
            if alias and _usable(values.get(alias)):
                value = values[alias]
                source = sources.get(alias, "candidate")
                status = statuses.get(alias, "candidate")
                break

        candidates = evidence_candidates.get(canonical, [])
        distinct_candidates = list(dict.fromkeys(item["value"] for item in candidates))
        user_resolved = source in {"user_message", "user_confirmed"} or status == "user_confirmed"
        if len(distinct_candidates) > 1 and not user_resolved:
            conflicts.append({
                "canonical": canonical,
                "label": _text(spec.get("label")) or canonical,
                "candidates": candidates,
            })

        if canonical == "date" and not value:
            value = _text(event.get("date"))
            source = _text(event.get("date_source")) or "event_date"
        if canonical == "project_name" and not value:
            value = _text(
                (event.get("entity_fields") or {}).get("project_name")
                or (event.get("entity_fields") or {}).get("project")
                or system_project.get("project_name")
            )
            source = "system_project"
        if canonical == "construction_unit" and not value:
            value = _text(
                (event.get("entity_fields") or {}).get("construction_unit")
                or system_project.get("construction_unit")
            )
            source = "system_project"
        if canonical == "owner_unit" and not value:
            value = _text(system_project.get("owner_unit"))
            source = "system_project"
        if canonical in {"supervision_unit", "supervision_org"} and not value:
            value = _text(system_project.get(canonical))
            source = "system_project"

        table_type = _text(spec.get("table_type"))
        control = "text"
        if "枚举" in table_type:
            control = "select"
        elif "日期时间" in table_type:
            control = "datetime-local"
        elif "日期" in table_type:
            control = "date"
        elif "数字" in table_type or "序号" in table_type:
            control = "number"
        elif canonical in _MULTILINE:
            control = "textarea"

        row = {
            "key": _text(spec.get("key")) or canonical,
            "canonical": canonical,
            "label": _text(spec.get("label")) or canonical,
            "section": _text(spec.get("section")),
            "table_type": table_type,
            "control": control,
            "options": list(spec.get("options") or record_field_spec.options_for(spec)),
            "question": record_field_spec.question_for(spec),
            "required": required,
            "readonly": kind == "auto" and bool(value),
            "value": value,
            "source": source or ("missing" if required else ""),
            "status": status or ("filled" if value else "missing"),
            "missing": required and not _usable(value),
            "conflict": any(item["canonical"] == canonical for item in conflicts),
            "candidates": candidates,
        }
        if value:
            problem = ""
            if control == "select":
                options = list(spec.get("options") or record_field_spec.options_for(spec))
                if options and value not in options:
                    problem = "请选择合同定义中的有效选项"
            elif control == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    problem = "日期格式无效，请使用 YYYY-MM-DD"
            elif control == "datetime-local":
                normalized = value.replace("年", "-").replace("月", "-").replace("日", " ").replace("时", ":").replace("分", "").strip()
                # The record date is stored separately, so a clearly stated
                # time-only value is valid and is combined with that date at
                # materialization. A vague/unparseable time remains blocked.
                if not re.fullmatch(r"\d{1,2}:\d{2}", normalized):
                    try:
                        datetime.fromisoformat(normalized)
                    except ValueError:
                        problem = "日期时间格式无法识别，请补充为明确日期和时间"
            elif control == "number":
                try:
                    float(value)
                except ValueError:
                    problem = "此字段需要数字"
            if problem:
                row["validation_error"] = problem
                invalid_fields.append({"canonical": canonical, "label": row["label"], "message": problem})
        rows.append(row)
        if row["missing"]:
            missing.append(canonical)
            missing_labels.append(row["label"])

    return {
        "form_code": form_code,
        "contract_version": contract["version"],
        "contract_source": contract["source"],
        "form_name": contract["name"],
        "fields": rows,
        "values": {row["canonical"]: row["value"] for row in rows if row["value"]},
        "sources": {row["canonical"]: row["source"] for row in rows if row["value"]},
        "missing_fields": list(dict.fromkeys(missing)),
        "missing_labels": list(dict.fromkeys(missing_labels)),
        "conflicts": conflicts,
        "conflict_labels": [item["label"] for item in conflicts],
        "invalid_fields": invalid_fields,
        "invalid_labels": [item["label"] for item in invalid_fields],
        "materialization_error": materialization_error,
        "write_allowed": not missing and not conflicts and not invalid_fields and not materialization_error and record_type != "待定",
    }


def resolve_record_form(
    record: Any,
    project: Any = None,
    form_code: str | None = None,
) -> dict[str, Any]:
    """Resolve the same required-field contract for an existing WorkRecord.

    Agent-created records normally already contain ``prov_fields``.  Legacy
    records do not, so record columns are mapped into the same canonical field
    vocabulary before sharing validation and rendering behaviour.
    """
    content = record.content_json if isinstance(getattr(record, "content_json", None), dict) else {}
    selected: dict[str, dict[str, Any]] = {}

    def put(key: str, value: Any, source: str = "record", status: str = "user_confirmed") -> None:
        text = _text(value)
        if not key or not _usable(text) or key in selected:
            return
        selected[key] = {"value": text, "source": source, "status": status}

    # Confirmed/resolved values have priority.  Candidate maps are fallback
    # material for legacy records, never a reason to overwrite a user choice.
    for map_name, source, status in (
        ("field_resolutions", "user_confirmed", "user_confirmed"),
        ("confirmed_fields", "user_confirmed", "user_confirmed"),
        ("prov_fields", "user_confirmed", "user_confirmed"),
        ("form_context", "candidate", "candidate"),
        ("form_field_candidates", "candidate", "candidate"),
        ("extracted_fields", "candidate", "candidate"),
        ("extracted_watermark_fields", "watermark", "evidence_extracted"),
    ):
        fields = content.get(map_name)
        if not isinstance(fields, dict):
            continue
        for key, raw in fields.items():
            value, raw_source, raw_status = _selected_value(raw)
            put(
                str(key),
                value,
                raw_source or source,
                raw_status or status,
            )

    record_type = _text(getattr(record, "record_type", "")) or "待定"
    record_date = record_fields.business_date(record)
    date_text = record_date.isoformat() if isinstance(record_date, date) else ""
    direct: dict[str, Any] = {
        "date": date_text,
        "record_date": date_text,
        "section": getattr(record, "section", None),
        "location": getattr(record, "location", None),
        "weather": getattr(record, "weather", None),
        "content": getattr(record, "work_content", None),
        "problems": getattr(record, "problems", None),
        "treatment": getattr(record, "treatment", None),
        "worker_count": getattr(record, "worker_count", None),
        "inspector": getattr(record, "acceptance_participants", None),
        "station_process": getattr(record, "station_process", None),
        "station_content": getattr(record, "station_content", None),
        "station_conclusion": getattr(record, "station_conclusion", None),
        "material_name": getattr(record, "witness_material_name", None),
        "material_spec": getattr(record, "witness_material_spec", None),
        "quantity": getattr(record, "witness_quantity", None),
        "unit": getattr(record, "witness_unit", None),
        "cert_no": getattr(record, "witness_cert_no", None),
        "acceptance_result": getattr(record, "witness_accept_result", None) or getattr(record, "acceptance_result", None),
    }
    station_location = getattr(record, "station_location", None)
    if record_type == "旁站" and _usable(station_location):
        direct["section"] = station_location
    for value, attr in (
        ("start_time", "station_start_time"),
        ("end_time", "station_end_time"),
    ):
        raw = getattr(record, attr, None)
        if isinstance(raw, datetime):
            direct[value] = raw.isoformat(sep=" ", timespec="minutes")
    if record_type != "旁站":
        for value, attr in (
            ("start_time", "inspection_start_time"),
            ("end_time", "inspection_end_time"),
        ):
            raw = getattr(record, attr, None)
            if isinstance(raw, datetime):
                direct[value] = raw.isoformat(sep=" ", timespec="minutes")
    if record_type == "见证":
        direct["sample_date"] = date_text
        direct["sampler"] = getattr(record, "acceptance_participants", None)
        direct["test_result"] = content.get("test_result")
    for key, value in direct.items():
        put(key, value, "record", "user_confirmed")

    entity_fields: dict[str, str] = {}
    if project is not None:
        entity_fields = {
            "project_name": _text(getattr(project, "name", None)),
            "owner_unit": _text(getattr(project, "owner_unit", None)),
            "construction_unit": _text(getattr(project, "construction_unit", None)),
            "supervision_unit": _text(getattr(project, "supervision_unit", None)),
            "supervision_org": _text(getattr(project, "supervision_org", None)),
        }

    event: dict[str, Any] = {
        "record_type": record_type,
        "date": date_text,
        "date_source": "record",
        "fields": selected,
        "entity_fields": {key: value for key, value in entity_fields.items() if value},
    }
    if form_code:
        event["forms"] = [form_code]
    return resolve_event_form({"evidence": []}, event)


def enrich_event(plan: dict[str, Any] | None, event: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an event with inline form requirements attached."""
    enriched = dict(event)
    resolved = resolve_event_form(plan, event)
    record_type = str(event.get("record_type") or "待定")
    if record_type == "待定":
        status = "needs_record_type"
    elif event.get("record_type_confirmed") is not True:
        status = "needs_user_confirmation"
    elif (
        resolved["missing_fields"] or resolved["conflicts"] or resolved["invalid_fields"]
        or resolved.get("materialization_error")
    ):
        status = "needs_resolution"
    else:
        status = "ready_for_confirmation"
    blockers = [
        *(
            [{"kind": "record_type", "canonical": "record_type", "label": "请确认工作记录类型"}]
            if record_type == "待定" else []
        ),
        *(
            [{"kind": "user_confirmation", "canonical": "record_type", "label": f"请确认采用“{record_type}”记录类型"}]
            if record_type != "待定" and event.get("record_type_confirmed") is not True else []
        ),
        *(
            {"kind": "missing", "canonical": canonical, "label": label}
            for canonical, label in zip(resolved["missing_fields"], resolved["missing_labels"])
        ),
        *(
            {"kind": "conflict", **item}
            for item in resolved["conflicts"]
        ),
        *(
            {"kind": "invalid", **item}
            for item in resolved["invalid_fields"]
        ),
        *(
            [{"kind": "form_not_executable", "canonical": "form_code", "label": resolved["materialization_error"]}]
            if resolved.get("materialization_error") else []
        ),
    ]
    record_draft = {
        "status": status,
        "record_type": record_type,
        "record_type_confirmed": event.get("record_type_confirmed") is True,
        "form_code": resolved["form_code"],
        "form_name": resolved["form_name"],
        "contract_version": resolved["contract_version"],
        "contract_source": resolved["contract_source"],
        "record_date": _text(event.get("date")),
        "section": _text(event.get("section")),
        "location": _text(event.get("location")),
        "content": _text(event.get("content")),
        "fields": resolved["values"],
        "blockers": blockers,
        "generation_allowed": bool(resolved["write_allowed"] and event.get("record_type_confirmed") is True),
    }
    enriched["record_draft"] = record_draft
    enriched["form_code"] = resolved["form_code"]
    enriched["form_fields"] = resolved["fields"]
    enriched["form_values"] = resolved["values"]
    enriched["form_missing_fields"] = resolved["missing_fields"]
    enriched["form_missing_labels"] = resolved["missing_labels"]
    enriched["form_conflicts"] = resolved["conflicts"]
    enriched["form_invalid_fields"] = resolved["invalid_fields"]
    enriched["form_contract_version"] = resolved["contract_version"]
    enriched["form_assessment"] = {
        "status": status,
        "form_code": resolved["form_code"],
        "contract_version": resolved["contract_version"],
        "contract_source": resolved["contract_source"],
        "missing_fields": resolved["missing_fields"],
        "conflicts": resolved["conflicts"],
        "invalid_fields": resolved["invalid_fields"],
        "generation_allowed": record_draft["generation_allowed"],
    }
    # Keep the legacy field useful to older clients while making canonical
    # template requirements part of the same confirmation gate.
    existing = [str(item) for item in event.get("missing_fields") or []]
    enriched["missing_fields"] = list(dict.fromkeys(
        [item for item in existing if item not in resolved["missing_fields"]]
        + resolved["missing_fields"]
    ))
    return enriched


def enrich_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return plan
    return {
        **plan,
        "events": [
            enrich_event(plan, event)
            for event in plan.get("events", [])
            if isinstance(event, dict)
        ],
    }
