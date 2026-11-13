"""监理记录「缺项提示 + 独立复核意见」。

参考同一台服务器上 jl-service（监理现场助手原型）已验证的两条设计，按监理通的
技术栈做最小落地（2026-11-13）：

* jl D13 / ``schemas/*.json``：字段规则集中在 schema，代码只做通用校验；占位词
  （待补/待定/无/-）一律算缺项，避免「模型把占位词写成值 → 不齐的文书被当成可签」。
* jl D16 → D19 → D20：复核用独立会话、只提意见不改稿、结果并进同一条回执，
  不额外插一条 assistant 消息（否则用户会以为「AI 没理我」）。

边界（必须守住）：
- 缺项只提示，不追问、不拦保存、不下发表单卡（用户 11-06/11-09 已定纯文字一问一答）。
- 缺项 ≠ 可签发：签发门槛仍由文书/签章侧保留，本模块只提供数据。
- 复核失败 / 超时 / 解析失败一律降级为空，绝不影响建档。
- 本模块不写数据库，只加工回执字典；也不改生成物 ``record_field_spec.py``。

环境变量：
- ``DOC_QUALITY_ENABLED``（默认 1）：总开关，关掉后回执完全不变。
- ``DOC_REVIEW_ENABLED``（默认 1）：只关复核，保留缺项提示。
- ``DOC_REVIEW_MODEL``（默认空）：可指向便宜模型；空则跟随主线模型配置。
- ``DOC_REVIEW_TIMEOUT``（默认 4 秒）：单次复核超时，超时即降级。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# jl schemas/*.json 的 placeholders 三件套。刻意不含「无 / 符合要求」——
# 在 problems 等结论字段里它们是有效内容（与 agent_tools._backfill_no_issue 的
# 边界一致），当成缺项会把用户明确说的「无问题」误判成没填。
PLACEHOLDERS = frozenset({
    "待补", "待定", "待填写", "待确认", "未知", "不详", "暂无", "未填",
    "n/a", "na", "null", "none", "/", "-", "--", "?", "？",
})

# 工具参数的键与省统表字段 arg 并不总是一致（content / work_content 等）。
_VALUE_ALIASES: dict[str, tuple[str, ...]] = {
    "work_content": ("content", "work_content", "description"),
    "content": ("content", "work_content", "description"),
    "station_part": ("station_part", "station_location", "section"),
    "section": ("section", "location"),
    "problems": ("problems", "problem"),
    "treatment": ("treatment", "handle"),
    "weather": ("weather",),
}

# 记录类型无法映射到省统表时（例如 A 层「待定」草稿），只提示事实字段，
# 绝不把「记录类型」当缺项去追问——A 层刻意允许先存待定、后改类型。
_GENERIC_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("section", "部位/区域", ("section", "location")),
    ("work_content", "记录内容", ("content", "work_content", "description")),
)

REVIEW_SYSTEM = (
    "你是独立的监理文书复核员，只复核别人写好的记录，不改写、不代填、不寒暄。"
    "重点挑四类问题：① 结论有没有依据（规范条号或现场证据）；② 有无自相矛盾；"
    "③ 有无把「待补/待定」之类占位词当成实际内容；④ 缺项是否影响签发。"
    "只输出 JSON：{\"items\":[{\"level\":\"must_fix\"|\"suggest\",\"text\":\"...\"}]}，"
    "最多 3 条，每条不超过 40 字；没有问题就输出 {\"items\":[]}。不要输出其它文字。"
)

_FACT_LABELS: tuple[tuple[str, str], ...] = (
    ("record_type", "记录类型"), ("section", "部位"), ("location", "位置"),
    ("content", "内容"), ("work_content", "检查内容"), ("station_process", "旁站工序"),
    ("station_conclusion", "旁站结论"), ("problems", "发现问题"),
    ("treatment", "处理情况"), ("acceptance_result", "验收结论"),
    ("weather", "天气"), ("worker_count", "在场人数"),
    ("_user_message", "用户原话"),
)


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def _is_blank(value: Any) -> tuple[bool, str]:
    """返回 (是否缺项, 原因)。空值、纯占位词、被括号包住的占位词都算缺。"""
    if value is None:
        return True, "empty"
    if isinstance(value, (list, tuple, dict, set)):
        return (not value), "empty"
    text = str(value).strip()
    if not text:
        return True, "empty"
    lowered = text.lower().rstrip("。.!！ ")
    if lowered in PLACEHOLDERS:
        return True, "placeholder"
    bare = re.sub(r"^[(\[{（【\s]+|[)\]}）】\s]+$", "", lowered)
    if bare and bare in PLACEHOLDERS:
        return True, "placeholder"
    return False, ""


def _value_of(values: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return None


def _field_keys(field: dict[str, Any]) -> tuple[str, ...]:
    arg = str(field.get("arg") or "").strip()
    canonical = str(field.get("canonical") or "").strip()
    key = str(field.get("key") or "").strip()
    candidates = [c for c in (arg, canonical, key) if c]
    for name in (arg, canonical):
        candidates.extend(_VALUE_ALIASES.get(name, ()))
    # 去重保序
    return tuple(dict.fromkeys(candidates))


def missing_fields(record_type: str, values: dict[str, Any] | None) -> list[dict[str, Any]]:
    """按 record_field_spec 的字段契约算缺项（只校验 ask + required）。

    auto 字段由系统带出、signoff 走签章服务，都不该在这里提示补填。
    """
    from app.services import record_field_spec

    values = values or {}
    form_code = record_field_spec.RECORD_TYPE_TO_FORM.get(str(record_type or "").strip())
    if not form_code:
        items: list[dict[str, Any]] = []
        for key, label, aliases in _GENERIC_FIELDS:
            blank, reason = _is_blank(_value_of(values, aliases))
            if blank:
                items.append({"key": key, "label": label, "required": False, "reason": reason})
        return items[:8]

    form = record_field_spec.FORMS.get(form_code) or {}
    items = []
    for field in form.get("fields") or []:
        if not isinstance(field, dict):
            continue
        if field.get("kind") != "ask" or not field.get("required"):
            continue
        label = str(field.get("label") or field.get("canonical") or "字段")
        value = _value_of(values, _field_keys(field))
        blank, reason = _is_blank(value)
        if blank:
            items.append({
                "key": str(field.get("arg") or field.get("canonical") or ""),
                "label": label,
                "required": True,
                "reason": reason,
            })
    return items[:8]


def missing_summary(items: list[dict[str, Any]] | None) -> str:
    """只陈述事实，不追问、不要求用户回答（A 层零必填口径）。"""
    if not items:
        return ""
    labels = "、".join(str(x.get("label") or "") for x in items[:4] if x.get("label"))
    more = f" 等 {len(items)} 项" if len(items) > 4 else ""
    return f"还差 {len(items)} 项：{labels}{more}（已先存着，可在记录里补，不影响保存）"


def _facts_text(payload: dict[str, Any], missing: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    substantive = 0
    for key, label in _FACT_LABELS:
        value = payload.get(key)
        blank, _ = _is_blank(value)
        if blank or label in seen:
            continue
        seen.add(label)
        if label not in ("记录类型", "用户原话"):
            substantive += 1
        text = re.sub(r"\s+", " ", str(value)).strip()[:80]
        lines.append(f"{label}：{text}")
        if len(lines) >= 10:
            break
    photo_count = int(payload.get("photo_count") or 0)
    if photo_count:
        lines.append(f"现场照片：{photo_count} 张")
        substantive += 1
    if missing:
        lines.append("待补字段：" + "、".join(str(x.get("label") or "") for x in missing[:6]))
        substantive += 1
    # 只有「记录类型 / 用户原话」时没有可复核的实质内容
    # （jl 踩过「复核员被告知缺项：无」，这里直接跳过，不烧模型配额）。
    return "\n".join(lines) if substantive > 0 else ""


def _parse_review(text: str) -> list[dict[str, str]]:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    items: list[dict[str, str]] = []
    for row in (data.get("items") or [])[:3]:
        if not isinstance(row, dict):
            continue
        level = str(row.get("level") or "").strip()
        if level in ("must_fix", "必须改", "required"):
            level = "must_fix"
        elif level in ("suggest", "建议改", "optional"):
            level = "suggest"
        else:
            continue
        body = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()[:60]
        if body:
            items.append({"level": level, "text": body})
    return items


async def review_document(payload: dict[str, Any], missing: list[dict[str, Any]] | None) -> dict[str, Any]:
    """独立会话复核：不带聊天历史、不带工具，只提意见，失败一律降级。"""
    if not _flag("DOC_REVIEW_ENABLED"):
        return {"items": [], "status": "disabled"}
    facts = _facts_text(payload, missing)
    if not facts:
        return {"items": [], "status": "skipped", "reason": "empty_record"}

    model = os.getenv("DOC_REVIEW_MODEL", "").strip() or None
    try:
        timeout = max(0.5, float(os.getenv("DOC_REVIEW_TIMEOUT", "4") or 4))
    except ValueError:
        timeout = 4.0
    messages = [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user", "content": f"请复核这条监理记录：\n{facts}"},
    ]
    try:
        from app.services import model_gateway

        out = await asyncio.wait_for(
            model_gateway.chat_completion(
                messages, temperature=0.2, max_tokens=400, model=model,
            ),
            timeout=timeout,
        )
        text = out[0] if isinstance(out, tuple) else out
    except asyncio.TimeoutError:
        logger.warning("doc review timed out after %.1fs", timeout)
        return {"items": [], "status": "timeout"}
    except Exception as exc:  # noqa: BLE001 - 复核是锦上添花，绝不能影响建档
        logger.warning("doc review failed: %s", type(exc).__name__)
        return {"items": [], "status": "failed"}

    items = _parse_review(text)
    if not items and str(text or "").strip().find("{") < 0:
        return {"items": [], "status": "unparsed"}
    return {"items": items, "status": "ok" if items else "clean"}


async def enrich_action_result(
    result: dict[str, Any], args: dict[str, Any] | None, message: str = "",
) -> dict[str, Any]:
    """建档成功后加工回执：加缺项列表 + 复核意见（原地改，返回同一 dict）。"""
    if not _flag("DOC_QUALITY_ENABLED") or not isinstance(result, dict):
        return result
    if not result.get("ok"):
        return result

    values = dict(args or {})
    record_type = str(values.get("record_type") or "").strip()
    if not record_type:
        title = str((result.get("card") or {}).get("title") or "")
        for known in ("巡视", "旁站", "验收", "见证", "待定"):
            if known in title:
                record_type = known
                break

    missing = missing_fields(record_type, values)
    result["missing_fields"] = missing
    if missing:
        result["reply"] = str(result.get("reply") or "").rstrip() + "\n" + missing_summary(missing)

    payload = {
        **values,
        "record_type": record_type,
        "photo_count": (result.get("data") or {}).get("photo_count") or 0,
    }
    if message:
        payload["_user_message"] = str(message)[:120]
    result["review"] = await review_document(payload, missing)
    return result
