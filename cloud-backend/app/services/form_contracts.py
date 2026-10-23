"""Stable helpers for versioning generated form definitions."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def contract_version(form_code: str, forms: dict[str, Any]) -> str:
    """Fingerprint the full executable contract, independent of generated module code."""
    form = forms.get(form_code)
    if not isinstance(form, dict):
        return ""
    payload = json.dumps(form, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fields_of(form_code: str) -> list[dict[str, Any]]:
    """Return one normalized Agent field contract for any registered form.

    The richer record field specification remains authoritative when present.
    Other forms are projected from their editable form schema, without
    implying that they can be materialized as a WorkRecord.
    """
    from app.services import provform_schema, record_field_spec

    fields = record_field_spec.fields_of(form_code)
    if fields:
        return fields
    schema = provform_schema.get_schema(form_code)
    if not schema:
        return []
    content_map = provform_schema.CONTENT_MAP.get(form_code, {})
    projected: list[dict[str, Any]] = []
    for section in schema.get("sections", []):
        for item in section.get("fields", []):
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            kind = "auto" if item.get("system") else "ask"
            projected.append({
                "key": key,
                "arg": key,
                "canonical": content_map.get(key, key),
                "label": str(item.get("label") or key),
                "raw_label": str(item.get("label") or key),
                "section": str(section.get("title") or ""),
                "table_type": _field_type(str(item.get("type") or "text")),
                "kind": kind,
                "required": bool(item.get("required")),
                "options": list(item.get("options") or []),
                "maxlen": item.get("maxlen"),
            })
    return projected


def contract_for(form_code: str) -> dict[str, Any]:
    """Return the versioned normalized contract and its source metadata."""
    from app.services import form_registry, provform_schema, record_field_spec

    fields = fields_of(form_code)
    record_contract = record_field_spec.FORMS.get(form_code)
    schema = provform_schema.get_schema(form_code)
    source = "record_field_spec" if record_contract else "provform_schema"
    payload = {
        "form_code": form_code,
        "source": source,
        "schema_version": (schema or {}).get("version"),
        "fields": fields,
        "signoffs": (record_contract or schema or {}).get("signoffs", []),
    }
    form = form_registry.FORM_BY_CODE.get(form_code)
    payload["name"] = (record_contract or {}).get("name") or (schema or {}).get("name") or (form.name if form else form_code)
    payload["version"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16] if fields else ""
    return payload


def _field_type(value: str) -> str:
    return {
        "date": "日期", "datetime": "日期时间", "time": "日期时间",
        "number": "数字", "select": "枚举", "textarea": "文本",
        "text": "文本",
    }.get(value, value)
