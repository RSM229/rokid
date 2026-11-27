"""计划字段的「让模型自己改到达标」循环（2026-11-27）。

校验器不再负责猜人类写法（斜杠日期、17.57分、下午+17…），它只负责**指出**
哪个字段不符合合同。修复交给模型：带上字段类型、当前值、错误原因，让模型按
合同改写，再校验一次。

这样新增字段/新格式时不用再往解析器里堆正则；解析器只作为快速通道。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import agent_review_plan, model_gateway, record_field_spec

MAX_ROUNDS = 2

_SYSTEM = (
    "你在修正一份监理工作记录草稿里不符合格式的字段值。"
    "只改格式，不改用户表达的语义；不要新增用户没说过的事实。"
    '只输出 JSON：{"repairs":[{"event":1,"canonical":"字段名","value":"修正后的值"}]}。'
    "拿不准的字段不要输出，留给用户补充。"
)


def _field_spec(form_code: str | None, canonical: str) -> dict[str, Any]:
    if not form_code:
        return {}
    for spec in record_field_spec.fields_of(form_code):
        if str(spec.get("canonical") or "") == canonical:
            return spec
    return {}


def collect_invalid(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """收集所有「值存在但格式不合法」的字段（缺字段不算，模型不能凭空造）。"""
    problems: list[dict[str, Any]] = []
    for index, event in enumerate(plan.get("events") or []):
        resolved = _resolve(plan, event)
        form_code = resolved.get("form_code")
        values = (event.get("fields") or {}).get(form_code) or {}
        for invalid in resolved.get("invalid_fields") or []:
            canonical = str(invalid.get("canonical") or "")
            spec = _field_spec(form_code, canonical)
            problems.append({
                "event": index + 1,
                "form": form_code,
                "canonical": canonical,
                "label": invalid.get("label") or spec.get("label") or canonical,
                "message": invalid.get("message") or "格式不符合合同",
                "table_type": str(spec.get("table_type") or ""),
                "value": (values.get(canonical) or {}).get("value"),
            })
    return problems


def _resolve(plan: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    from app.services import agent_form_requirements

    try:
        return agent_form_requirements.resolve_event_form(plan, event)
    except Exception:  # noqa: BLE001
        return {}


def _prompt(problems: list[dict[str, Any]]) -> str:
    lines = [f"今天日期：{date.today().isoformat()}（服务端）"]
    for item in problems:
        value = item.get("value")
        lines.append(
            f"- 事项{item['event']} {item['label']}（{item['canonical']}，"
            f"{item['table_type'] or '文本'}）：当前值 {value!r}；问题：{item['message']}"
        )
    hints = []
    for item in problems:
        spec = _field_spec(item.get("form"), item["canonical"])
        options = record_field_spec.options_for(spec) if spec else []
        if options:
            hints.append(f"{item['canonical']} 可选值：{'/'.join(str(o) for o in options[:10])}")
    if hints:
        lines.append("字段取值：" + "；".join(hints))
    return "\n".join(lines) + "\n请输出修正值。"


def _apply(plan: dict[str, Any], repairs: list[dict[str, Any]]) -> int:
    applied = 0
    events = plan.get("events") or []
    for repair in repairs:
        if not isinstance(repair, dict):
            continue
        try:
            event_index = int(repair.get("event") or 0) - 1
        except (TypeError, ValueError):
            continue
        canonical = str(repair.get("canonical") or "").strip()
        value = str(repair.get("value") or "").strip()
        if not canonical or not value or not (0 <= event_index < len(events)):
            continue
        event = events[event_index]
        resolved = _resolve(plan, event)
        form_code = resolved.get("form_code")
        spec = _field_spec(form_code, canonical)
        if not spec:
            continue
        fields = event.setdefault("fields", {}).setdefault(form_code, {})
        current = fields.get(canonical) if isinstance(fields.get(canonical), dict) else {}
        fields[canonical] = {
            **current,
            "value": value,
            "source": "model_repair",
            "status": "user_stated_candidate",
        }
        applied += 1
    return applied


async def repair_plan(
    plan: dict[str, Any],
    *,
    db: AsyncSession | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """把格式不合法的字段交给模型改写，最多两轮；返回（计划，剩余问题）。"""
    for _ in range(MAX_ROUNDS):
        problems = collect_invalid(plan)
        if not problems:
            break
        try:
            raw, _cfg = await model_gateway.chat_completion(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": _prompt(problems)},
                ],
                temperature=0.1,
                provider=provider,
                model=model,
                response_format={"type": "json_object"},
            )
        except model_gateway.ModelGatewayError:
            break
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            break
        repairs = data.get("repairs") if isinstance(data, dict) else None
        if not isinstance(repairs, list) or not repairs:
            break
        if not _apply(plan, repairs):
            break
    return plan, agent_review_plan.review_issues(plan)
