"""Source-aware facts used by the agent planning layer.

The language model may propose field values from the current user message, but
it cannot grant those values a higher trust level. This module owns the
source/status vocabulary, fact normalization and cross-source conflict checks.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Iterable

from app.services.agent_context import merge_user_facts


USER_FACT_SOURCES = {
    "user_message_model",
    "user_message_extraction",
    "user_message_deterministic",
    "user_confirmation",
}
USER_FACT_STATUSES = {"user_stated_candidate", "user_confirmed"}
OBJECTIVE_EVIDENCE_SOURCES = {"watermark", "exif", "watermark_or_exif"}
_FIELD_RE = re.compile(r"^(?:extra_fields\.)?[A-Za-z][A-Za-z0-9_.-]{0,95}$")
_EMPTY_VALUES = {"", "null", "none", "unknown", "未知", "未识别", "待定", "n/a"}
_FACT_ALIASES = {
    "date": "record_date",
    "business_date": "record_date",
    "content": "work_content",
    "work_area": "section",
    "part": "section",
}
# Facts are intentionally narrower than arbitrary tool arguments. They cover
# cross-cutting business fields used for merging and conflict handling; all
# table-specific values still travel through args/extra_fields.
_ALLOWED_FACT_FIELDS = {
    "record_type", "record_date", "project", "location", "section",
    "work_content", "problems", "treatment", "worker_count", "weather",
    "station_part", "station_process", "station_content", "station_conclusion",
    "acceptance_result", "witness_material_name", "hazard_type", "description",
}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _confidence(value: Any, default: float = 0.8) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _usable_value(value: Any) -> bool:
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return _text(value).lower() not in _EMPTY_VALUES


def normalize_model_facts(
    raw_facts: Any,
    *,
    message_ref: str = "",
) -> list[dict[str, Any]]:
    """Normalize model-proposed facts without trusting model-supplied sources."""
    items: list[tuple[str, Any, dict[str, Any]]] = []
    if isinstance(raw_facts, dict):
        for field, value in raw_facts.items():
            if _text(field) == "extra_fields" and isinstance(value, dict):
                for extra_key, extra_value in value.items():
                    items.append((f"extra_fields.{_text(extra_key)}", extra_value, {}))
            else:
                items.append((_text(field), value, {}))
    elif isinstance(raw_facts, list):
        for item in raw_facts[:64]:
            if not isinstance(item, dict):
                continue
            field = _text(item.get("field") or item.get("name"))
            value = item.get("value")
            if field == "extra_fields" and isinstance(value, dict):
                for extra_key, extra_value in value.items():
                    items.append((f"extra_fields.{_text(extra_key)}", extra_value, item))
                continue
            items.append((field, value, item))

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = datetime.now().isoformat(timespec="seconds")
    for field, value, source_item in items:
        field = _FACT_ALIASES.get(field, field)
        if not (
            field in _ALLOWED_FACT_FIELDS
            or field.startswith("extra_fields.")
        ):
            continue
        if not _FIELD_RE.fullmatch(field) or field in seen or not _usable_value(value):
            continue
        if field.split(".", 1)[0] in {"tool", "action", "actions", "intent"}:
            continue
        seen.add(field)
        source_span = _text(source_item.get("source_span") or source_item.get("evidence"))[:300]
        out.append({
            "field": field,
            "value": value,
            "source": "user_message_model",
            "source_ref": message_ref,
            "source_span": source_span,
            "confidence": _confidence(source_item.get("confidence"), 0.8),
            "status": "user_stated_candidate",
            "sources": [{
                "source": "user_message_model",
                "source_ref": message_ref,
                "source_span": source_span,
                "updated_at": now,
            }],
            "updated_at": now,
        })
    return out


def merge_fact_candidates(*groups: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Merge fact groups in ascending trust order."""
    result: list[dict[str, Any]] = []
    for group in groups:
        result = merge_user_facts(result, [item for item in (group or []) if isinstance(item, dict)])
    return result


def user_fact_map(facts: Iterable[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in facts or []:
        if not isinstance(item, dict):
            continue
        field = _text(item.get("field"))
        if not field or not _usable_value(item.get("value")):
            continue
        if item.get("source") not in USER_FACT_SOURCES:
            continue
        if item.get("status") not in USER_FACT_STATUSES:
            continue
        out[field] = item
    return out


def facts_to_args(facts: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    """Return ordinary tool args from trusted user facts only."""
    out: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for field, fact in user_fact_map(facts).items():
        value = fact.get("value")
        if field.startswith("extra_fields."):
            key = field.split(".", 1)[1]
            if key:
                extra[key] = value
        elif field not in out:
            out[field] = value
    if extra:
        out["extra_fields"] = {**(out.get("extra_fields") or {}), **extra}
    return out


def _comparable(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return re.sub(r"\s+", "", text).lower()


def _evidence_field_source(item: dict[str, Any], field: str) -> str:
    """Return the source of one evidence field without trusting generic vision as OCR."""
    field_sources = item.get("field_sources")
    if isinstance(field_sources, dict):
        source = _text(field_sources.get(field))
        if source:
            return source
    if field == "record_date":
        source = _text(item.get("observed_date_source"))
        if source:
            return source
    watermark_fields = item.get("watermark_fields")
    watermark_keys = {
        "record_date": ("taken_at", "record_date", "date"),
        "project": ("project", "project_name"),
        "location": ("location", "address"),
        "section": ("section", "work_area"),
        "weather": ("weather",),
    }.get(field, ())
    if isinstance(watermark_fields, dict) and any(_text(watermark_fields.get(key)) for key in watermark_keys):
        return "watermark"
    return _text(item.get("source")) or "evidence"


def _objective_source(source: str) -> bool:
    return source in OBJECTIVE_EVIDENCE_SOURCES


def detect_source_conflicts(
    facts: Iterable[dict[str, Any]] | None,
    evidence: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Report disagreements and apply priority only to watermark/EXIF fields."""
    user_facts = user_fact_map(facts)
    evidence_values: dict[str, list[str]] = {}
    evidence_sources: dict[str, list[str]] = {}
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        for field, keys in {
            "record_date": ("record_date", "observed_date"),
            "project": ("project",),
            "location": ("location",),
            "section": ("section",),
            "weather": ("weather",),
        }.items():
            value = next((_text(item.get(key)) for key in keys if _text(item.get(key))), "")
            observed = evidence_values.get(field, [])
            known = {_comparable(v) for v in observed}
            source = _evidence_field_source(item, field)
            if value and _comparable(value) not in known:
                evidence_values.setdefault(field, []).append(value)
                evidence_sources.setdefault(field, []).append(source)
            elif value and _objective_source(source):
                # The same value may first arrive as generic vision and later be
                # confirmed by OCR/EXIF. Keep values and sources index-aligned.
                index = next(
                    i for i, existing in enumerate(observed)
                    if _comparable(existing) == _comparable(value)
                )
                sources = evidence_sources.setdefault(field, [""] * len(observed))
                sources[index] = source

    conflicts: list[dict[str, Any]] = []
    for field, observed in evidence_values.items():
        fact = user_facts.get(field)
        if not fact:
            continue
        user_value = fact.get("value")
        normalized_user = _comparable(user_value)
        normalized_observed = {_comparable(value) for value in observed}
        if normalized_user and normalized_observed and normalized_user not in normalized_observed:
            sources = evidence_sources.get(field) or []
            objective_index = next(
                (index for index, source in enumerate(sources) if _objective_source(source)),
                None,
            )
            if objective_index is None:
                preferred_value = user_value
                preferred_source = _text(fact.get("source")) or "user_message"
                resolution = "user_value_preferred_pending_evidence_confirmation"
            else:
                preferred_value = observed[objective_index]
                preferred_source = "watermark_or_exif"
                resolution = "evidence_value_preferred_pending_confirmation"
            conflicts.append({
                "field": field,
                "user_value": user_value,
                "evidence_values": observed,
                "evidence_sources": sources,
                "preferred_value": preferred_value,
                "preferred_source": preferred_source,
                "status": "conflict",
                "resolution": resolution,
                "source_ref": fact.get("source_ref") or "",
            })
    return conflicts


def fact_context_fields(facts: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    """Compact field/status summary for plans and diagnostics."""
    return {
        field: {
            "value": fact.get("value"),
            "source": fact.get("source"),
            "status": fact.get("status"),
            "confidence": fact.get("confidence", 0.0),
            "source_ref": fact.get("source_ref") or "",
        }
        for field, fact in user_fact_map(facts).items()
    }
