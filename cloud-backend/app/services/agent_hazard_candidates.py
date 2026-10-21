"""Validation rules for user-confirmed hazard candidates.

The visual model may propose hazards, but its output is never a formal business
record.  This module normalizes and validates the candidates that the user has
explicitly selected before the router performs one transactional batch write.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


MAX_CANDIDATES = 20
MAX_LOCATION_LENGTH = 256
MAX_SECTION_LENGTH = 128
MAX_WEATHER_LENGTH = 64
MAX_REMARK_LENGTH = 4000


class HazardCandidateError(ValueError):
    """A candidate batch is malformed or contains a non-hazard item."""


def is_hazard_hit(detection: dict[str, Any]) -> bool:
    return bool(detection.get("has_hazard")) or (
        str(detection.get("risk_status") or "").strip() == "存在隐患"
    )


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _optional_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise HazardCandidateError("候选隐患的拍摄时间格式无效") from exc


def normalize_detection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HazardCandidateError("隐患识别结果必须是对象")
    if not is_hazard_hit(value):
        raise HazardCandidateError("未命中隐患的识别结果不能创建正式隐患记录")

    evidence = value.get("evidence") if isinstance(value.get("evidence"), list) else []
    suggestions = value.get("recheck_suggestion") if isinstance(value.get("recheck_suggestion"), list) else []
    top1_risk = _text(
        value.get("top1_risk")
        or value.get("description")
        or value.get("risk_category")
        or value.get("hazard_type"),
        4000,
    )
    return {
        "has_hazard": True,
        "risk_status": _text(value.get("risk_status") or "存在隐患", 32),
        "risk_category": _text(value.get("risk_category") or value.get("hazard_type"), 128),
        "top1_risk": top1_risk or "待核实的现场安全隐患",
        "evidence": [_text(item, 1000) for item in evidence if _text(item, 1000)],
        "uncertainty": _text(value.get("uncertainty"), 2000),
        "recheck_suggestion": [
            _text(item, 1000) for item in suggestions if _text(item, 1000)
        ],
        "scene_related": _text(value.get("scene_related"), 128),
        "confidence": value.get("confidence")
        if isinstance(value.get("confidence"), (int, float))
        and not isinstance(value.get("confidence"), bool)
        else 0.0,
    }


def normalize_candidates(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise HazardCandidateError("请至少选择一个隐患候选")
    if len(items) > MAX_CANDIDATES:
        raise HazardCandidateError(f"一次最多确认 {MAX_CANDIDATES} 条隐患候选")

    normalized: list[dict[str, Any]] = []
    media_ids: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            raise HazardCandidateError("隐患候选格式无效")
        media_id = item.get("media_id")
        if isinstance(media_id, bool) or not isinstance(media_id, int) or media_id <= 0:
            raise HazardCandidateError("隐患候选缺少有效的图片媒体标识")
        if media_id in media_ids:
            raise HazardCandidateError("同一张图片不能重复创建隐患记录")
        media_ids.add(media_id)
        normalized.append({
            "media_id": media_id,
            "detection": normalize_detection(item.get("detection")),
            "section": _text(item.get("section"), MAX_SECTION_LENGTH),
            "location": _text(item.get("location"), MAX_LOCATION_LENGTH),
            "weather": _text(item.get("weather"), MAX_WEATHER_LENGTH),
            "remark": _text(item.get("remark"), MAX_REMARK_LENGTH),
            "taken_at": _optional_datetime(item.get("taken_at")),
        })
    return normalized
