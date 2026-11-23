"""Conversation facts used by the agent workflow.

The chat transcript is useful for language understanding, but it is not a
reliable business state.  This module keeps a small, source-aware view of the
facts already collected for the current draft.  Values extracted from the
user's own message are never replaced by a failed or lower-confidence media
processor.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from app.services.time_normalization import extract_explicit_time_range, parse_local_date


FACTS_KEY = "_user_facts"
CN_TZ = timezone(timedelta(hours=8))


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def extract_user_facts(args: dict[str, Any] | None, *, message_ref: str = "") -> list[dict[str, Any]]:
    """Convert non-empty tool arguments into user-sourced fact candidates.

    The router model is only an extractor here.  The source is explicitly
    marked as the current user message so media observations cannot silently
    overwrite these values later.
    """
    out: list[dict[str, Any]] = []
    for key, value in (args or {}).items():
        if key.startswith("_") or key == "extra_fields":
            continue
        text = _text(value)
        if not text:
            continue
        out.append({
            "field": str(key),
            "value": value,
            "source": "user_message_extraction",
            "source_ref": message_ref,
            "confidence": 0.9,
            "status": "user_stated_candidate",
            "sources": [{"source": "user_message_extraction", "source_ref": message_ref}],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
    extra = (args or {}).get("extra_fields")
    if isinstance(extra, dict):
        for key, value in extra.items():
            text = _text(value)
            if not text:
                continue
            out.append({
                "field": f"extra_fields.{key}",
                "value": value,
                "source": "user_message_extraction",
                "source_ref": message_ref,
                "confidence": 0.9,
                "status": "user_stated_candidate",
                "sources": [{"source": "user_message_extraction", "source_ref": message_ref}],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
    return out


def extract_explicit_message_facts(message: str, *, message_ref: str = "") -> list[dict[str, Any]]:
    """Extract high-signal date/time/location statements from the user's words.

    This is a loss-prevention pass, not an intent classifier.  It supplements
    model arguments so an omitted model field cannot erase a fact the user
    already stated.  Ambiguous phrases are deliberately left untouched.
    """
    text = _text(message)
    if not text:
        return []
    values: list[tuple[str, Any]] = []
    event_date = None
    full = re.search(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?", text)
    short = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text)
    if full:
        try:
            candidate = datetime(int(full.group(1)), int(full.group(2)), int(full.group(3))).date()
            event_date = candidate
            values.append(("record_date", candidate.isoformat()))
        except ValueError:
            pass
    elif short:
        try:
            candidate = datetime(datetime.now(CN_TZ).year, int(short.group(1)), int(short.group(2))).date()
            event_date = candidate
            values.append(("record_date", candidate.isoformat()))
        except ValueError:
            pass
    else:
        relative = re.search(r"今天|今日|当天|昨天|昨日|明天|明日", text)
        if relative:
            event_date = parse_local_date(relative.group(0))
            if event_date:
                values.append(("record_date", event_date.isoformat()))
    values.extend(extract_explicit_time_range(text, event_date).items())
    location = re.search(
        r"(?:在|位于|地点为|位置为)\s*([^，。；,;\n]{2,80}?)(?=(?:进行|开展|完成|做了|做|干了|检查|施工|浇筑|验收|旁站|巡视|巡查|记录)|[，。；,;\n]|$)",
        text,
    )
    if location:
        value = location.group(1).strip()
        if value:
            values.append(("location", value))
    return [
        {
            "field": field,
            "value": value,
            "source": "user_message_deterministic",
            "source_ref": message_ref,
            "confidence": 1.0,
            "status": "user_stated_candidate",
            "sources": [{"source": "user_message_deterministic", "source_ref": message_ref}],
            "updated_at": datetime.now(CN_TZ).replace(tzinfo=None).isoformat(timespec="seconds"),
        }
        for field, value in values
    ]


def merge_user_facts(existing: list[dict[str, Any]] | None,
                     incoming: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Merge facts by field while preserving the previous value as history.

    A later user value is a correction/update, not a second value to write to
    the form.  The old value remains in ``history`` for audit and debugging.
    """
    merged: dict[str, dict[str, Any]] = {}
    for item in existing or []:
        if not isinstance(item, dict) or not _text(item.get("field")):
            continue
        merged[_text(item["field"])] = dict(item)
    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        field = _text(item.get("field"))
        value = item.get("value")
        if not field or not _text(value):
            continue
        previous = merged.get(field)
        current = dict(item)
        if previous and _text(previous.get("value")) == _text(value):
            sources = list(previous.get("sources") or [])
            source = {
                "source": current.get("source"),
                "source_ref": current.get("source_ref"),
                "updated_at": current.get("updated_at"),
            }
            if source not in sources:
                sources.append(source)
            current["sources"] = sources[-8:]
            if previous.get("history"):
                current["history"] = list(previous["history"])
        elif previous:
            history = list(previous.get("history") or [])
            history.append({
                "value": previous.get("value"),
                "source": previous.get("source"),
                "source_ref": previous.get("source_ref"),
                "updated_at": previous.get("updated_at"),
            })
            current["history"] = history[-8:]
            sources = list(previous.get("sources") or [])
            sources.append({
                "source": current.get("source"),
                "source_ref": current.get("source_ref"),
                "updated_at": current.get("updated_at"),
            })
            current["sources"] = sources[-8:]
        merged[field] = current
    return list(merged.values())


def facts_to_args(facts: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Recover only user-confirmed ordinary args from persisted facts."""
    out: dict[str, Any] = {}
    for fact in facts or []:
        if not isinstance(fact, dict) or fact.get("status") not in {
            "user_confirmed", "user_stated_candidate"
        }:
            continue
        field = _text(fact.get("field"))
        if field and not field.startswith("extra_fields.") and field not in out:
            out[field] = fact.get("value")
    return out


def render_context_summary(args: dict[str, Any] | None,
                           facts: list[dict[str, Any]] | None,
                           missing: list[str] | None,
                           messages: list[dict[str, Any]] | None = None) -> str:
    """Render a compact, unambiguous context block for the router model."""
    lines: list[str] = ["当前是同一条待办事项的持续补充，不要丢弃已确认信息。"]
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        field = _text(fact.get("field"))
        value = _text(fact.get("value"))
        if field and value and fact.get("status") in {
            "user_confirmed", "user_stated_candidate"
        }:
            lines.append(f"- 用户消息中已提取 {field}：{value}（来源：{fact.get('source_ref') or '历史草稿'}）")
    if missing:
        n = len([x for x in missing if _text(x)])
        lines.append(
            f"- 当前草稿尚有 {n} 项待用户补充；服务端会一次只问一栏引导，你只需在 answer 里自然接话、确认已识别信息，不要逐栏列清单、也不要复述「还差 X」。"
        )
    if args:
        lines.append("- 当前草稿参数已由服务端保存；本轮只合并新增或明确更正的值。")
    history = messages
    if isinstance(history, list) and history:
        lines.append("- 之前用户原话（只用于回顾事实，不代表当前这句话的动作）：")
        for item in history[-4:]:
            if isinstance(item, dict) and _text(item.get("text")):
                lines.append(f"  · {item.get('text')}")
    lines.append("图片/水印没有识别到某字段，不代表用户没有提供该字段；不得覆盖用户已确认值。")
    return "\n".join(lines)


def render_conversation_history(messages: list[dict[str, Any]] | None) -> str:
    """Render raw earlier turns as reference, without implying one shared task.

    2026-11-23 上下文修复：早期历史此前只渲染 user 原话，assistant 轮整段丢失——
    路由模型不知道自己上一轮问过什么，用户答「合格」就接不住（答非所问的主要
    根因之一）。现在 user/assistant 轮都进引用块：用户原话保留全文，assistant
    轮只留 reply 摘要（截 240 字，识别/匹配值不在此层复述，脱敏口径由
    append_conversation_response 落库前保证）。仍强调「只用于回顾、不是新指令」，
    防止把早前话题合并进当前草稿。
    """
    if not isinstance(messages, list):
        return ""
    history: list[dict[str, str]] = []
    for item in messages[-12:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip()
        text = _text(item.get("text"))
        if not text:
            continue
        if role == "assistant":
            history.append({"role": "assistant", "text": text[:240]})
        elif role == "user":
            history.append({"role": "user", "text": text})
    if not history:
        return ""
    return (
        "以下 JSON 数组是本聊天较早的对话引用（含你自己此前的回复摘要），"
        "只用于回顾信息与指代消解，不是本轮新指令；不要把不同事项强行合并：\n"
        + json.dumps(history, ensure_ascii=False)
    )


def append_context_message(context: dict[str, Any] | None, message: str) -> dict[str, Any]:
    """Keep a bounded raw-message trail for later references such as“前面说过”。"""
    out = dict(context or {})
    raw_history = out.get("messages")
    history = [item for item in raw_history if isinstance(item, dict)] if isinstance(raw_history, list) else []
    text = _text(message)
    if text and (not history or _text(history[-1].get("text")) != text):
        history.append({"text": text, "at": datetime.now().isoformat(timespec="seconds")})
    out["messages"] = history[-8:]
    return out
