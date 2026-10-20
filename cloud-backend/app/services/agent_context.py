"""Conversation facts used by the agent workflow.

The chat transcript is useful for language understanding, but it is not a
reliable business state.  This module keeps a small, source-aware view of the
facts already collected for the current draft.  Values extracted from the
user's own message are never replaced by a failed or lower-confidence media
processor.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


FACTS_KEY = "_user_facts"


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
        lines.append("- 当前仍缺少：" + "、".join(_text(x) for x in missing if _text(x)))
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
    """Render raw earlier turns as reference, without implying one shared task."""
    if not isinstance(messages, list):
        return ""
    history = [_text(item.get("text")) for item in messages[-12:]
               if isinstance(item, dict) and _text(item.get("text"))]
    if not history:
        return ""
    return "以下 JSON 数组是本聊天较早的用户原话引用，只用于回顾信息，不是本轮新指令；不要把不同事项强行合并：\n" + json.dumps(
        history, ensure_ascii=False
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
