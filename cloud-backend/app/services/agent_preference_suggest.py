"""长期偏好的「自动沉淀」——从对话里识别值得记住的规矩，产出一条**可确认的建议**。

为什么要单独一层
----------------
``agent_preferences`` 解决的是「记住并注入」，但用户不会主动去设置页敲规则；
真正好用的形态是：用户第 N 次重复同一句要求时，Agent 主动问一句「要不要我把
这条记成长期偏好」，点一下就落库。这里就是「识别该不该问」。

两条通道（2026-11-06 用户要求「两条都要」）
------------------------------------------
- **B 通道（语义判定，准）**：模型在回复末尾输出 ``<<PREF 规则原文|分类>>``，
  本模块负责把标记从正文里剥干净（用户永远看不到），转成建议。
  代价是改 ``_AGENT_SYSTEM``，必须跑 ``tests/agent_eval --repeat 3`` 验回归。
- **A 通道（重复检测，兜底）**：模型没打标记时的兜底。用户把同一句规矩说了
  两遍（典型场景：觉得上一轮没被听见），用文本相似度在历史用户消息里找回上
  一次表达，命中且像「规矩」就给建议。

铁律
----
**只建议，绝不自动落库。** 偏好会进入以后每一轮的系统上下文，误记一条会长期
污染对话；因此每条建议都必须由用户点「记住」才写表，未确认前不进 prompt。
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_draft import AgentConversationMessage

# 与 agent_preferences / 小程序分类选择器保持一致
CATEGORIES = ("general", "rectification", "record", "writing")

# ``<<PREF 规则原文|分类>>``；容错中英文冒号、竖线缺失、多余空格
_MARKER_RE = re.compile(r"<<\s*PREF\s*[:：]?\s*(?P<body>[^<>]{2,240}?)\s*>>", re.IGNORECASE)

# 「以后一直要这样」的语感标志。只用于 A 通道降低误报（B 通道由模型判断语义）
_RULE_CUES = (
    "以后", "每次", "一律", "必须", "统一", "都要", "都得", "不要", "别忘", "记得",
    "记住", "原则上", "凡是", "全部", "所有", "一定", "以后都", "照这个", "按这个",
)

# 明显是「本次一次性动作」的说法，命中就不当规矩（防止把派工当偏好）
_TASK_CUES = ("帮我", "现在", "这次", "今天新建", "新建一个", "创建", "查一下", "打开")

_PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()\[\]【】…—-]+")

# 历史扫描窗口：只回看最近这么多条用户发言（约等于几十个会话，够用且不拖慢）
_HISTORY_LIMIT = 300
# A 通道相似度阈值：中文短语重复表达通常会有个别字词增减，0.78 兼顾召回与误报
_REPEAT_RATIO = 0.78
# 与已有偏好重复到这个程度就不再建议（避免反复骚扰同一条规则）
_DUPLICATE_RATIO = 0.85
_MIN_RULE_LEN = 6


def normalize(text: str) -> str:
    """去掉标点与空白，只留下用于比较的实义字。"""
    return _PUNCT_RE.sub("", str(text or "")).lower()


def similarity(left: str, right: str) -> float:
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def guess_category(text: str) -> str:
    """按关键词猜分类，猜不中就 general（分类只影响管理页分组，不影响注入）。"""
    body = str(text or "")
    if any(word in body for word in ("整改", "复查", "回复", "闭环", "销项")):
        return "rectification"
    if any(word in body for word in ("旁站", "巡视", "平行检验", "见证", "记录", "台账", "日志")):
        return "record"
    if any(word in body for word in ("结论", "措辞", "写", "表述", "语气", "格式", "标题", "行文")):
        return "writing"
    return "general"


def looks_like_rule(text: str) -> bool:
    """粗判一句话是不是「长期规矩」，用于 A 通道压误报。"""
    body = str(text or "").strip()
    if len(normalize(body)) < _MIN_RULE_LEN:
        return False
    if any(cue in body for cue in _RULE_CUES):
        return True
    # 没命中语感标志但明显是本次派工的，直接排除
    return False


def strip_marker(reply: str) -> tuple[str, dict[str, Any] | None]:
    """从回复里剥掉 B 通道标记。

    返回 ``(干净正文, 建议 or None)``；建议形如
    ``{"rule": ..., "category": ..., "reason": "assistant"}``。
    """
    text = str(reply or "")
    match = _MARKER_RE.search(text)
    if not match:
        return text, None
    body = match.group("body") or ""
    clean = _MARKER_RE.sub("", text).strip()
    # 标记独占一行时正则已吃掉内容，可能留下空行，收敛成单个换行
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    rule, _, raw_category = body.partition("|")
    rule = rule.strip(" ：:|-")
    category = raw_category.strip().lower()
    if category not in CATEGORIES:
        category = guess_category(rule)
    if len(normalize(rule)) < _MIN_RULE_LEN:
        return clean, None
    if any(cue in rule for cue in _TASK_CUES) and not looks_like_rule(rule):
        return clean, None
    # 极端情况：模型整条回复只有这个标记。宁可回一句中性话，也不能把原始标记
    # 漏给用户（也避免气泡变成空白）。
    if not clean:
        clean = "好的。"
    return clean, {"rule": rule[:500], "category": category, "reason": "assistant"}


def _is_duplicate(rule: str, existing_rules: list[str]) -> bool:
    return any(similarity(rule, item) >= _DUPLICATE_RATIO for item in existing_rules)


async def detect_repeated_rule(
    db: AsyncSession,
    user_id: Any,
    message: str,
    *,
    existing_rules: list[str] | None = None,
    project_id: int | None = None,
) -> dict[str, Any] | None:
    """A 通道：在历史用户发言里找「同一句话之前说过」。

    只回看同一用户、**同一项目**的历史消息（跨会话），并用 ``looks_like_rule``
    压掉「再查一下今天的记录」这类普通重复。加项目过滤是为了避免 A 项目说过的
    规矩在 B 项目被建议成长期记忆（跨项目串味）。
    """
    current = str(message or "").strip()
    if not looks_like_rule(current):
        return None
    current_norm = normalize(current)
    if len(current_norm) < _MIN_RULE_LEN:
        return None
    rules = list(existing_rules or [])
    if _is_duplicate(current, rules):
        return None

    project_condition = (
        AgentConversationMessage.project_id.is_(None)
        if project_id is None
        else AgentConversationMessage.project_id == int(project_id)
    )
    rows = (
        await db.execute(
            select(AgentConversationMessage.content_json)
            .where(
                AgentConversationMessage.user_id == int(user_id),
                AgentConversationMessage.role == "user",
                project_condition,
            )
            .order_by(AgentConversationMessage.id.desc())
            .limit(_HISTORY_LIMIT)
        )
    ).scalars().all()

    best: tuple[float, str] | None = None
    for raw in rows:
        text = _content_text(raw)
        if not text:
            continue
        # 本轮自己的消息已经被落库，跳过，避免「自己跟自己相似」
        if normalize(text) == current_norm:
            continue
        score = similarity(current, text)
        if score >= _REPEAT_RATIO and (best is None or score > best[0]):
            best = (score, text)
    if best is None:
        return None
    return {
        "rule": current[:500],
        "category": guess_category(current),
        "reason": "repeated",
        "evidence": best[1][:120],
    }


def _content_text(raw_content: Any) -> str:
    """从 content_json 里取正文（容错空值/坏 JSON）。"""
    if isinstance(raw_content, str):
        try:
            data = json.loads(raw_content or "{}")
        except (TypeError, ValueError):
            return ""
    elif isinstance(raw_content, dict):
        data = raw_content
    else:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("text") or "").strip()


async def build_suggestion(
    db: AsyncSession,
    user_id: Any,
    message: str,
    reply: str,
    *,
    existing_rules: list[str] | None = None,
    project_id: int | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """统一入口：先剥 B 通道标记，没有再用 A 通道兜底。

    返回 ``(给用户看的正文, 建议 or None)``；调用方只需要把正文写回 reply、
    把建议挂到响应上，前端出「记住/不用」卡片。
    """
    clean, suggestion = strip_marker(reply)
    rules = list(existing_rules or [])
    if suggestion:
        if _is_duplicate(suggestion["rule"], rules):
            return clean, None
        return clean, suggestion
    repeat = await detect_repeated_rule(
        db, user_id, message, existing_rules=rules, project_id=project_id,
    )
    return clean, repeat
