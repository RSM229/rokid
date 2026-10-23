"""监理记录字段口径。草稿可补录，提交前校验；结论与签章不得自动代办。"""
from datetime import date, datetime, timedelta, timezone
from fastapi import HTTPException

CN_TZ = timezone(timedelta(hours=8))
ACCEPTANCE_RESULTS = ("合格", "不合格", "待整改")


def now_local():
    return datetime.now(CN_TZ).replace(tzinfo=None)


def business_date(record):
    value = getattr(record, "record_date", None)
    if value:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    return (getattr(record, "station_start_time", None) or
            getattr(record, "inspection_start_time", None) or
            getattr(record, "created_at", None) or now_local()).date()


# 「无异常」类结论：不是「发现问题」，落文书正文时不冠「发现问题：」前缀（与 agent_tools._NO_ISSUE_HINTS 同口径）
_NO_ISSUE_HINTS = ("符合要求", "无异常", "没有发现问题", "没有问题", "无问题", "未发现问题", "正常", "一切正常")
_INSTRUCTION_ONLY_HINTS = (
    "记录这个工作", "记录今天的工作", "帮我记录", "生成记录", "生成工作记录",
    "整理一下", "帮我整理", "创建记录", "建立记录", "记录一下",
)


def is_instruction_only(value) -> bool:
    """Whether text is only a command to the agent, not a business field value."""
    text = str(value or "").strip()
    if not text:
        return False
    compact = "".join(text.split()).strip("，。；;：:")
    return compact in {item.strip("，。；;：:") for item in _INSTRUCTION_ONLY_HINTS}


def problem_text(record):
    problems = str(getattr(record, "problems", "") or "").strip()
    treatment = str(getattr(record, "treatment", "") or "").strip()
    lines = []
    if problems and not is_instruction_only(problems):
        if any(k in problems for k in _NO_ISSUE_HINTS):
            lines.append(problems)  # 「符合要求」原样落正文，避免出现「发现问题：符合要求」的自相矛盾
        else:
            lines.append(f"发现问题：{problems}")
    if treatment and not is_instruction_only(treatment):
        lines.append(f"处理情况：{treatment}")
    return "\n".join(lines)


def validate_times(start, end):
    if (start and start.tzinfo) or (end and end.tzinfo):
        raise HTTPException(400, "现场时间请使用北京时间，不带时区偏移")
    if start and end and end < start:
        raise HTTPException(400, "结束时间不能早于开始时间")


def validate_record(record, project):
    """对正式提交校验。缺失数据不以默认合格、默认人数或虚构单位替代。"""
    missing = []
    content = record.content_json if isinstance(record.content_json, dict) else {}
    confirmed = {}
    for key in ("form_context", "form_field_candidates", "field_resolutions"):
        value = content.get(key)
        if isinstance(value, dict):
            confirmed.update(value)
    construction_unit = (
        getattr(project, "construction_unit", None)
        or confirmed.get("construction_unit")
        or confirmed.get("contractor")
    )
    if not project_context(project).get("project_name"):
        missing.append("工程项目名称")
    rtype = record.record_type
    if rtype in ("巡视", "旁站") and not construction_unit:
        missing.append("施工单位")
    if rtype == "巡视":
        if not record.section:
            missing.append("巡视工程部位")
        if not record.work_content:
            missing.append("巡视内容")
        # 施工人数：房建 GD-B-214 巡视记录表无此栏，仅在填写时校验格式，不作必填
    elif rtype == "旁站":
        if not (record.station_location or record.section):
            missing.append("旁站关键部位")
        if not record.station_process:
            missing.append("旁站关键工序")
    elif rtype == "验收":
        if not record.section:
            missing.append("验收部位")
        if not ((record.content_json or {}).get("acceptance_content") or record.work_content):
            missing.append("验收内容")
        if not record.acceptance_participants:
            missing.append("验收单位及人员")
        if record.acceptance_result not in ACCEPTANCE_RESULTS:
            missing.append("人工确认的验收结论")
    if missing:
        raise HTTPException(400, "请补全：" + "、".join(missing))
    validate_times(record.station_start_time, record.station_end_time)
    validate_times(record.inspection_start_time, record.inspection_end_time)


def project_fields(project):
    return project_context(project)


_PROJECT_FIELD_ALIASES = {
    "project_name": ("project_name", "name", "project", "engineering_name", "工程名称"),
    "owner_unit": ("owner_unit", "owner_unit_name", "建设单位", "建设方"),
    "construction_unit": ("construction_unit", "contractor", "contractor_unit", "施工单位"),
    "supervision_unit": ("supervision_unit", "supervision_unit_name", "监理单位"),
    "supervision_org": ("supervision_org", "supervision_department", "项目监理机构"),
    "acceptance_participants": ("acceptance_participants", "acceptance_unit", "验收单位及人员"),
}


def project_context(project) -> dict[str, str]:
    """Normalize optional project facts from ORM objects or external dictionaries."""
    result = {}
    for canonical, aliases in _PROJECT_FIELD_ALIASES.items():
        value = ""
        for alias in aliases:
            raw = project.get(alias) if isinstance(project, dict) else getattr(project, alias, None)
            value = _clean_field_value(raw)
            if value:
                break
        result[canonical] = value
    return result


_BUSINESS_FIELD_ALIASES = {
    "project_name": ("project_name", "project", "engineering_name", "工程名称"),
    "owner_unit": ("owner_unit", "owner_unit_name", "建设单位"),
    "construction_unit": (
        "construction_unit", "contractor", "contractor_unit", "施工单位",
    ),
    "supervision_unit": (
        "supervision_unit", "supervision_unit_name", "监理单位",
    ),
    "supervision_org": ("supervision_org", "项目监理机构"),
    "inspector": (
        "inspector", "person", "checker", "inspection_person", "检查人员",
    ),
    "weather": ("weather",),
    "location": ("location", "address", "地点"),
    "section": ("section", "work_area", "施工区域", "部位"),
}


def _clean_field_value(value) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in {"unknown", "none", "null", "未知", "未识别"} else text


def _identity_key(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _content_evidence_maps(record) -> list[tuple[str, dict]]:
    """Return evidence maps in descending authority order.

    ``field_resolutions`` is the user's confirmed decision. Watermark evidence
    comes next; candidate maps are only fallback material.
    """
    content = record.content_json if isinstance(record.content_json, dict) else {}
    maps: list[tuple[str, dict]] = []
    for key, source in (
        ("confirmed_fields", "user_confirmed"),
        ("field_resolutions", "user_confirmed"),
        ("prov_fields", "user_confirmed"),
        ("extracted_watermark_fields", "watermark"),
    ):
        value = content.get(key)
        if isinstance(value, dict):
            maps.append((source, value))
    for item in content.get("evidence_watermark_fields") or []:
        if isinstance(item, dict) and isinstance(item.get("fields"), dict):
            maps.append(("watermark", item["fields"]))
    for key in ("form_context", "extracted_fields", "form_field_candidates"):
        value = content.get(key)
        if isinstance(value, dict):
            maps.append(("candidate", value))
    return maps


def _collect_business_candidates(record) -> dict[str, list[dict[str, str]]]:
    candidates = {field: [] for field in _BUSINESS_FIELD_ALIASES}
    for default_source, fields in _content_evidence_maps(record):
        for canonical, aliases in _BUSINESS_FIELD_ALIASES.items():
            for alias in aliases:
                raw = fields.get(alias)
                if isinstance(raw, dict):
                    value = _clean_field_value(raw.get("value"))
                    source = _clean_field_value(raw.get("source")) or default_source
                else:
                    value = _clean_field_value(raw)
                    source = default_source
                if not value:
                    continue
                if default_source == "user_confirmed":
                    candidates[canonical] = [
                        item for item in candidates[canonical]
                        if _identity_key(item["value"]) != _identity_key(value)
                    ]
                    candidates[canonical].insert(0, {"value": value, "source": source})
                    break
                if not any(
                    _identity_key(existing["value"]) == _identity_key(value)
                    for existing in candidates[canonical]
                ):
                    candidates[canonical].append({"value": value, "source": source})
                break
    return candidates


def business_record_fields(record, project=None) -> dict:
    """Resolve display/export facts while keeping ``project_id`` as scope.

    A WorkRecord always belongs to the project selected by ``project_id``.  An
    old watermark project name is evidence, not the formal project label.  The
    only exception is an explicit Agent decision saved in ``field_resolutions``
    or ``prov_fields``; that decision remains auditable and overrides the label.
    """
    system = project_fields(project)
    candidates = _collect_business_candidates(record)
    content = record.content_json if isinstance(record.content_json, dict) else {}
    explicit_project = ""
    field_sources = content.get("field_sources")
    field_sources = field_sources if isinstance(field_sources, dict) else {}
    project_source = str(
        field_sources.get("project_name") or field_sources.get("project") or ""
    ).strip()
    confirmed_fields = content.get("confirmed_fields")
    if isinstance(confirmed_fields, dict):
        for alias in _BUSINESS_FIELD_ALIASES["project_name"]:
            raw = confirmed_fields.get(alias)
            explicit_project = _clean_field_value(
                raw.get("value") if isinstance(raw, dict) else raw
            )
            if explicit_project:
                break
    for key in ("field_resolutions",):
        if explicit_project or project_source not in {"user_message", "user_confirmed"}:
            break
        fields = content.get(key)
        if not isinstance(fields, dict):
            continue
        for alias in _BUSINESS_FIELD_ALIASES["project_name"]:
            raw = fields.get(alias)
            value = _clean_field_value(raw.get("value") if isinstance(raw, dict) else raw)
            if value:
                explicit_project = value
                break
        if explicit_project:
            break
    resolved: dict[str, str] = {}
    conflicts: dict[str, dict] = {}
    candidate_values: dict[str, list[str]] = {}

    for field, values in candidates.items():
        deduped: list[dict[str, str]] = []
        for item in values:
            if not any(
                _identity_key(item["value"]) == _identity_key(existing["value"])
                for existing in deduped
            ):
                deduped.append(item)
        system_value = _clean_field_value(system.get(field))
        confirmed_value = next(
            (item["value"] for item in deduped if item.get("source") == "user_confirmed"),
            "",
        )
        if field == "project_name":
            # Never let a legacy watermark quietly become the formal project
            # after the user has switched projects.  A confirmed Agent choice is
            # the only non-system value that may remain as the formal label.
            preferred = explicit_project or confirmed_value or system_value or (deduped[0]["value"] if deduped else "")
        else:
            preferred = confirmed_value or (deduped[0]["value"] if deduped else system_value)
        resolved[field] = preferred
        candidate_values[field] = [item["value"] for item in deduped]
        if field == "project_name":
            station_project = _clean_field_value(getattr(record, "station_project_name", None))
            if station_project and not any(
                _identity_key(station_project) == _identity_key(existing)
                for existing in candidate_values[field]
            ):
                candidate_values[field].append(station_project)
        if not preferred and field == "project_name":
            preferred = resolved[field] = _clean_field_value(
                getattr(record, "station_project_name", None)
            )
            if preferred:
                candidate_values[field].append(preferred)
        distinct: list[str] = []
        for value in [preferred, system_value, *candidate_values[field]]:
            if value and not any(
                _identity_key(value) == _identity_key(existing)
                for existing in distinct
            ):
                distinct.append(value)
        if len(distinct) > 1:
            conflicts[field] = {
                "preferred": preferred,
                "system": system_value,
                "candidates": distinct,
            }

    business_project = resolved.get("project_name") or system.get("project_name") or ""
    system_project = system.get("project_name") or ""
    watermark_project = next(
        (
            value for value in candidate_values.get("project_name", [])
            if system_project and _identity_key(value) != _identity_key(system_project)
        ),
        "",
    )
    mismatch = bool(
        watermark_project and system_project
        and _identity_key(watermark_project) != _identity_key(system_project)
    )
    return {
        **resolved,
        "project_name": business_project,
        "business_project_name": business_project,
        "system_project_name": system_project,
        "watermark_project_name": watermark_project,
        "project_mismatch": mismatch,
        "field_conflicts": conflicts,
        "field_candidates": candidate_values,
    }


def invalidate_document(doc):
    """保留签署历史，旧版本签章不可再用于新内容。"""
    doc.content_revision = (doc.content_revision or 1) + 1
    doc.pdf_path = None
    doc.docx_path = None
    doc.signed_pdf_path = None
    doc.signed_at = None
    doc.signed_by = None
    doc.reviewed_at = None
    doc.reviewed_by = None
    if doc.content:
        content = dict(doc.content)
        content.pop("review_date", None)
        doc.content = content
    if doc.content:
        content = dict(doc.content)
        content.pop("review_date", None)
        doc.content = content


def validate_document(doc):
    """正式文书的完整性校验，也覆盖绕过工作记录直接提交的路径。"""
    import math
    c = doc.content or {}
    required = [("project_name", "工程项目名称"), ("date", "业务日期")]
    if doc.doc_type in ("旁站记录", "巡视记录"):
        required += [("construction_unit", "施工单位"), ("section", "工程部位")]
    if doc.doc_type == "巡视记录":
        required += [("content", "巡视内容")]
        # 施工人数：房建 GD-B-214 无此栏 → 非必填；填了才校验是否为非负整数
        count = c.get("worker_count")
        if count not in (None, ""):
            try:
                if isinstance(count, bool) or int(count) != float(count) or int(count) < 0:
                    raise ValueError()
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(400, "施工人数须为非负整数")
    elif doc.doc_type == "旁站记录":
        required += [("station_process", "旁站关键工序")]
    elif doc.doc_type == "验收记录":
        required += [("section", "验收部位"), ("acceptance_content", "验收内容"), ("acceptance_participants", "验收单位及人员")]
        if c.get("acceptance_result") not in ACCEPTANCE_RESULTS:
            raise HTTPException(400, "请人工确认验收结论")
    elif doc.doc_type == "平行检查记录":
        required += [("section", "检查部位"), ("acceptance_content", "检查项目"), ("record", "检查记录")]
        if c.get("conclusion") not in ("合格", "不合格"):
            raise HTTPException(400, "请人工确认平行检查结论")
    elif doc.doc_type == "监理日志":
        required += [("weather_morning", "天气"), ("construction_progress", "施工进展"), ("supervision_section", "监理工作情况"), ("problems_section", "存在问题及处理情况")]
        try:
            high, low = float(c.get("temperature_high", "")), float(c.get("temperature_low", ""))
            if not math.isfinite(high) or not math.isfinite(low) or high < low:
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(400, "请补填有效气温，最高气温不能低于最低气温")
    elif "通知单" in doc.doc_type:
        required += [("construction_unit", "施工单位"), ("supervision_org", "项目监理机构"), ("section", "问题部位")]
        if not (c.get("problems") or c.get("problem_desc") or c.get("hazard_description")):
            required.append(("problems", "存在问题"))
    missing = [label for key, label in required if c.get(key) is None or not str(c.get(key, "")).strip()]
    if missing:
        raise HTTPException(400, "请补全文书：" + "、".join(missing))
    try:
        date.fromisoformat(str(c["date"]))
    except (TypeError, ValueError):
        raise HTTPException(400, "业务日期格式无效")

