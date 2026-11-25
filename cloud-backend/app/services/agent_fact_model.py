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
# extra_fields 槽位键允许中文（模型输出「巡视部位」类开放中文标签时兜底
# 转槽位键，值不丢；正文键仍只认英文字符，防任意中文键漏进 facts 顶层）
_EXTRA_FIELD_RE = re.compile(r"^extra_fields\.\S{1,95}$")
_EMPTY_VALUES = {"", "null", "none", "unknown", "未知", "未识别", "待定", "n/a"}
_FACT_ALIASES = {
    "date": "record_date",
    "business_date": "record_date",
    "inspection_start_time": "start_time",
    "station_start_time": "start_time",
    "inspection_end_time": "end_time",
    "station_end_time": "end_time",
    "content": "work_content",
    "work_description": "work_content",
    "project_name": "project",
    "engineering_name": "project",
    "contractor": "construction_unit",
    "contractor_unit": "construction_unit",
    "owner": "owner_unit",
    "owner_unit_name": "owner_unit",
    "supervision_unit_name": "supervision_unit",
    "person": "inspector",
    "checker": "inspector",
    "inspection_person": "inspector",
    "work_area": "section",
    "part": "section",
    # 🔴 2026-11-25（qwen3.7-plus 路由实测）：模型会把 facts/args 的字段名
    # 写成**中文标签**（「施工内容」「开始时间」「部位」「结论」……）而非
    # 提示词要求的英文参数名——中文名不在白名单，normalize_model_facts
    # 全部丢弃，facts 恒空（生产统计：qwen3.7-plus 15 轮 0 次模型抽取，
    # deepseek-flash 75%、qwen3.8-flash 82%）。这里补中文标签 → canonical
    # 映射（_FACT_FIELD_LABELS 的反向表 + 模型实测出现的变体），
    # 与 deepseek/flash 时代的英文输出兼容并存。
    "日期": "record_date",
    "时间": "start_time",
    "开始时间": "start_time",
    "结束时间": "end_time",
    "项目": "project",
    "工程名称": "project",
    "地点": "location",
    "位置": "location",
    "部位": "section",
    "工程部位": "section",
    "施工部位": "section",
    "天气": "weather",
    "施工内容": "station_process",
    "工作内容": "work_content",
    "验收内容": "work_content",
    "巡视内容": "work_content",
    "旁站内容": "work_content",
    "人数": "worker_count",
    "施工人数": "worker_count",
    "结论": "acceptance_result",
    "验收结论": "acceptance_result",
    "旁站结论": "station_conclusion",
    "检查人员": "inspector",
    "监理人员": "inspector",
    "施工单位": "construction_unit",
    "建设单位": "owner_unit",
    "监理单位": "supervision_unit",
    "问题": "problems",
    "问题描述": "problems",
    "处理情况": "treatment",
}
# Facts are intentionally narrower than arbitrary tool arguments. They cover
# cross-cutting business fields used for merging and conflict handling; all
# table-specific values still travel through args/extra_fields.
_ALLOWED_FACT_FIELDS = {
    "record_type", "record_date", "project", "location", "section",
    "start_time", "end_time",
    "work_content", "problems", "treatment", "worker_count", "weather",
    "owner_unit", "construction_unit", "supervision_unit", "supervision_org",
    "inspector",
    "station_part", "station_process", "station_content", "station_conclusion",
    "acceptance_result", "witness_material_name", "hazard_type", "description",
}

# Evidence producers are allowed to keep legacy keys, but comparisons and
# form filling use one canonical name. This prevents a watermarked
# ``施工单位`` from becoming ``contractor`` in one layer and disappearing
# behind ``construction_unit`` in another.
_EVIDENCE_FIELD_KEYS = {
    "record_date": ("record_date", "observed_date", "taken_at", "date"),
    "project": ("project", "project_name", "engineering_name"),
    "location": ("location", "address"),
    "section": ("section", "work_area"),
    "weather": ("weather",),
    "owner_unit": ("owner_unit", "owner_unit_name"),
    "construction_unit": ("construction_unit", "contractor", "contractor_unit"),
    "supervision_unit": ("supervision_unit", "supervision_unit_name"),
    "inspector": ("inspector", "person", "checker", "inspection_person"),
}
_FACT_FIELD_LABELS = {
    "record_date": "日期",
    "start_time": "开始时间",
    "end_time": "结束时间",
    "project": "项目",
    "location": "地点",
    "section": "工程部位",
    "weather": "天气",
    "owner_unit": "建设单位",
    "construction_unit": "施工单位",
    "supervision_unit": "监理单位",
    "inspector": "检查人员",
}

# 🔴 2026-11-25（qwen3.7-plus 二轮）：模型输出中文字段名是**开放集合**
# （实测「巡视部位」「施工人数」不在静态别名表）。字段表 label 是权威对照
# （「旁站的关键工序」↔ station_process）——这里把全部表单的 label→canonical
# 收敛成查找表：中文键先查静态别名，不中再查本表（含去括号包含匹配），
# 仍未命中转 extra_fields.<原中文名>（槽位填充路径），不再整体丢弃。
_LABEL_ALIASES: dict[str, str] = {}
_LABEL_SUFFIX_STRIP = ("部位", "时间", "内容", "人数", "结论", "情况", "单位", "人员")


def _build_label_aliases() -> None:
    from app.services import record_field_spec

    for form in record_field_spec.FORMS.values():
        for field in form.get("fields") or []:
            label = _text(field.get("label"))
            canon = _text(field.get("canonical") or field.get("key"))
            if not label or not canon:
                continue
            existing = _LABEL_ALIASES.get(label)
            # 同名 label 不同 canonical（跨表冲突）：以先注册为准，避免抖动
            if existing and existing != canon:
                continue
            _LABEL_ALIASES[label] = canon
    # 白名单字段的中文标签（_FACT_FIELD_LABELS 反向）优先级更高
    for canon, label in _FACT_FIELD_LABELS.items():
        _LABEL_ALIASES[label] = canon


def _match_zh_label_field(raw_field: str) -> str:
    """中文标签 → canonical；命中返回归一名，未命中返回 extra_fields 形式。"""
    field = _text(raw_field)
    if not field:
        return ""
    if not _LABEL_ALIASES:
        _build_label_aliases()
    # 1) 全等匹配
    hit = _LABEL_ALIASES.get(field)
    if hit:
        return hit
    # 2) 去常见后缀再匹配（「巡视部位」→「巡视」、「施工人数」→「施工」）
    for suffix in _LABEL_SUFFIX_STRIP:
        if field.endswith(suffix) and len(field) > len(suffix):
            stem = field[: -len(suffix)]
            for cand in (stem, stem + "部位", stem + "内容", stem + "时间"):
                hit = _LABEL_ALIASES.get(cand)
                if hit:
                    return hit
    # 3) 表 label 包含匹配（「巡视部位」命中「巡视的部位及情况」等长标签）
    for label, canon in _LABEL_ALIASES.items():
        if len(field) >= 2 and (field in label or label in field):
            return canon
    # 4) 兜底：转槽位键（值不丢，进 extra_fields 由表单层消化）
    return f"extra_fields.{field}"


def _zh_key_needs_label_match(field: str) -> bool:
    """是否是中文键且静态别名没覆盖（需要标签表匹配）。"""
    if _FACT_ALIASES.get(field):
        return False
    return bool(field) and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\-]*", field)


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
        if _zh_key_needs_label_match(field):
            # 中文键（静态别名未覆盖）：字段表 label 匹配 → canonical，
            # 未命中转 extra_fields 槽位键——值不再因键名是中文而丢弃。
            field = _match_zh_label_field(field)
        if not (
            field in _ALLOWED_FACT_FIELDS
            or field.startswith("extra_fields.")
        ):
            continue
        if not _FIELD_RE.fullmatch(field) and not _EXTRA_FIELD_RE.fullmatch(field):
            continue
        if field in seen or not _usable_value(value):
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


def system_fact_map(facts: Iterable[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Return configured project/profile values used as non-destructive baselines."""
    out: dict[str, dict[str, Any]] = {}
    for item in facts or []:
        if not isinstance(item, dict):
            continue
        field = _FACT_ALIASES.get(_text(item.get("field")), _text(item.get("field")))
        if not field or not _usable_value(item.get("value")):
            continue
        if item.get("source") not in {"system_project", "user_profile"}:
            continue
        out[field] = item
    return out


def build_system_facts(
    *,
    project_name: str = "",
    construction_unit: str = "",
    supervision_unit: str = "",
    supervision_org: str = "",
    inspector: str = "",
) -> list[dict[str, Any]]:
    """Build the low-priority project/profile context sent into planning."""
    now = datetime.now().isoformat(timespec="seconds")
    values = (
        ("project", project_name, "system_project"),
        ("construction_unit", construction_unit, "system_project"),
        ("supervision_unit", supervision_unit, "system_project"),
        ("supervision_org", supervision_org, "system_project"),
        ("inspector", inspector, "user_profile"),
    )
    return [
        {
            "field": field,
            "value": value,
            "source": source,
            "source_ref": "当前项目/登录用户资料",
            "confidence": 0.8 if source == "system_project" else 0.6,
            "status": "system_configured" if source == "system_project" else "profile_candidate",
            "sources": [{
                "source": source,
                "source_ref": "当前项目/登录用户资料",
                "updated_at": now,
            }],
            "updated_at": now,
        }
        for field, value, source in values
        if _usable_value(value)
    ]


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
    watermark_keys = _EVIDENCE_FIELD_KEYS.get(field, ())
    if isinstance(watermark_fields, dict) and any(_text(watermark_fields.get(key)) for key in watermark_keys):
        return "watermark"
    return _text(item.get("source")) or "evidence"


def _objective_source(source: str) -> bool:
    return source in OBJECTIVE_EVIDENCE_SOURCES


def detect_source_conflicts(
    facts: Iterable[dict[str, Any]] | None,
    evidence: Iterable[dict[str, Any]] | None,
    system_facts: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Report disagreements across user, project/profile and evidence sources.

    Watermark/EXIF remains the preferred objective candidate, but it never
    becomes an unresolved silent overwrite. Every disagreement is returned as
    a confirmation item with all candidate values preserved.
    """
    user_facts = user_fact_map(facts)
    configured_facts = system_fact_map(system_facts)
    evidence_values: dict[str, list[str]] = {}
    evidence_sources: dict[str, list[str]] = {}
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        for field, keys in _EVIDENCE_FIELD_KEYS.items():
            watermark_fields = item.get("watermark_fields")
            value = next((_text(item.get(key)) for key in keys if _text(item.get(key))), "")
            if not value and isinstance(watermark_fields, dict):
                value = next(
                    (_text(watermark_fields.get(key)) for key in keys
                     if _text(watermark_fields.get(key))),
                    "",
                )
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
    fields = list(dict.fromkeys([
        *evidence_values.keys(), *user_facts.keys(), *configured_facts.keys(),
    ]))
    for field in fields:
        observed = evidence_values.get(field, [])
        fact = user_facts.get(field)
        configured = configured_facts.get(field)
        user_value = fact.get("value") if fact else ""
        system_value = configured.get("value") if configured else ""
        normalized_user = _comparable(user_value)
        normalized_system = _comparable(system_value)
        normalized_observed = {_comparable(value) for value in observed}
        values = [value for value in (system_value, user_value, *observed) if _usable_value(value)]
        distinct = {_comparable(value) for value in values}
        if len(distinct) < 2:
            continue
        sources = evidence_sources.get(field) or []
        objective_index = next(
            (index for index, source in enumerate(sources) if _objective_source(source)),
            None,
        )
        # 🔴 2026-11-04 用户明令：项目名称以当前项目（system）为主——
        # 只有证据/用户描述识别到「不同」项目名才需要确认（needs_confirmation）；
        # 系统未配置项目名时，水印识别到的才作为确认候选。
        if field == "project" and _usable_value(system_value):
            preferred_value = system_value
            preferred_source = "system_project"
            resolution = "system_value_preferred_pending_confirmation"
        elif objective_index is not None:
            preferred_value = observed[objective_index]
            preferred_source = sources[objective_index] or "watermark_or_exif"
            resolution = "evidence_value_preferred_pending_confirmation"
        elif fact:
            preferred_value = user_value
            preferred_source = _text(fact.get("source")) or "user_message"
            resolution = (
                "user_value_preferred_pending_evidence_confirmation"
                if observed else "user_value_preferred_pending_confirmation"
            )
        else:
            preferred_value = system_value
            preferred_source = _text(configured.get("source")) if configured else "system"
            resolution = "system_value_preferred_pending_confirmation"
        conflicts.append({
            "field": field,
            "label": _FACT_FIELD_LABELS.get(field, field),
            "user_value": user_value,
            "system_value": system_value,
            "system_source": _text(configured.get("source")) if configured else "",
            "evidence_values": observed,
            "evidence_sources": sources,
            "preferred_value": preferred_value,
            "preferred_source": preferred_source,
            "options": list(dict.fromkeys(str(value) for value in values)),
            "needs_confirmation": True,
            "status": "conflict",
            "resolution": resolution,
            "source_ref": fact.get("source_ref") if fact else "",
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
