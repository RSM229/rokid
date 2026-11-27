"""草稿补全的最小 agent 循环（2026-11-27）。

此前「这条消息填哪些字段」是一次性抽参 + 服务端正则兜底，模型没有决定权。
这里改成 model + tools + loop：

    模型看草稿 → 选一个工具（set_record_type / fill_fields / ask_user / done）
    → 服务端只做校验与落值 → 把结果回给模型 → 继续，直到 done 或 ask_user

服务端不再猜「哪个词是部位、几点开始」，只负责：
* 只接受当前表式里真实存在的字段（防止编造）；
* 时间/日期做通用归一化；
* 记录类型必须在 RECORD_TYPES 里；
* 最多 4 步，防止死循环。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import model_gateway, record_field_spec, time_normalization
from app.services.agent_request_plan import RECORD_TYPES

MAX_STEPS = 4

_SYSTEM = (
    "你是监理工作台的记录助手。你的任务：读懂用户这句话，把能确定的信息填进当前草稿，"
    "缺的字段再问用户。你只能通过工具改变草稿，不要输出解释性长文。\n"
    "每一步只输出一个 JSON 对象："
    '{"thought":"一句话说明你为什么这么做","tool":"工具名","args":{...}}\n'
    "工具：\n"
    "- set_record_type(args: {record_type})：用户明确说了记录类型时用\n"
    "- fill_fields(args: {fields: {字段名: 值}})：把用户明确提供的信息写进草稿\n"
    "- ask_user(args: {question})：信息不够且必须问用户时用，问最关键的 1-2 项\n"
    "- done(args: {})：该填的都填了，交给后续流程\n"
    "规则：只写用户明确说过的值，不要推断；字段名用字段表里的名字或中文标签都可以；"
    "不确定就 ask_user，不要瞎填。"
)


@dataclass
class AgentTurn:
    fields: dict[str, Any] = field(default_factory=dict)
    record_type: str = ""
    question: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return bool(self.question)


def _ask_field_map(record_type: str, args: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """当前记录类型可填的字段（canonical → 字段定义），数据来自字段表。"""
    if not record_type:
        return {}
    try:
        form = record_field_spec.form_for_record_type(record_type)
    except Exception:  # noqa: BLE001
        return {}
    if not form:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for spec in record_field_spec.fields_of(form):
        canonical = str(spec.get("canonical") or "")
        if canonical and spec.get("kind") == "ask":
            out[canonical] = spec
    return out


def _field_lookup(record_type: str) -> dict[str, dict[str, Any]]:
    """字段的多名索引：canonical / 落库参数 / 标签 / 表字段 key 都能认。

    模型不一定会用内部参数名（实测它写「巡视的工程部位」），这里按字段表
    解析，不要求在提示词里背名字。
    """
    if not record_type:
        return {}
    try:
        form = record_field_spec.form_for_record_type(record_type)
    except Exception:  # noqa: BLE001
        return {}
    if not form:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for spec in record_field_spec.fields_of(form):
        if spec.get("kind") != "ask":
            continue
        for key in (
            spec.get("canonical"), spec.get("arg"), spec.get("label"),
            spec.get("raw_label"), spec.get("key"),
        ):
            key = str(key or "").strip()
            if key:
                lookup.setdefault(key, spec)
    return lookup


def _normalize_value(field_spec: dict[str, Any], value: Any, record_date: str = "") -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    canonical = str(field_spec.get("canonical") or "")
    if canonical in {"start_time", "end_time"}:
        parsed = time_normalization.extract_explicit_time_range(text)
        if parsed:
            return parsed.get(canonical) or text
        return time_normalization.normalize_time_value(text) or text
    return text[:200]


def _state_prompt(
    *,
    record_type: str,
    args: dict[str, Any],
    missing: list[str],
    fields: dict[str, dict[str, Any]],
    context: str = "",
) -> str:
    field_lines = [
        f"- {canonical}（{spec.get('label')}）" for canonical, spec in fields.items()
    ] if fields else ["（还没有确定记录类型，先用 set_record_type）"]
    return (
        f"可选记录类型：{'、'.join(RECORD_TYPES)}\n"
        f"当前记录类型：{record_type or '待定'}\n"
        f"已填字段：{json.dumps(args, ensure_ascii=False)[:1200]}\n"
        f"仍缺字段：{json.dumps(missing[:12], ensure_ascii=False)}\n"
        f"可填字段表：\n" + "\n".join(field_lines)
        + (f"\n\n本轮已查阅到的材料（可直接使用，不要重复去查）：\n{context[:4000]}" if context else "")
    )


async def run_draft_agent(
    db: AsyncSession,
    *,
    message: str,
    draft: dict[str, Any],
    provider: str | None = None,
    model: str | None = None,
    context: str = "",
) -> AgentTurn:
    """让模型用工具补草稿；失败返回空 turn，调用方回退原有逻辑。"""
    args = dict(draft.get("args") or {})
    record_type = str(args.get("record_type") or "").strip()
    missing = [str(item) for item in (draft.get("missing") or [])]
    turn = AgentTurn(fields={}, record_type=record_type)

    conversation: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": _state_prompt(
                record_type=record_type,
                args=args,
                missing=missing,
                fields=_ask_field_map(record_type, args),
                context=context,
            ) + f"\n用户这句话：{message}",
        },
    ]

    for step in range(MAX_STEPS):
        try:
            raw, _cfg = await model_gateway.chat_completion(
                conversation,
                temperature=0.1,
                provider=provider,
                model=model,
                response_format={"type": "json_object"},
            )
        except model_gateway.ModelGatewayError:
            return turn
        decision = _parse(raw)
        if not decision:
            return turn
        tool = str(decision.get("tool") or "").strip()
        tool_args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        thought = str(decision.get("thought") or "").strip()
        turn.steps.append({"tool": tool, "args": tool_args, "thought": thought})

        if tool == "set_record_type":
            candidate = str(tool_args.get("record_type") or "").strip()
            if candidate in RECORD_TYPES:
                record_type = candidate
                turn.record_type = candidate
                args["record_type"] = candidate
                missing = _recompute_missing(record_type, args, missing)
                observation = f"已设为 {candidate}；仍缺 {missing[:8]}"
            else:
                observation = f"记录类型 {candidate!r} 不在目录里，请改用目录中的类型"
        elif tool == "fill_fields":
            payload = tool_args.get("fields") if isinstance(tool_args.get("fields"), dict) else {}
            lookup = _field_lookup(record_type)
            applied: dict[str, str] = {}
            rejected: list[str] = []
            for key, value in payload.items():
                spec = lookup.get(str(key))
                if spec is None:
                    rejected.append(str(key))
                    continue
                normalized = _normalize_value(spec, value)
                if not normalized:
                    continue
                # 草稿 args 按「落库参数」存（_field_value / supplement_hits 都读这个），
                # 没有独立参数的字段暂不接收，避免写错槽位。
                target = str(spec.get("arg") or "")
                if not target:
                    rejected.append(str(key))
                    continue
                applied[target] = normalized
            args.update(applied)
            turn.fields.update(applied)
            missing = _recompute_missing(record_type, args, missing)
            observation = (
                f"已写入 {list(applied)}；未接受 {rejected[:6]}；仍缺 {missing[:8]}"
            )
        elif tool == "ask_user":
            question = str(tool_args.get("question") or "").strip()
            turn.question = question[:300]
            return turn
        elif tool == "done":
            return turn
        else:
            observation = f"未知工具 {tool!r}，只能用 set_record_type/fill_fields/ask_user/done"

        conversation.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
        conversation.append({"role": "user", "content": f"工具结果：{observation}"})

    return turn


def _parse(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _recompute_missing(record_type: str, args: dict[str, Any], fallback: list[str]) -> list[str]:
    """用字段表重算仍缺项；算不出来时保留上游给的缺项。"""
    if not record_type:
        return fallback
    try:
        from app.services import agent_tools

        form = record_field_spec.form_for_record_type(record_type)
        views = agent_tools.missing_field_views(args, form)
        return [str(item.get("label") or "") for item in views if item.get("label")][:12]
    except Exception:  # noqa: BLE001
        return fallback
