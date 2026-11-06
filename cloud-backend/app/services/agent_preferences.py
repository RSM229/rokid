"""用户级长期偏好 —— 跨会话、跨项目注入 agent 上下文的读写服务。

与 ``agent_context``（会话内事实/草稿）和 ``AgentConversationMemory``
（会话原话回忆）的区别：

- 会话内草稿随工具结束即清；偏好**永久保存**，直到用户关闭。
- 会话原话只用于「回顾早先说过什么」；偏好是「用户明示的做事规则」，
  注入系统提示词后**持续影响模型行为**（如「整改回复必须附前后对比照片」）。

隔离口径：按 ``user_id`` 隔离，跨项目不串，与现有 agent 记忆同原则。
写入原则：除非用户在管理页明确声明（``user_declared``），助手沉淀的
建议（``assistant_suggested``）必须可被一键关闭（``active=False``），
不允许把用户没确认的习惯当成强制规则。

信任边界（对齐 ``agent_fact_model`` 的 ``system_project`` 思想）：
偏好注入口径只读 ``active=True`` 的行，且只作为**非破坏性基线**——
具体落库前服务端仍会做参数校验、权限校验与审计，偏好不能越权。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_draft import AgentUserPreference

logger = logging.getLogger(__name__)

#: 单用户活跃偏好上限，防止提示词无限膨胀（每条约 30~60 token）
MAX_ACTIVE_PREFERENCES = 20


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


async def list_active_preferences(
    db: AsyncSession,
    user_id: int,
    *,
    category: str | None = None,
) -> list[AgentUserPreference]:
    """Read the user's active preferences, newest first, capped at MAX."""
    stmt = (
        select(AgentUserPreference)
        .where(
            AgentUserPreference.user_id == int(user_id),
            AgentUserPreference.active.is_(True),
        )
        .order_by(AgentUserPreference.updated_at.desc())
        .limit(MAX_ACTIVE_PREFERENCES)
    )
    if category:
        stmt = stmt.where(AgentUserPreference.category == _text(category))
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def list_all_preferences(
    db: AsyncSession,
    user_id: int,
) -> list[AgentUserPreference]:
    """List every row for a user regardless of active state (management page)."""
    stmt = (
        select(AgentUserPreference)
        .where(AgentUserPreference.user_id == int(user_id))
        .order_by(AgentUserPreference.updated_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def upsert_preference(
    db: AsyncSession,
    user_id: int,
    rule: str,
    *,
    category: str = "general",
    source: str = "user_declared",
) -> AgentUserPreference:
    """Create a new active preference, or reactivate an identical inactive one.

    Deduplicates by normalized rule text: repeating the same rule updates the
    existing row instead of stacking near-duplicate prompt lines. Returns the
    stored row. Caller owns the transaction (commit/rollback).
    """
    text = _text(rule)
    if not text:
        raise ValueError("rule 不能为空")
    if source not in {"user_declared", "assistant_suggested"}:
        source = "user_declared"
    if category not in {"general", "rectification", "record", "writing"}:
        category = "general"
    user_id = int(user_id)

    # 归一化去重：rule 文本 strip 后全等即视为同一条（忽略空白差异）
    existing = (
        await db.execute(
            select(AgentUserPreference).where(
                AgentUserPreference.user_id == user_id,
                AgentUserPreference.rule == text,
            )
        )
    ).scalars().all()

    if existing:
        row = existing[0]
        # 主键相同则更新延伸信息；否则复用一条即可
        row.active = True
        row.category = category
        row.source = source
        row.updated_at = datetime.now()
        # 若历史里已有同文本的旧多行，一并去重置 inactive（保留最新一条活跃）
        for stale in existing[1:]:
            stale.active = False
        return row

    active_count = (
        await db.execute(
            select(AgentUserPreference.id).where(
                AgentUserPreference.user_id == user_id,
                AgentUserPreference.active.is_(True),
            )
        )
    ).scalars().all()
    # 超上限时把最旧的 active 置 false，换取新规则入队（不丢历史）
    if len(active_count) >= MAX_ACTIVE_PREFERENCES:
        overflow = (
            await db.execute(
                select(AgentUserPreference)
                .where(
                    AgentUserPreference.user_id == user_id,
                    AgentUserPreference.active.is_(True),
                )
                .order_by(AgentUserPreference.updated_at.asc())
                .limit(len(active_count) - MAX_ACTIVE_PREFERENCES + 1)
            )
        ).scalars().all()
        for row in overflow:
            row.active = False

    row = AgentUserPreference(
        user_id=user_id,
        rule=text,
        category=category,
        source=source,
        active=True,
    )
    db.add(row)
    await db.flush()
    return row


async def set_preference_active(
    db: AsyncSession,
    user_id: int,
    preference_id: int,
    active: bool,
) -> bool:
    """Enable/disable one preference row. Returns False when not found/foreign."""
    result = await db.execute(
        update(AgentUserPreference)
        .where(
            AgentUserPreference.id == int(preference_id),
            AgentUserPreference.user_id == int(user_id),
        )
        .values(active=bool(active), updated_at=datetime.now())
    )
    return bool(result.rowcount)


def render_preferences_block(
    preferences: Sequence[AgentUserPreference] | None,
) -> str:
    """Render active preferences into a compact system-prompt block.

    Empties to "" so caller can skip adding a message entirely. Kept as a
    separate system message (not merged into _AGENT_SYSTEM) so the router
    template stays byte-stable for eval baselines.
    """
    rows = [p for p in (preferences or []) if p is not None and _text(getattr(p, "rule", ""))]
    if not rows:
        return ""
    lines: list[str] = [
        "用户长期偏好（跨会话有效；除非本条与用户当前明确指令冲突，否则请遵守）："
    ]
    for row in rows:
        prefix = "「" + _text(row.rule) + "」"
        lines.append(f"- {prefix}")
    return "\n".join(lines)