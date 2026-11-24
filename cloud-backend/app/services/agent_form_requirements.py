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
from app.services.time_normalization import normalize_datetime_value, parse_local_date
from app.services.watermark_ocr import match_form_field

# 🔴 验收类型推断与工具层（agent_tools._infer_acceptance_type）必须同源：
#    抄一份会造成生产与跑分层漂移。agent_tools 对本模块只有函数级导入，
#    模块级反向引用不构成循环导入。
from app.services.agent_tools import _infer_acceptance_type


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
    # 🔴 注意：taken_at（照片拍摄时间）不能出现在 date/time/sample_date 的别名里。
    #   _CANONICAL_INPUT_FIELDS 是反向索引，同一个 alias 被多个 canonical 引用时
    #   后定义者覆盖先定义者，taken_at 会从 start_time 漂到 sample_date，
    #   导致巡视/旁站等记录的 date/start_time 仍判缺失、反过来问用户拍摄时间
    #   （2026-11-11 真机：水印有时间却被问时间）。taken_at 由
    #   resolve_event_form 的水印分支显式拆分到 record_date + start_time。
    "start_time": ("start_time", "inspection_start_time", "station_start_time"),
    "end_time": ("end_time", "inspection_end_time", "station_end_time"),
    # 🔴 record_date 只能有一个反向目标（date）。若同时挂在 time/sample_date 下，
    #   反向索引后定义者覆盖先定义者 → record_date → sample_date，同 taken_at 一样
    #   把日期塞进没人看的字段、date 判缺失（2026-11-11 实测同一 bug）。
    #   sample_date 是见证的「取样日期」，与 record_date（记录日期）语义不同，不做兜底。
    "date": ("date", "record_date"),
    "time": ("time",),
    "sample_date": ("sample_date",),
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

# 参建单位/项目身份字段：多来源不一致时按「用户明说 > 当前项目 > 水印」裁决，
# 不再让用户逐项勾选（2026-11-03 用户要求「少输入、智能体帮忙思考」）。
PROJECT_PARTY_FIELDS = {"project_name", "construction_unit", "supervision_unit", "supervision_org", "owner_unit"}

# 水印拍摄时间「2026-10-19 15:42:10 / 2026-10-19T15:42 / 2026-10-19 15时42分」的解析。
# 与 agent.py 的 _TIME_RE/_DATE_RE 同规则，但抽到本模块供 resolve_event_form 直接使用。
_WM_DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
_WM_CLOCK_RE = re.compile(r"(\d{1,2})\s*[:：时]\s*(\d{2})")


def _parse_watermark_date(value: Any) -> str:
    """水印拍摄时间文本 → YYYY-MM-DD；取不到返回空串。"""
    m = _WM_DATE_RE.search(str(value or ""))
    if not m:
        return ""
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_watermark_clock(value: Any) -> str:
    """水印拍摄时间文本 → HH:MM；取不到返回空串。"""
    m = _WM_CLOCK_RE.search(str(value or ""))
    if not m:
        return ""
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


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
                    # agent_tools 把「表上有、工具没单列参数」的字段统一收在
                    # extra_fields 槽位里。合同校验若只展开一层，会把这些已填值
                    # 全判为缺失：验收记录卡在「验收类型」、创建编号为空
                    # （2026-11-09 真机复现）。这里把它当作合法字段输入再展开一层。
                    extra = nested.get("extra_fields")
                    if isinstance(extra, dict):
                        yield f"event.{key}.{nested_key}.extra_fields", extra


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

    # 🔴 2026-11-24 带图草稿「举牌验收照片」永久卡栏（UI 复验第四轮）：
    #    photo 栏 values 永远为空（照片是实物不是文本），事件明明挂着
    #    evidence_ids 图片却仍判 missing → 逐栏追问推进到照片栏后死循环
    #    （「跳过」也不消费）。与 resolve_record_form 的 inspection_photos
    #    映射同语义：事件已挂图时 photo 视为已满足。
    if form_code == "acceptance_record" and event.get("evidence_ids"):
        _photo_count = len([x for x in event.get("evidence_ids") if str(x or "").strip()])
        if _photo_count:
            add("photo", f"已附{_photo_count}张现场照片", "evidence", "evidence_extracted")

    # 2026-11-03 当前项目优先于照片水印：先把参建单位/项目字段按当前项目写入，
    # 水印 add() 遇到已存在值会跳过，显式用户选择仍会无条件覆盖（用户明说优先）。
    for _pf in PROJECT_PARTY_FIELDS:
        _pv = _text(system_project.get(_pf))
        if _usable(_pv):
            add(_pf, _pv, "system_project", "system_configured")

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

        # 🔴 2026-11-11 水印拍摄时间（taken_at）→ 记录日期 + 本类型「开始时间」。
        #    此前 taken_at 在反向别名索引里被 sample_date 抢占（date/time/sample_date
        #    三处误挂、后定义者覆盖先定义者），巡视/旁站记录的 date 与 start_time
        #    永远判缺失 → 系统反问「拍摄时间是多少」而用户明明已发带时间水印照片。
        #    与 apply_prefill（agent.py）同规则：record_type 为旁站落 station_start_time，
        #    否则落 start_time（巡视等）。date 落到 record_date（date 由事件级回填兜底）。
        _wm_taken_raw = str(
            (evidence.get("watermark_fields") or {}).get("taken_at") or ""
        ).strip()
        if _wm_taken_raw:
            _wm_iso_date = _parse_watermark_date(_wm_taken_raw)
            if _wm_iso_date:
                add("record_date", _wm_iso_date, "watermark", "evidence_extracted")
            _wm_clock = _parse_watermark_clock(_wm_taken_raw)
            if _wm_clock:
                if record_type == "旁站":
                    add("station_start_time", _wm_clock, "watermark", "evidence_extracted")
                else:
                    add("start_time", _wm_clock, "watermark", "evidence_extracted")

    # 🔴 2026-11-23 验收类型水印推断（用户「水印有类型了为什么还问我」）：
    #    水印 OCR 的原文行在前端以 label='识别文字' 进 watermark_items，
    #    match_form_field 对它永远返回 None → acceptance_type 判缺 → 整理卡
    #    反复问「验收类型」，而照片上明明印着「检验批质量验收记录」。
    #    与工具层（agent_tools.py acc_src 推断）同源：_infer_acceptance_type
    #    只认受控枚举词，命中即作为候选填入；用户显式选择仍无条件覆盖。
    _acc_type_pool: list[str] = []
    for evidence_id in event.get("evidence_ids") or []:
        evidence = evidence_by_id.get(str(evidence_id)) or {}
        for item in evidence.get("watermark_items") or []:
            if isinstance(item, dict) and _usable(item.get("value")):
                _acc_type_pool.append(_text(item.get("value")))
        for _wv in (evidence.get("watermark_fields") or {}).values():
            _acc_type_pool.append(_text(_wv))
        _observation = _text(evidence.get("observation"))
        if _observation:
            _acc_type_pool.append(_observation)
    _acc_type_src = " ".join(
        [str((plan or {}).get("request_text") or ""), str(event.get("content") or "")]
        + _acc_type_pool
    )
    if form_code == "acceptance_record" and not _usable(values.get("acceptance_type")):
        _inferred = _infer_acceptance_type(_acc_type_src)
        if _inferred:
            add("acceptance_type", _inferred, "watermark", "evidence_extracted")

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
    auto_resolutions: list[dict[str, Any]] = []
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

        field_aliases = list(_INPUT_ALIASES.get(canonical, ()))
        if canonical == "acceptance_content":
            field_aliases.extend(("content", "work_content"))
        aliases = [canonical, _text(spec.get("key")), _text(spec.get("arg")), *field_aliases]
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
            # 2026-11-03 自动裁决：不再阻断建档。value 已按优先级
            # （用户明说 > 当前项目 > 水印）采用其一，其余候选留在 candidates
            # 供用户事后点选修改。
            auto_resolutions.append({
                "canonical": canonical,
                "label": _text(spec.get("label")) or canonical,
                "chosen": value,
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
            # 「由系统生成=是」（kind=auto）的栏位由项目信息/文书渲染层带出，
            # 不该算成用户没填：否则像「验收单位及人员」这类项目对象里没有
            # 现成值的栏位会把建档永久卡住（2026-11-09 验收记录复现）。
            "missing": required and kind != "auto" and not _usable(value),
            "conflict": any(item["canonical"] == canonical for item in conflicts),
            "auto_resolved": any(item["canonical"] == canonical for item in auto_resolutions),
            "candidates": candidates,
        }
        if value:
            problem = ""
            if control == "select":
                options = list(spec.get("options") or record_field_spec.options_for(spec))
                if options and value not in options:
                    problem = "请选择合同定义中的有效选项"
            elif control == "date":
                parsed_date = parse_local_date(value)
                if parsed_date is None:
                    problem = "日期格式无效，请使用 YYYY-MM-DD"
                else:
                    value = parsed_date.isoformat()
            elif control == "datetime-local":
                normalized = normalize_datetime_value(
                    value, event.get("date") or values.get("date") or values.get("record_date"),
                )
                if normalized is None:
                    problem = "日期时间格式无法识别，请补充为明确日期和时间"
                else:
                    value = normalized
            elif control == "number":
                try:
                    float(value)
                except ValueError:
                    problem = "此字段需要数字"
            if problem:
                row["validation_error"] = problem
                invalid_fields.append({"canonical": canonical, "label": row["label"], "message": problem})
            else:
                row["value"] = value
        rows.append(row)
        if row["missing"]:
            missing.append(canonical)
            missing_labels.append(row["label"])

    row_values = {row["canonical"]: row["value"] for row in rows}
    start_value = row_values.get("start_time")
    end_value = row_values.get("end_time")
    if start_value and end_value:
        start_dt = normalize_datetime_value(start_value, event.get("date"))
        end_dt = normalize_datetime_value(end_value, event.get("date"))
        if start_dt and end_dt and end_dt < start_dt:
            label = next(
                (_text(row.get("label")) for row in rows if row.get("canonical") == "end_time"),
                "结束时间",
            )
            invalid_fields.append({
                "canonical": "end_time", "label": label,
                "message": "结束时间早于开始时间；若为跨日记录，请明确次日日期",
            })

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
        "auto_resolutions": auto_resolutions,
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

    def replace(key: str, value: Any, source: str = "record", status: str = "user_confirmed") -> None:
        canonical = _CANONICAL_INPUT_FIELDS.get(key, key)
        text = _text(value)
        if not canonical or not _usable(text):
            return
        for selected_key in list(selected):
            if _CANONICAL_INPUT_FIELDS.get(selected_key, selected_key) == canonical:
                selected.pop(selected_key, None)
        selected[canonical] = {"value": text, "source": source, "status": status}

    # Start with confirmed and candidate snapshots, then replace them with the
    # persisted record columns that define the current business record.
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
        "acceptance_participants": getattr(record, "acceptance_participants", None),
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
        direct["content"] = getattr(record, "station_content", None) or getattr(record, "work_content", None)
    elif record_type == "验收":
        acceptance_content = content.get("acceptance_content") or getattr(record, "work_content", None)
        direct["content"] = acceptance_content
        direct["acceptance_content"] = acceptance_content
        # 分组建档把验收结论写在 content_json 顶层（acceptance_type /
        # self_check_conclusion / supervision_conclusion），与 prov_fields 同义；
        # 表式校验只读 prov_fields/记录列时会误判缺项——详情页四栏都有值，
        # 点生成文书仍提示「缺验收类型、自检结论、监理结论」（2026-11-18 真机）。
        for key in ("acceptance_type", "self_check_conclusion", "supervision_conclusion"):
            direct[key] = content.get(key)
        # 「举牌验收照片」canonical=photo，实物落在 inspection_photos 列：
        # 不映射会把已附照片的验收判成缺照片，生成被永久拦住。
        photos = getattr(record, "inspection_photos", None) or []
        if photos:
            direct["photo"] = f"已附{len(photos)}张"
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
        replace(key, value, "record", "user_confirmed")

    cleared_fields: set[str] = set()
    field_edits = content.get("record_field_edits")
    if isinstance(field_edits, dict):
        for key, raw in field_edits.items():
            canonical = _CANONICAL_INPUT_FIELDS.get(str(key), str(key))
            for selected_key in list(selected):
                if _CANONICAL_INPUT_FIELDS.get(selected_key, selected_key) == canonical:
                    selected.pop(selected_key, None)
            value, source, status = _selected_value(raw)
            if _usable(value):
                selected[canonical] = {
                    "value": value,
                    "source": source or "user_edit",
                    "status": status or "user_confirmed",
                }
                cleared_fields.discard(canonical)
            else:
                cleared_fields.add(canonical)

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
        "date": "" if "date" in cleared_fields else date_text,
        "date_source": "record",
        "section": "" if "section" in cleared_fields else str(
            station_location if record_type == "旁站" and _usable(station_location)
            else getattr(record, "section", None) or ""
        ),
        "location": "" if "location" in cleared_fields else str(getattr(record, "location", None) or ""),
        "content": "" if "content" in cleared_fields else str(direct.get("content") or ""),
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
    # 2026-11-03 去除“记录类型需用户确认”门禁：类型一旦被判定（非待定），
    # 智能体即据当前候选直接建档，无需用户逐项点选“确认采用”。
    if record_type == "待定":
        status = "needs_record_type"
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
        "generation_allowed": bool(resolved["write_allowed"]),
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
