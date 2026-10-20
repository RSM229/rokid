"""字段表驱动的记录表「步进表单」组装。

把两套既有真源合并成小程序可直接渲染的分步结构，不产生第二份字段定义：

- ``provform_schema``：提供 key / label / type / options / placeholder / maxlen（渲染用）
- ``record_field_spec``：提供 kind(ask/auto/signoff) / required（决定只读与必填口径）

两者以 **schema key** 对齐（字段表由 provform_schema 生成，key 同源）。
字段表没覆盖的表（23 类手填类）退回 ``schema_only`` 模式：只读判定仍用
``provform_schema.get_schema()`` 纠正过的 system 标记，行为与本改造前一致。

设计要点：
- 只读字段（kind=auto/signoff）不占步内名额，只跟随所在 section 展示；
- 每个 section 内「可填项」超过 ``MAX_STEP_FIELDS`` 时按顺序切子步；
- prefill 的键可以是 canonical / 追问 arg / schema key，统一反查回 schema key。
"""
from __future__ import annotations

from typing import Any

from app.services import provform_schema, record_field_spec

# 每步最多几个「要用户填」的字段。超过就切子步，避免一屏铺 20 栏。
MAX_STEP_FIELDS = 5


def _scalar(value: Any) -> str:
    """只接受标量：列表/字典（如材料台账的 items）不进单值预填，避免渲染出 JSON。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def _alias_index(form_code: str) -> tuple[set[str], dict[str, str]]:
    """返回 (schema key 集合, 别名→schema key 映射)。"""
    keys: set[str] = set()
    alias: dict[str, str] = {}
    for f in record_field_spec.fields_of(form_code):
        key = f.get("key")
        if not key:
            continue
        keys.add(str(key))
        for name in (f.get("canonical"), f.get("arg")):
            if name:
                alias.setdefault(str(name), str(key))
    return keys, alias


def _content_map_index(form_code: str) -> dict[str, str]:
    """canonical → schema key（字段表没覆盖时兜底，来源 CONTENT_MAP）。"""
    return {str(canon): str(key)
            for key, canon in (provform_schema.CONTENT_MAP.get(form_code) or {}).items()}


def prefill_to_keys(form_code: str, prefill: dict | None) -> dict[str, str]:
    """把 canonical / arg / schema key 混写的预填字典统一成 schema-key 字典。"""
    if not prefill:
        return {}
    keys, alias = _alias_index(form_code)
    by_canon = _content_map_index(form_code)
    out: dict[str, str] = {}
    for raw_key, raw_val in prefill.items():
        name = str(raw_key or "")
        if not name:
            continue
        if name in keys:
            target = name
        elif name in alias:
            target = alias[name]
        else:
            target = by_canon.get(name)
        if not target:
            continue
        text = _scalar(raw_val)
        if text:
            out[target] = text
    return out


def _build_field(form_code: str, schema_field: dict, spec_field: dict | None,
                 values: dict[str, str]) -> dict:
    key = str(schema_field.get("key") or "")
    if spec_field:
        kind = str(spec_field.get("kind") or "ask")
        required = bool(spec_field.get("required"))
    else:
        # 无字段表：沿用 get_schema() 已纠正的 system 标记
        kind = "auto" if schema_field.get("system") else "ask"
        required = bool(schema_field.get("required"))

    item: dict[str, Any] = {
        "key": key,
        "canonical": provform_schema.canonical_key(form_code, key),
        "label": schema_field.get("label") or (spec_field or {}).get("label") or key,
        "type": schema_field.get("type") or "text",
        "kind": kind,
        "required": required,
        "readonly": kind in ("auto", "signoff"),
        "value": values.get(key, ""),
    }
    for opt in ("options", "placeholder", "maxlen"):
        if schema_field.get(opt):
            item[opt] = schema_field[opt]
    return item


def _split_section(title: str, fields: list[dict]) -> list[tuple[str, list[dict]]]:
    """按「可填项」数量切子步；只读字段跟随前一个可填项，不单独起一步。"""
    if not fields:
        return []
    fillable = sum(1 for f in fields if not f["readonly"])
    if fillable <= MAX_STEP_FIELDS:
        return [(title, fields)]

    chunks: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for field in fields:
        if not field["readonly"]:
            if used >= MAX_STEP_FIELDS:
                chunks.append(current)
                current, used = [], 0
            used += 1
        current.append(field)
    if current:
        chunks.append(current)

    if len(chunks) <= 1:
        return [(title, fields)]
    return [(f"{title}（{i + 1}/{len(chunks)}）", chunk) for i, chunk in enumerate(chunks)]


def _merge_readonly_steps(chunks: list[tuple[str, list[dict]]]) -> list[tuple[str, list[dict]]]:
    """把「整步没有可填项」（全是系统带出/签章栏）的步骤并进相邻步。

    避免用户点进一个什么都没有只能看的步骤。优先并入上一步；首步则前置到
    下一个有可填项步骤的顶部（用户仍能看到系统带出的值）。
    """
    merged: list[tuple[str, list[dict]]] = []
    pending: list[dict] = []
    for title, fields in chunks:
        fillable = sum(1 for f in fields if not f["readonly"])
        if fillable == 0:
            if merged:
                merged[-1] = (merged[-1][0], merged[-1][1] + fields)
            else:
                pending.extend(fields)
            continue
        merged.append((title, pending + fields if pending else fields))
        pending = []
    if pending:
        # 整张表都没有可填项（纯系统带出/签章表）：保留为单步，不做静默丢弃
        if merged:
            merged[0] = (merged[0][0], pending + merged[0][1])
        else:
            merged.append(("", pending))
    return merged


def build_steps(form_code: str, prefill: dict | None = None,
                prefill_source: str = "none") -> dict | None:
    """组装步进表单；该表无字段级 schema 时返回 None（调用方决定 404）。"""
    schema = provform_schema.get_schema(form_code)
    if schema is None:
        return None

    spec_fields = record_field_spec.fields_of(form_code)
    spec_by_key = {str(f["key"]): f for f in spec_fields if f.get("key")}
    values = prefill_to_keys(form_code, prefill)

    chunks: list[tuple[str, list[dict]]] = []
    missing: list[str] = []
    dropped: list[str] = []
    prefilled = 0
    for section in schema.get("sections") or []:
        built: list[dict] = []
        for schema_field in section.get("fields") or []:
            key = str(schema_field.get("key") or "")
            # 有字段表时：schema 里多出来的键 = 生成脚本 SPEC_DROP 刻意隐藏的栏
            # （模板上无此栏，如巡视「施工作业人数」）。不能让它退化成可填项。
            if spec_by_key and key not in spec_by_key:
                dropped.append(key)
                continue
            field = _build_field(form_code, schema_field, spec_by_key.get(key), values)
            built.append(field)
            if field["value"]:
                prefilled += 1
            if field["kind"] == "ask" and field["required"] and not field["value"]:
                missing.append(key)
        chunks.extend(_split_section(str(section.get("title") or ""), built))

    steps = [
        {"index": i, "title": title, "fields": fields}
        for i, (title, fields) in enumerate(_merge_readonly_steps(chunks))
    ]

    return {
        "form_code": form_code,
        "name": schema.get("name") or "",
        "form_number": schema.get("form_number") or "",
        "version": schema.get("version") or provform_schema.SCHEMA_VERSION,
        "source": "field_spec" if spec_by_key else "schema_only",
        "max_step_fields": MAX_STEP_FIELDS,
        # 签认栏不占步，随最后一步展示（与改造前的整表渲染保持一致）
        "signoffs": schema.get("signoffs") or [],
        "prefill_source": prefill_source,
        "prefill_count": prefilled,
        "missing": missing,
        "missing_count": len(missing),
        "dropped": dropped,
        "step_count": len(steps),
        "steps": steps,
    }
