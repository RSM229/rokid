"""Deterministic work-item candidates built from facts and evidence.

The model does not decide how many records should exist. This planner groups
evidence only on explicit identity fields and keeps unknown evidence visible
as unassigned rather than silently merging it into a record.
"""
from __future__ import annotations

from typing import Any, Iterable


IDENTITY_FIELDS = ("record_date", "project", "location", "section")
CONTEXT_FIELDS = (
    "owner_unit", "construction_unit", "supervision_unit", "inspector",
    "work_content", "weather", "start_time", "end_time",
)
# 🔴 2026-11-24 深走测试断链（agent-walk-full 卡点）第二处：带图草稿逐栏追问的
# 短答 fact（self_check_conclusion 等）被 item_plan 整层丢弃——IDENTITY/CONTEXT
# 白名单之外全部不进 item.fields，前端 reviewPlanFromItemPlan 拿不到值，
# 「已把X并入当前草稿」回执后缺栏清单纹丝不动、同栏无限重问。
# 放行口径（宁窄勿宽）：
#   1. 必须是当前 record_type 表式里 kind=ask 的字段 canonical（record_field_spec）；
#   2. 必须有用户来源 fact（source 以 user_ 开头）——水印/系统/模型编造字段不进
#      item.fields（它们仍可进 plan.facts，但分组视图只认用户明说的表栏值，
#      防止模型幻觉字段污染分组与草稿）；
#   3. 照片/签章类栏排除（聊天填不了，不走文字链路）。
FORM_ASK_FIELD_SOURCES = {"user_message", "user_message_model", "user_message_deterministic"}
# 参建单位/项目身份字段：多来源不一致时按「用户明说 > 当前项目 > 水印」裁决，
# 不再让用户逐项勾选（2026-11-03 用户要求「少输入、智能体帮忙思考」）。
PROJECT_PARTY_FIELDS = {"project", "owner_unit", "construction_unit", "supervision_unit", "supervision_org"}
RECORD_ACTIONS = {"record_work", "create_hazard"}
_EMPTY_VALUES = {"", "null", "none", "unknown", "未知", "未识别", "待定", "n/a"}
_OBJECTIVE_SOURCES = {"watermark", "exif", "watermark_or_exif"}
_EVIDENCE_CONTEXT_KEYS = {
    "owner_unit": ("owner_unit",),
    "construction_unit": ("construction_unit", "contractor"),
    "supervision_unit": ("supervision_unit",),
    "inspector": ("inspector", "person"),
    "weather": ("weather",),
}
_FIELD_LABELS = {
    "record_date": "日期",
    "start_time": "开始时间",
    "end_time": "结束时间",
    "project": "项目",
    "location": "地点",
    "section": "工程部位",
    "owner_unit": "建设单位",
    "construction_unit": "施工单位",
    "supervision_unit": "监理单位",
    "inspector": "检查人员",
    "weather": "天气",
    "work_content": "工作内容",
}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _usable(value: Any) -> bool:
    return _text(value).lower() not in _EMPTY_VALUES


def _fact_map(facts: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in facts or []:
        if not isinstance(item, dict):
            continue
        field = _text(item.get("field"))
        value = item.get("value")
        if field and _usable(value) and field not in out:
            out[field] = value
    return out


def _evidence_identity(item: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    identity = {
        "record_date": _text(item.get("record_date") or item.get("observed_date")),
        "project": _text(item.get("project")),
        "location": _text(item.get("location")),
        "section": _text(item.get("section")),
    }
    field_sources = item.get("field_sources")
    watermark_fields = item.get("watermark_fields")
    watermark_keys = {
        "record_date": ("taken_at", "record_date", "date"),
        "project": ("project", "project_name"),
        "location": ("location", "address"),
        "section": ("section", "work_area"),
    }
    sources: dict[str, str] = {}
    for field, value in identity.items():
        if not _usable(value):
            continue
        source = _text(field_sources.get(field)) if isinstance(field_sources, dict) else ""
        if not source and field == "record_date" and _text(item.get("observed_date_source")):
            source = _text(item.get("observed_date_source"))
        if not source and isinstance(watermark_fields, dict):
            if any(_text(watermark_fields.get(key)) for key in watermark_keys.get(field, ())):
                source = "watermark"
        sources[field] = source or _text(item.get("source")) or "evidence"
    return identity, sources


def _evidence_context(item: dict[str, Any]) -> dict[str, str]:
    """Collect form-relevant evidence fields that are not grouping identities."""
    watermark_fields = item.get("watermark_fields")
    watermark = watermark_fields if isinstance(watermark_fields, dict) else {}
    out: dict[str, str] = {}
    for field, keys in _EVIDENCE_CONTEXT_KEYS.items():
        value = next(
            (_text(item.get(key)) for key in keys if _text(item.get(key))),
            "",
        ) or next(
            (_text(watermark.get(key)) for key in keys if _text(watermark.get(key))),
            "",
        )
        if _usable(value):
            out[field] = value
    activity = _text(item.get("activity"))
    if _usable(activity):
        out["work_content"] = activity
    return out


def _compatible(group: dict[str, str], incoming: dict[str, str]) -> bool:
    shared = 0
    for field in IDENTITY_FIELDS:
        left = _text(group.get(field)).lower()
        right = _text(incoming.get(field)).lower()
        if left and right:
            if left != right:
                return False
            shared += 1
    return shared > 0


def _field_views(
    identity: dict[str, str],
    user_fields: dict[str, Any],
    evidence_sources: dict[str, str] | None = None,
    context_fields: dict[str, dict[str, Any]] | None = None,
    system_fields: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    sources = evidence_sources or {}
    system_fields = system_fields or {}

    def add_view(
        field: str,
        preferred_value: Any,
        preferred_source: str,
        status: str,
        *candidates: Any,
    ) -> None:
        values = list(dict.fromkeys(
            _text(value) for value in (preferred_value, *candidates) if _usable(value)
        ))
        field_view: dict[str, Any] = {
            "value": _text(preferred_value),
            "source": preferred_source,
            "status": status,
        }
        if len({_text(value).lower() for value in values}) > 1:
            # 2026-11-03 用户要求「不要我自己填表格 / 少输入」：多来源不一致不再标
            # conflict 让人逐项勾选。由 Agent 直接裁决（采用哪个见 value/source），
            # 其余候选留在 candidates 里供事后点选修改，不再阻断建档。
            field_view["candidates"] = values
            field_view["auto_resolved"] = True
        out[field] = field_view

    for field in IDENTITY_FIELDS:
        evidence_value = _text(identity.get(field))
        user_value = user_fields.get(field)
        system_value = system_fields.get(field)
        evidence_source = _text(sources.get(field)) or "evidence"
        objective_evidence = evidence_source in _OBJECTIVE_SOURCES
        if field in PROJECT_PARTY_FIELDS:
            # 项目身份字段：用户明说 > 当前项目 > 水印（防止跨项目照片污染归属）。
            if _usable(user_value):
                add_view(
                    field, user_value, "user_message", "user_stated_candidate",
                    evidence_value, system_value,
                )
            elif _usable(system_value):
                add_view(
                    field, system_value, "system_project", "system_configured",
                    evidence_value,
                )
            elif evidence_value:
                add_view(
                    field, evidence_value, evidence_source,
                    "evidence_extracted" if objective_evidence else "vision_observed",
                    system_value,
                )
        else:
            # 其余字段维持原优先级：客观水印 > 用户 > 系统。
            if evidence_value and (objective_evidence or not _usable(user_value)):
                add_view(
                    field, evidence_value, evidence_source,
                    "evidence_extracted" if objective_evidence else "vision_observed",
                    user_value, system_value,
                )
            elif _usable(user_value):
                add_view(
                    field, user_value, "user_message", "user_stated_candidate",
                    evidence_value, system_value,
                )
            elif _usable(system_value):
                add_view(field, system_value, "system_project", "system_configured")
    for field, candidate in (context_fields or {}).items():
        if field in out:
            continue
        values = candidate.get("values") if isinstance(candidate, dict) else None
        value = _text(candidate.get("value")) if isinstance(candidate, dict) else ""
        source = _text(candidate.get("source")) if isinstance(candidate, dict) else ""
        evidence_values = [_text(item) for item in (values or []) if _text(item)]
        if _usable(user_fields.get(field)):
            add_view(
                field, user_fields.get(field), "user_message", "user_stated_candidate",
                value or (evidence_values[0] if evidence_values else ""),
            )
        elif field in PROJECT_PARTY_FIELDS and _usable(system_fields.get(field)):
            # 参建单位字段：当前项目优先于照片水印。
            add_view(
                field, system_fields.get(field), "system_project", "system_configured",
                value or (evidence_values[0] if evidence_values else ""),
            )
        elif value or evidence_values:
            add_view(
                field, value or evidence_values[0], source or "watermark",
                "evidence_extracted", *evidence_values[1:],
                user_fields.get(field), system_fields.get(field),
            )
        elif _usable(system_fields.get(field)):
            add_view(field, system_fields.get(field), "system_project", "system_configured")
    return out


def _add_context_fields(
    fields: dict[str, dict[str, Any]],
    user_fields: dict[str, Any],
    system_fields: dict[str, Any],
) -> None:
    for field in CONTEXT_FIELDS:
        if field in fields:
            continue
        if _usable(user_fields.get(field)):
            fields[field] = {
                "value": user_fields[field],
                "source": "user_message",
                "status": "user_stated_candidate",
            }
        elif _usable(system_fields.get(field)):
            fields[field] = {
                "value": system_fields[field],
                "source": "system_project",
                "status": "system_configured",
            }


def _add_form_ask_fields(
    fields: dict[str, dict[str, Any]],
    facts: Iterable[dict[str, Any]] | None,
    record_type: str | None,
) -> None:
    """用户明说的表式专用字段（非 IDENTITY/CONTEXT）也进 item.fields。

    只认「当前 record_type 表式里 kind=ask 的 canonical + fact 来源是用户」：
    分组身份（IDENTITY/CONTEXT）继续由本模块白名单裁决，表式业务栏（验收类型/
    自检结论等）由字段表裁决。水印/系统/低置信模型字段不进（防编造污染分组）。
    """
    rtype = _text(record_type)
    if not rtype or rtype in {"待定", "chat", "未知"}:
        return
    from app.services import record_field_spec
    try:
        form_code = record_field_spec.form_for_record_type(rtype)
    except Exception:
        return
    if not form_code:
        return
    user_sources: dict[str, dict[str, Any]] = {}
    for item in facts or []:
        if not isinstance(item, dict):
            continue
        field = _text(item.get("field"))
        value = _text(item.get("value"))
        source = _text(item.get("source"))
        if not field or not _usable(value):
            continue
        if source not in FORM_ASK_FIELD_SOURCES:
            continue
        if field not in user_sources:
            user_sources[field] = {
                "value": value,
                "source": source,
                "status": str(item.get("status") or "user_stated_candidate"),
            }
    if not user_sources:
        return
    for spec in record_field_spec.fields_of(form_code):
        canonical = _text(spec.get("canonical"))
        label = _text(spec.get("label"))
        if not canonical or canonical in fields:
            continue
        if spec.get("kind") != "ask":
            continue
        if any(k in label for k in ("照片", "签章", "签字")):
            continue
        view = user_sources.get(canonical)
        if view is not None:
            fields[canonical] = dict(view)


def build_item_plan(
    *,
    facts: Iterable[dict[str, Any]] | None,
    evidence: Iterable[dict[str, Any]] | None,
    record_type: str | None = None,
    record_type_confidence: float = 0.0,
    action_ids: Iterable[str] | None = None,
    system_facts: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build zero, one or many item candidates without writing anything."""
    fact_map = _fact_map(facts)
    system_map = _fact_map(system_facts)
    actions = {str(item) for item in (action_ids or [])}
    evidence_items = [item for item in (evidence or []) if isinstance(item, dict)]
    wants_record = bool(actions.intersection(RECORD_ACTIONS)) or bool(record_type)

    if not evidence_items:
        if not wants_record:
            return {
                "items": [], "candidate_count": 0, "unassigned_evidence_indexes": [],
                "requires_confirmation": False, "issues": [],
                "grouping_fields": list(IDENTITY_FIELDS), "write_allowed": False,
            }
        missing = [field for field in ("record_date", "location", "section") if not _usable(fact_map.get(field))]
        fields = _field_views({}, fact_map, system_fields=system_map)
        _add_context_fields(fields, fact_map, system_map)
        _add_form_ask_fields(fields, facts, record_type)
        return {
            "items": [{
                "id": "item_1",
                "record_type": record_type,
                "record_type_confidence": record_type_confidence,
                "fields": fields,
                "evidence_indexes": [],
                "missing_fields": missing,
                "confidence": 0.75,
                "status": "candidate",
            }],
            "candidate_count": 1,
            "unassigned_evidence_indexes": [],
            "requires_confirmation": True,
            "issues": [],
            "grouping_fields": list(IDENTITY_FIELDS),
            "write_allowed": False,
        }

    groups: list[dict[str, Any]] = []
    unassigned: list[int] = []
    for index, item in enumerate(evidence_items):
        identity, sources = _evidence_identity(item)
        known = {key: value for key, value in identity.items() if _usable(value)}
        if not known:
            unassigned.append(index)
            continue
        target = next((group for group in groups if _compatible(group["identity"], identity)), None)
        if target is None:
            target = {
                "identity": {}, "sources": {}, "evidence_indexes": [], "notes": [],
                "context": {},
            }
            groups.append(target)
        target["evidence_indexes"].append(index)
        for field, value in known.items():
            target["identity"].setdefault(field, value)
            if field not in target["sources"] and sources.get(field):
                target["sources"][field] = sources[field]
        note = _text(item.get("activity") or item.get("observation") or item.get("note"))
        if note:
            target["notes"].append(note[:500])
        for field, value in _evidence_context(item).items():
            bucket = target["context"].setdefault(field, {"values": [], "source": "watermark"})
            if value not in bucket["values"]:
                bucket["values"].append(value)

    items: list[dict[str, Any]] = []
    for index, group in enumerate(groups, 1):
        identity = group["identity"]
        context: dict[str, dict[str, Any]] = {}
        for field, bucket in (group.get("context") or {}).items():
            values = list(bucket.get("values") or [])
            context[field] = {
                "value": values[0] if values else "",
                "values": values,
                "source": bucket.get("source") or "watermark",
            }
        fields = _field_views(
            identity, fact_map, group.get("sources"), context, system_map,
        )
        _add_context_fields(fields, fact_map, system_map)
        _add_form_ask_fields(fields, facts, record_type)
        missing = [field for field in ("record_date", "location", "section") if field not in fields]
        issues = []
        for field, value in fields.items():
            if not isinstance(value, dict) or value.get("status") != "conflict":
                continue
            source = _text(value.get("source"))
            source_label = "图片水印/EXIF" if source in _OBJECTIVE_SOURCES else (
                "系统项目资料" if source in {"system_project", "user_profile"} else "用户描述"
            )
            issues.append(
                f"{_FIELD_LABELS.get(field, field)}存在多个候选值，当前优先采用{source_label}，需用户确认后才能建档"
            )
        items.append({
            "id": f"item_{index}",
            "record_type": record_type,
            "record_type_confidence": record_type_confidence,
            "fields": fields,
            "evidence_indexes": group["evidence_indexes"],
            "notes": group["notes"],
            "missing_fields": missing,
            "issues": issues,
            "confidence": 0.9 if len(group["evidence_indexes"]) > 1 else 0.78,
            "status": "candidate",
        })

    plan_issues: list[str] = []
    if unassigned and not groups:
        fields = _field_views({}, fact_map, system_fields=system_map)
        _add_context_fields(fields, fact_map, system_map)
        _add_form_ask_fields(fields, facts, record_type)
        items.append({
            "id": "item_1",
            "record_type": record_type,
            "record_type_confidence": record_type_confidence,
            "fields": fields,
            "evidence_indexes": unassigned,
            "notes": [],
            "missing_fields": [field for field in ("record_date", "location", "section") if not _usable(fact_map.get(field))],
            "issues": ["资料中未提取到可用于分组的日期、项目、地点或部位"],
            "confidence": 0.35,
            "status": "needs_identity",
        })
        unassigned = []
        plan_issues.append("资料缺少可分组的身份字段，需用户确认是否合并或补充信息")
    elif unassigned:
        plan_issues.append(f"{len(unassigned)} 份资料的日期、项目、地点和部位均未提取到，需确认归属")
    if len(items) > 1:
        plan_issues.append(f"识别到 {len(items)} 个可能事项；合并或拆分必须由用户确认")

    return {
        "items": items,
        "candidate_count": len(items),
        "unassigned_evidence_indexes": unassigned,
        "requires_confirmation": bool(items),
        "issues": plan_issues,
        "grouping_fields": list(IDENTITY_FIELDS),
        "write_allowed": False,
    }
