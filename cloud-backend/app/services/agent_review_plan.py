"""Validation and revision rules for multi-event agent review plans."""
from __future__ import annotations

from typing import Any


ALLOWED_FORMS = {
    "gd_b_212_pangzhan", "gd_b_213_pingxing", "gd_b_214_xunshi",
    "gd_b_215_tongzhi", "gdaq_30308_yinhuan", "acceptance_record",
    "gd_b_220_rizhi", "gd_b_221_yuebao", "gd_b_219_cailiao",
    "gdaq_30306_weida", "anquan_jianli_rizhi",
}

# These form definitions can be materialized from the existing WorkRecord model.
EXECUTABLE_RECORD_FORMS = {
    "gd_b_212_pangzhan", "gd_b_213_pingxing", "gd_b_214_xunshi",
    "acceptance_record", "gdaq_30306_weida",
}


def validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("整理计划版本无效")
    if not isinstance(plan.get("request_text"), str) or not plan["request_text"].strip():
        raise ValueError("本次整理要求不能为空")
    evidence = plan.get("evidence")
    events = plan.get("events")
    unassigned = plan.get("unassigned_ids")
    if not isinstance(evidence, list) or not isinstance(events, list) or not isinstance(unassigned, list):
        raise ValueError("整理计划结构无效")
    evidence_ids = [item.get("id") for item in evidence if isinstance(item, dict)]
    if len(evidence_ids) != len(evidence) or any(not isinstance(item, str) or not item for item in evidence_ids):
        raise ValueError("资料标识无效")
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("资料标识重复")
    for item in evidence:
        media_id = item.get("media_id")
        if media_id is not None and (type(media_id) is not int or media_id <= 0):
            raise ValueError("资料媒体标识无效")
        if item.get("image") and media_id is None:
            raise ValueError("图片资料必须关联服务端媒体")
    event_ids = [event.get("id") for event in events if isinstance(event, dict)]
    if len(event_ids) != len(events) or any(not isinstance(item, str) or not item for item in event_ids):
        raise ValueError("事项标识无效")
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("事项标识重复")
    assigned: list[str] = []
    for event in events:
        for key in ("title", "location", "content"):
            if not isinstance(event.get(key, ""), str):
                raise ValueError("事项字段格式无效")
        date = event.get("date", "")
        if not isinstance(date, str) or (date and not _valid_date(date)):
            raise ValueError("事项日期无效")
        if event.get("date_confirmed") and not date:
            raise ValueError("已确认日期不能为空")
        event_evidence = event.get("evidence_ids")
        forms = event.get("forms")
        if not isinstance(event_evidence, list) or not isinstance(forms, list):
            raise ValueError("事项资料或表式格式无效")
        if len(set(event_evidence)) != len(event_evidence) or any(item not in evidence_ids for item in event_evidence):
            raise ValueError("事项资料关联无效")
        assigned.extend(event_evidence)
        if len(set(forms)) != len(forms) or any(form not in ALLOWED_FORMS for form in forms):
            raise ValueError("存在不支持的表式")
    if len(set(unassigned)) != len(unassigned) or any(item not in evidence_ids for item in unassigned):
        raise ValueError("待分配资料无效")
    assigned.extend(unassigned)
    if len(assigned) != len(evidence_ids) or set(assigned) != set(evidence_ids):
        raise ValueError("每份资料必须且只能归属一个事项或待分配区")


def review_issues(plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    events = plan.get("events") or []
    if not events:
        issues.append("尚未确定现场事项")
    if plan.get("unassigned_ids"):
        issues.append(f"{len(plan['unassigned_ids'])} 份资料尚未分配")
    for i, event in enumerate(events, 1):
        if not str(event.get("title") or "").strip():
            issues.append(f"事项 {i} 缺少名称")
        if not event.get("date_confirmed"):
            issues.append(f"事项 {i} 日期待确认")
        if not event.get("forms"):
            issues.append(f"事项 {i} 表式待确定")
        if not event.get("evidence_ids") and not str(event.get("content") or "").strip():
            issues.append(f"事项 {i} 缺少工作描述或资料")
    return issues


def _valid_date(value: str) -> bool:
    import datetime
    try:
        return datetime.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False
