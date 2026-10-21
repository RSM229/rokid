"""Deterministic work-item candidates built from facts and evidence.

The model does not decide how many records should exist. This planner groups
evidence only on explicit identity fields and keeps unknown evidence visible
as unassigned rather than silently merging it into a record.
"""
from __future__ import annotations

from typing import Any, Iterable


IDENTITY_FIELDS = ("record_date", "project", "location", "section")
RECORD_ACTIONS = {"record_work", "create_hazard"}
_EMPTY_VALUES = {"", "null", "none", "unknown", "未知", "未识别", "待定", "n/a"}
_OBJECTIVE_SOURCES = {"watermark", "exif", "watermark_or_exif"}


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
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    sources = evidence_sources or {}
    for field in IDENTITY_FIELDS:
        evidence_value = _text(identity.get(field))
        user_value = user_fields.get(field)
        evidence_source = _text(sources.get(field)) or "evidence"
        objective_evidence = evidence_source in _OBJECTIVE_SOURCES
        if evidence_value and (objective_evidence or not _usable(user_value)):
            out[field] = {
                "value": evidence_value,
                "source": evidence_source,
                "status": "evidence_extracted" if objective_evidence else "vision_observed",
            }
        elif _usable(user_value):
            out[field] = {
                "value": user_value,
                "source": "user_message",
                "status": "user_stated_candidate",
            }
    return out


def build_item_plan(
    *,
    facts: Iterable[dict[str, Any]] | None,
    evidence: Iterable[dict[str, Any]] | None,
    record_type: str | None = None,
    action_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build zero, one or many item candidates without writing anything."""
    fact_map = _fact_map(facts)
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
        return {
            "items": [{
                "id": "item_1",
                "record_type": record_type,
                "fields": _field_views({}, fact_map),
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
            target = {"identity": {}, "sources": {}, "evidence_indexes": [], "notes": []}
            groups.append(target)
        target["evidence_indexes"].append(index)
        for field, value in known.items():
            target["identity"].setdefault(field, value)
            if field not in target["sources"] and sources.get(field):
                target["sources"][field] = sources[field]
        note = _text(item.get("activity") or item.get("observation") or item.get("note"))
        if note:
            target["notes"].append(note[:500])

    items: list[dict[str, Any]] = []
    for index, group in enumerate(groups, 1):
        identity = group["identity"]
        fields = _field_views(identity, fact_map, group.get("sources"))
        missing = [field for field in ("record_date", "location", "section") if field not in fields]
        issues: list[str] = []
        for field in IDENTITY_FIELDS:
            user_value = _text(fact_map.get(field))
            evidence_value = _text(identity.get(field))
            if user_value and evidence_value and user_value.lower() != evidence_value.lower():
                source = _text((group.get("sources") or {}).get(field))
                preferred = "图片水印/EXIF" if source in _OBJECTIVE_SOURCES else "用户描述"
                issues.append(f"{field} 存在用户描述与图片证据冲突，候选按{preferred}采用")
        items.append({
            "id": f"item_{index}",
            "record_type": record_type,
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
        items.append({
            "id": "item_1",
            "record_type": record_type,
            "fields": _field_views({}, fact_map),
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
