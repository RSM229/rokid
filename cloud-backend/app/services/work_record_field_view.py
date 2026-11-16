"""工作记录详情页「按字段清单」渲染的字段视图（2026-11-16）。

唯一真源：``app/services/record_field_spec.py`` 的 ``FORMS`` —— 它由
「监理通各记录关键字（草稿）(1).xlsx /『字段清单』」生成（勿手改生成物）。
本模块**不另造第二份字段表**，只做两件事：

1. 按 xlsx 原表的分组（基本信息／施工情况／监理情况／问题与处理／签章…）
   与字段顺序，把每条记录拼成分组视图；字段名逐字取自 ``FORMS``。
2. 把「字段名」映射到「记录里已经存在的值」（详情 dict / content_json /
   prov_fields 三层取值），并标出 由系统生成 / 是否必填 / 是否缺项。

另保留一个「其他记录信息」分组：详情里有值、但不在该类型字段清单里的
字段（如验收结果、备注、水印识别出的施工内容）也照旧展示 —— 「只搬家
不丢弃」，绝不因为换了渲染方式就丢数据。

配套前端：wechat-miniprogram/miniprogram/pages/work/index.wxml 通用分组渲染。
"""
from __future__ import annotations

from typing import Any, Mapping

from app.services import record_field_spec

# 「记录类型 → 表式」的补充别名：生成物 RECORD_TYPE_TO_FORM 未覆盖的类型，
# 在这里指到同一份 FORMS，不复制字段定义。
_EXTRA_TYPE_FORMS: dict[str, str] = {
    "危大": "gdaq_30306_weida",
    "危大工程巡视记录": "gdaq_30306_weida",
    "平行检查": "gd_b_213_pingxing",
    "平行检查记录": "gd_b_213_pingxing",
    "监理通知单": "gd_b_215_tongzhi",
    "安全隐患整改通知": "gdaq_30308_yinhuan",
    "材料台账": "gd_b_219_cailiao",
    "监理日志": "gd_b_220_rizhi",
    "监理月报": "gd_b_221_yuebao",
}

# canonical → 取值候选键（按优先级）。值来源有三层：详情 dict、
# content_json、prov_fields；这里只写「别名」，字段清单仍只有 FORMS 一份。
_ALIASES: dict[str, tuple[str, ...]] = {
    "project_name": ("project_name", "station_project_name", "business_project_name", "system_project_name"),
    "doc_number": ("doc_number", "form_number", "record_no"),
    "date": ("record_date", "station_start_time", "inspection_start_time", "created_at"),
    "time": ("record_date", "inspection_start_time", "created_at"),
    "weekday": ("weekday",),
    "weather": ("weather",),
    "construction_unit": ("construction_unit",),
    "owner_unit": ("owner_unit",),
    "supervision_unit": ("supervision_unit", "supervision_org"),
    "section": ("section", "location", "station_location"),
    "station_process": ("station_process", "process"),
    "start_time": ("station_start_time", "inspection_start_time", "inspection_time_text"),
    "end_time": ("station_end_time", "inspection_end_time"),
    "worker_count": ("worker_count",),
    "equipment": ("equipment",),
    "material_info": ("material_info",),
    "qa_onsite": ("qa_onsite",),
    "self_check": ("self_check",),
    "content": ("station_content", "work_content", "content", "inspection_items"),
    "problems": ("problems", "problem_text", "inspection_remark"),
    "treatment": ("treatment", "handling"),
    "acceptance_type": ("acceptance_type", "acceptance_guide_ref"),
    "acceptance_content": ("acceptance_content", "acceptance_points_text"),
    "self_check_conclusion": ("self_check_conclusion",),
    "supervision_conclusion": ("supervision_conclusion", "acceptance_result", "witness_accept_result"),
    "photo": ("inspection_photos", "witness_photos"),
    "acceptance_participants": ("acceptance_participants",),
    "acceptance_result": ("acceptance_result", "witness_accept_result"),
    "witness_material_name": ("witness_material_name", "material_name"),
    "witness_material_spec": ("witness_material_spec", "material_spec"),
    "witness_quantity": ("witness_quantity", "quantity"),
    "witness_unit": ("witness_unit", "unit"),
    "witness_cert_no": ("witness_cert_no", "cert_no"),
    "inspector": ("inspector", "supervisor", "sampler"),
    "manufacturer": ("manufacturer",),
    "batch_no": ("batch_no",),
    "sampler": ("sampler", "inspector", "supervisor"),
    "test_result": ("test_result",),
    "remark": ("remark", "inspection_remark"),
    "station_conclusion": ("station_conclusion",),
    "acceptance_basis": ("acceptance_basis", "basis"),
    "violation": ("violation",),
    "requirement": ("requirement",),
    "deadline": ("deadline",),
    "attachment": ("attachment",),
    "subject": ("subject",),
    "cc_unit": ("cc_unit",),
    "to_unit": ("to_unit",),
    "hazard_name": ("hazard_name", "hazard_category"),
    "hazard_category": ("hazard_category", "hazard_name"),
    "monitoring": ("monitoring",),
    "rectify": ("rectify",),
    "pm_onsite": ("pm_onsite",),
    "safety_onsite": ("safety_onsite",),
    "special_cert": ("special_cert",),
    "basis": ("acceptance_basis", "basis"),
    "method": ("method",),
    "instrument": ("instrument",),
    "measure_data": ("measure_data",),
    "record": ("record",),
    "conclusion": ("conclusion", "acceptance_result"),
}

# 原表字段名过长（整段巡视内容六项）时，列表里显示短标签，全文放 note，
# 一个字都不丢。键 = 字段名前缀。
_LONG_LABEL_SHORT: dict[str, str] = {
    "巡视内容：": "巡视内容",
}

# 与 doc_quality 的 PLACEHOLDERS 同口径：占位词算缺项，刻意不含「无」，
# 避免把用户明确说的「无问题」当成没填。
_PLACEHOLDERS = frozenset({
    "待补", "待定", "待填写", "待确认", "未知", "不详", "暂无", "未填",
    "n/a", "na", "null", "none", "/", "-", "--", "?", "？",
})

# 生成物里没覆盖到的记录类型，用这些「事实字段」兜底展示，绝不把
# 记录类型/状态本身当缺项去追问（A 层「待定」草稿刻意允许后改类型）。
_FALLBACK_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("施工内容（水印识别）", ("work_content",)),
    ("工程部位", ("section", "location")),
    ("施工单位", ("construction_unit",)),
    ("天气", ("weather",)),
)

# 「其他记录信息」分组：详情里有值但不在此类型字段清单里的遗留字段。
# 只在字段清单渲染启用时追加，保证换渲染方式不丢数据。
_EXTRA_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("验收结果", ("acceptance_result",)),
    ("施工内容（水印识别）", ("work_content",)),
    ("旁站结论", ("station_conclusion",)),
    ("检查备注", ("inspection_remark",)),
    ("备注", ("remark",)),
    ("验收依据", ("acceptance_basis",)),
    ("作业人数", ("worker_count",)),
)


def _text(value: Any) -> str:
    """把任意值压成可展示字符串；列表/字典只取可读摘要，绝不塞 JSON 给用户。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [_text(v) for v in value]
        return "；".join(p for p in parts if p and p not in _PLACEHOLDERS)
    if isinstance(value, Mapping):
        for key in ("text", "value", "name", "label"):
            if value.get(key):
                return _text(value[key])
        return ""
    return str(value)


def _is_placeholder(text: str) -> bool:
    low = text.strip().lower()
    return not low or low in _PLACEHOLDERS or low in {"未填写", "未填写（可在编辑记录中补充）"}


def _lookup(
    keys: tuple[str, ...],
    detail: Mapping[str, Any],
    cj: Mapping[str, Any],
    prov: Mapping[str, Any],
) -> str:
    for key in keys:
        for source in (detail, cj, prov):
            if not isinstance(source, Mapping) or key not in source:
                continue
            text = _text(source[key])
            if not _is_placeholder(text):
                return text
    return ""


def _value_keys(field: Mapping[str, Any]) -> tuple[str, ...]:
    """取值键优先级：canonical 别名 > 工具参数名(arg) > 展示名。

    arg 是建档工具的参数名，未必等于详情字段（如旁站「检查内容」的
    arg=work_content，但详情真值是 station_content），所以别名优先。
    """
    canonical = str(field.get("canonical") or "")
    keys: list[str] = list(_ALIASES.get(canonical, ()))
    for candidate in (
        field.get("arg"), canonical, field.get("key"),
        field.get("raw_label"), field.get("label"),
    ):
        if candidate and str(candidate) not in keys:
            keys.append(str(candidate))
    return tuple(keys)


def _shorten_label(label: str) -> tuple[str, str]:
    """返回 (列表标签, 全文说明)。超长字段名短化展示，全文保留在 note。"""
    clean = " ".join(str(label or "").split())
    if not clean:
        return "字段", ""
    for prefix, short in _LONG_LABEL_SHORT.items():
        if clean.startswith(prefix):
            return short, clean
    if len(clean) <= 34:
        return clean, ""
    return clean[:34] + "…", clean


def build_field_sections(
    record_type: str,
    detail: Mapping[str, Any] | None = None,
    content_json: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按 xlsx 字段清单生成详情页分组视图。

    返回 ``[{"title": 分组名, "fields": [{label, value, note, required, auto,
    kind, missing, display_value}]}]``；无对应表式时返回兜底分组（可能为空列表）。
    """
    detail = detail if isinstance(detail, Mapping) else {}
    cj = content_json if isinstance(content_json, Mapping) else {}
    if not cj and isinstance(detail.get("content_json"), Mapping):
        cj = detail["content_json"]
    prov = cj.get("prov_fields") if isinstance(cj.get("prov_fields"), Mapping) else {}
    if not prov and isinstance(detail.get("prov_fields"), Mapping):
        prov = detail["prov_fields"]

    rtype = str(record_type or "").strip()
    form_code = (
        record_field_spec.RECORD_TYPE_TO_FORM.get(rtype)
        or _EXTRA_TYPE_FORMS.get(rtype)
    )
    form = record_field_spec.FORMS.get(form_code) if form_code else None

    sections: list[dict[str, Any]] = []
    used_labels: set[str] = set()

    if form:
        by_title: dict[str, list[dict[str, Any]]] = {}
        for field in form.get("fields", []):
            label = str(field.get("raw_label") or field.get("label") or "").strip()
            if not label:
                continue
            short, note = _shorten_label(label)
            used_labels.add(short)
            kind = str(field.get("kind") or "ask")
            auto = bool(field.get("auto"))
            required = bool(field.get("required"))
            value = ""
            if kind == "signoff":
                # 签章由签章服务另行处理，详情只提示状态，不算缺项。
                value = "待签章"
            else:
                keys = _value_keys(field)
                canonical = str(field.get("canonical") or "")
                if canonical == "content" and auto:
                    # 巡视内容等「由系统生成」的固定检查项：不把水印识别的
                    # 施工内容（work_content）冒充成检查结论，工序名留给
                    # 「其他记录信息」，六项全文保留在 note。
                    keys = tuple(k for k in keys if k != "work_content")
                value = _lookup(keys, detail, cj, prov)
                if canonical == "content" and auto and _is_placeholder(value) and note:
                    # 「巡视内容」这类由系统生成的固定检查项：字段清单的
                    # 字段名本身就是六项全文，把它作为值展示给用户核对，
                    # 别显示成空的「-」，也不冒充成现场填写的内容。
                    value = note.split("：", 1)[-1].strip() if "：" in note else note
                    note = ""
                if canonical == "photo":
                    # 举牌验收照片：列表是图片 URL，展示张数即可，缩略图在
                    # 下方「影像资料」区块，不要把一串 URL 塞进字段值。
                    photos = detail.get("inspection_photos") or detail.get("witness_photos") or []
                    count = len(photos) if isinstance(photos, (list, tuple)) else 0
                    value = f"已附 {count} 张（见影像资料）" if count else ""
                elif canonical == "doc_number" and not value and detail.get("id"):
                    value = f"#{detail['id']}"  # 编号＝系统生成，用记录号带出
                elif canonical == "date" and len(value) > 10:
                    value = value[:10]
                elif canonical in ("start_time", "end_time") and "T" in value:
                    value = value.replace("T", " ")[:16]
            if "验收部位、工序" in label:
                # 用户字段清单里验收记录没有「工作内容」这一栏：水印识别出的
                # 工序名（原截图被塞进「工作内容」）应搬到本栏，和部位合并，
                # 不再另起一个清单外的栏目。
                process = _lookup(("work_content",), detail, cj, prov)
                if process and process not in value:
                    value = " · ".join(p for p in (value, process) if p)
            if (str(field.get("canonical") or "") == "acceptance_content"
                    and not _text(detail.get("acceptance_content"))
                    and _text(detail.get("acceptance_points_text"))):
                # 分组建档带出的要点草案还没确认，标清状态，别冒充正式验收内容。
                note = "；".join(p for p in (note, "要点待确认，确认后写入正式记录") if p)
            missing = bool(required and not auto and kind != "signoff" and _is_placeholder(value))
            by_title.setdefault(str(field.get("section") or "基本信息"), []).append({
                "label": short,
                "value": value,
                "note": note,
                "required": required,
                "auto": auto,
                "kind": kind,
                "missing": missing,
                # 由系统生成的字段空着时不标「未填写」红字（用户补不了），
                # 只有需要人工录入的必填项才提示「未填写」。
                "display_value": value if not _is_placeholder(value) else ("未填写" if (required and not auto) else "-"),
            })
        sections = [
            {"title": title, "fields": fields}
            for title, fields in by_title.items() if fields
        ]
    else:
        fields = []
        for label, keys in _FALLBACK_FIELDS:
            value = _lookup(keys, detail, cj, prov)
            if value:
                fields.append({
                    "label": label, "value": value, "note": "",
                    "required": False, "auto": False, "kind": "ask",
                    "missing": False, "display_value": value,
                })
        if fields:
            sections = [{"title": "记录信息", "fields": fields}]

    # 换渲染方式不丢数据：详情里有值、但不在此类型字段清单里的遗留字段。
    used_values = [
        item["value"] for sec in sections for item in sec["fields"] if item["value"]
    ]
    extras = []
    for label, keys in _EXTRA_FIELDS:
        if label in used_labels:
            continue
        value = _lookup(keys, detail, cj, prov)
        # 值已被清单里的某个字段吸收（如工序名并入「验收部位、工序」）就不重复
        if not value or value == "待签章" or any(value in used for used in used_values):
            continue
        used_values.append(value)
        extras.append({
            "label": label, "value": value, "note": "",
            "required": False, "auto": False, "kind": "ask",
            "missing": False, "display_value": value,
        })
    if extras:
        sections.append({"title": "其他记录信息", "fields": extras})
    return sections
