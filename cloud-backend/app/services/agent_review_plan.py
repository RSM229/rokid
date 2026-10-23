"""Validation and revision rules for multi-event agent review plans."""
from __future__ import annotations

from typing import Any

from app.services import agent_form_requirements, form_registry


# Recognize every registered form so the Agent can preserve the user's choice
# and explain its route. Recognition does not imply WorkRecord generation.
ALLOWED_FORMS = {spec.form_code for spec in form_registry.FORMS}

# This is the one authoritative declaration of which forms can be materialized
# as a WorkRecord. Forms with separate business models or aggregate workflows
# intentionally remain outside this set.
EXECUTABLE_RECORD_FORMS = {
    spec.form_code for spec in form_registry.FORMS
    if spec.agent_work_record_type or spec.agent_business_route
}

RECORD_TYPES = {"巡视", "验收", "见证", "旁站", "平行检验", "起重机械安拆检查", "待定"}

# A selected form is part of the user's reviewed contract and must match the
# record type persisted by the executor.
FORM_RECORD_TYPES = {
    spec.form_code: spec.agent_work_record_type or spec.agent_record_type
    for spec in form_registry.FORMS
    if spec.agent_work_record_type or spec.agent_business_route
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
        fields = event.get("fields", {})
        if not isinstance(fields, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in fields.items()
        ):
            raise ValueError("事项专用字段格式无效")
        if len(set(event_evidence)) != len(event_evidence) or any(item not in evidence_ids for item in event_evidence):
            raise ValueError("事项资料关联无效")
        assigned.extend(event_evidence)
        if len(set(forms)) != len(forms) or any(form not in ALLOWED_FORMS for form in forms):
            raise ValueError("存在不支持的表式")
        record_type = event.get("record_type", "待定")
        if record_type not in RECORD_TYPES:
            raise ValueError("工作记录类型无效")
        confidence = event.get("record_type_confidence", 0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("记录类型置信度无效")
        unresolved = event.get("unresolved_conflicts", [])
        if not isinstance(unresolved, list) or any(not isinstance(item, str) or not item for item in unresolved):
            raise ValueError("事项冲突字段格式无效")
        if "record_type_confirmed" in event and not isinstance(event["record_type_confirmed"], bool):
            raise ValueError("记录类型确认状态无效")
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
        if not event.get("evidence_ids") and not str(event.get("content") or "").strip():
            issues.append(f"事项 {i} 缺少工作描述或资料")
        if str(event.get("record_type") or "待定") == "待定":
            issues.append(f"事项 {i} 请选择工作记录类型")
        elif event.get("record_type_confirmed") is not True:
            issues.append(f"事项 {i} 的记录类型“{event.get('record_type')}”是 Agent 建议，请由用户确认或修改")
        labels = {
            "project_name": "项目", "owner_unit": "建设单位", "construction_unit": "施工单位",
            "supervision_unit": "监理单位", "supervision_org": "项目监理机构",
            "inspector": "检查人员", "location": "地点", "section": "施工部位",
        }
        for field in event.get("unresolved_conflicts") or []:
            issues.append(f"事项 {i} 的{labels.get(field, field)}存在多个候选值，请先选择")
        resolved = agent_form_requirements.resolve_event_form(plan, event)
        selected_forms = [str(item) for item in (event.get("forms") or []) if str(item)]
        if len(selected_forms) > 1:
            issues.append(
                f"事项 {i} 目前只能绑定一种正式工作记录表式；请拆分事项或先选择单一表式，不能静默遗漏其他表"
            )
        if selected_forms and resolved.get("form_code"):
            # resolve_event_form intentionally accepts the first valid form so
            # older clients remain readable.  Confirmation is stricter: an
            # explicitly selected form must match the record type that the
            # executor will persist.
            selected_form = selected_forms[0]
            expected_type = FORM_RECORD_TYPES.get(selected_form)
            record_type = str(event.get("record_type") or "待定")
            if selected_form not in form_registry.FORM_BY_CODE:
                issues.append(f"事项 {i} 的表式选择无效，请重新选择有效表式")
            elif selected_form not in EXECUTABLE_RECORD_FORMS:
                form_name = form_registry.FORM_BY_CODE[selected_form].name
                issues.append(
                    f"事项 {i} 选择的表式“{form_name}”已按自身字段合同复核，但尚未接入 Agent 业务记录物化流程；"
                    "请使用该表式自己的业务入口，不能将其转换成其他类型记录"
                )
            elif selected_form != resolved.get("form_code"):
                issues.append(f"事项 {i} 的表式选择无效，请重新选择有效表式")
            elif expected_type and record_type != "待定" and expected_type != record_type:
                issues.append(
                    f"事项 {i} 的记录类型“{record_type}”与表式“{resolved.get('form_name') or selected_form}”不一致，请重新选择"
                )
        pinned_contract = (plan.get("form_contracts") or {}).get(resolved.get("form_code"))
        if pinned_contract and pinned_contract != resolved.get("contract_version"):
            issues.append(
                f"事项 {i} 使用的{resolved.get('form_name') or '表单'}规则已更新，请重新核对该事项字段"
            )
        if resolved.get("missing_labels"):
            issues.append(
                f"事项 {i} 的{resolved.get('form_name') or '记录表'}还需补充："
                + "、".join(resolved["missing_labels"])
            )
        for conflict in resolved.get("conflicts") or []:
            choices = " / ".join(dict.fromkeys(
                item.get("value", "") for item in conflict.get("candidates", []) if item.get("value")
            ))
            issues.append(
                f"事项 {i} 的{conflict.get('label') or conflict.get('canonical')}有多个水印候选值（{choices}），请确认采用哪一个"
            )
        for invalid in resolved.get("invalid_fields") or []:
            issues.append(
                f"事项 {i} 的{invalid.get('label') or invalid.get('canonical')}填写无效：{invalid.get('message')}"
            )
    return issues


def pin_form_contracts(plan: dict[str, Any], *, preserve_existing: bool = False) -> dict[str, Any]:
    """Bind a draft revision to the exact form definitions it was reviewed against."""
    existing = plan.get("form_contracts") if isinstance(plan.get("form_contracts"), dict) else {}
    contracts: dict[str, str] = {}
    for event in plan.get("events") or []:
        if not isinstance(event, dict):
            continue
        resolved = agent_form_requirements.resolve_event_form(plan, event)
        code = resolved.get("form_code")
        version = resolved.get("contract_version")
        if code and version:
            # Existing fingerprints describe the contract the user actually
            # reviewed. Never silently replace a stale fingerprint while
            # saving edits; confirmation must surface the drift and require a
            # fresh draft/review against the new schema.
            contracts[code] = str(existing.get(code) or version) if preserve_existing else str(version)
    return {**plan, "form_contracts": contracts}


def _valid_date(value: str) -> bool:
    import datetime
    try:
        return datetime.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False
