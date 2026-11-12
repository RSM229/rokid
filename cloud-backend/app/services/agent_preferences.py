"""个人记忆（原「用户长期偏好」）—— 跨会话注入 agent 上下文的读写服务。

与 ``agent_context``（会话内事实/草稿）、``AgentConversationMemory``
（会话原话回忆）和 ``AgentProjectMemory``（项目级共享规则）的区别：

- 会话内草稿随工具结束即清；个人记忆**永久保存**，直到用户关闭。
- 会话原话只用于「回顾早先说过什么」；个人记忆是「用户自己的做事规则」，
  注入系统提示词后**持续影响模型行为**（如「整改回复必须附前后对比照片」）。
- 项目规约由项目总监维护、全项目共享；个人记忆只属于本人。

隔离口径：按 ``user_id`` 隔离，跨项目不串。``project_id`` 为空表示「跟人走、
所有项目生效」，非空表示「仅该项目生效」——读路径只合并「全局 + 当前项目」。

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

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_draft import AgentUserPreference

logger = logging.getLogger(__name__)

#: 单用户活跃偏好上限，防止提示词无限膨胀（每条约 30~60 token）
MAX_ACTIVE_PREFERENCES = 20


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _binding(project_id: int | None):
    """个人记忆的作用域绑定条件。"""
    if project_id is None:
        return AgentUserPreference.project_id.is_(None)
    return AgentUserPreference.project_id == int(project_id)


def _visible_in_project(project_id: int | None):
    """注入/管理页可见：全局记忆 + 当前项目绑定的记忆。"""
    if project_id is None:
        return AgentUserPreference.project_id.is_(None)
    return or_(
        AgentUserPreference.project_id.is_(None),
        AgentUserPreference.project_id == int(project_id),
    )


async def list_active_preferences(
    db: AsyncSession,
    user_id: int,
    *,
    project_id: int | None = None,
    category: str | None = None,
) -> list[AgentUserPreference]:
    """Read the user's active memories visible in the current project."""
    stmt = (
        select(AgentUserPreference)
        .where(
            AgentUserPreference.user_id == int(user_id),
            AgentUserPreference.active.is_(True),
            _visible_in_project(project_id),
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
    *,
    project_id: int | None = None,
) -> list[AgentUserPreference]:
    """List every row visible in the current project (management page).

    管理页与注入保持同一作用域口径：「全局个人记忆 + 当前项目绑定的记忆」。
    其他项目绑定的个人记忆不会出现在当前项目页面，避免跨项目串味。
    """
    stmt = select(AgentUserPreference).where(
        AgentUserPreference.user_id == int(user_id),
        _visible_in_project(project_id),
    )
    rows = (await db.execute(
        stmt.order_by(AgentUserPreference.updated_at.desc())
    )).scalars().all()
    return list(rows)


async def get_preference(
    db: AsyncSession,
    user_id: int,
    preference_id: int,
) -> AgentUserPreference | None:
    """按 id 取本人一条记忆（发布到项目前校验归属）。"""
    return (
        await db.execute(
            select(AgentUserPreference).where(
                AgentUserPreference.id == int(preference_id),
                AgentUserPreference.user_id == int(user_id),
            )
        )
    ).scalars().first()


async def upsert_preference(
    db: AsyncSession,
    user_id: int,
    rule: str,
    *,
    category: str = "general",
    source: str = "user_declared",
    project_id: int | None = None,
) -> AgentUserPreference:
    """Create a new active preference, or reactivate an identical inactive one.

    Deduplicates by normalized rule text within the same binding: repeating the
    same rule updates the existing row instead of stacking near-duplicate prompt
    lines. Returns the stored row. Caller owns the transaction (commit/rollback).
    """
    text = _text(rule)
    if not text:
        raise ValueError("rule 不能为空")
    if source not in {"user_declared", "assistant_suggested"}:
        source = "user_declared"
    if category not in {"general", "rectification", "record", "writing"}:
        category = "general"
    user_id = int(user_id)
    binding_project_id = int(project_id) if project_id is not None else None

    # 归一化去重：同一作用域内 rule 文本 strip 后全等即视为同一条
    existing = (
        await db.execute(
            select(AgentUserPreference).where(
                AgentUserPreference.user_id == user_id,
                AgentUserPreference.rule == text,
                _binding(binding_project_id),
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
        project_id=binding_project_id,
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


async def set_preference_scope(
    db: AsyncSession,
    user_id: int,
    preference_id: int,
    project_id: int | None,
) -> AgentUserPreference | None:
    """把一条个人记忆改为「全局」或「仅当前项目」生效。

    ``project_id=None`` = 全局跟人走；非空 = 仅该项目生效。
    同一作用域内不允许出现两条完全相同的规则（否则会重复注入）。
    """
    row = await get_preference(db, user_id, preference_id)
    if row is None:
        return None
    binding_project_id = int(project_id) if project_id is not None else None
    if row.project_id == binding_project_id:
        return row
    duplicate = await db.scalar(
        select(AgentUserPreference.id).where(
            AgentUserPreference.user_id == int(user_id),
            AgentUserPreference.rule == row.rule,
            _binding(binding_project_id),
            AgentUserPreference.id != row.id,
        ).limit(1)
    )
    if duplicate is not None:
        raise ValueError("目标作用域里已有相同的记忆")
    row.project_id = binding_project_id
    row.updated_at = datetime.now()
    await db.flush()
    return row


async def update_preference(
    db: AsyncSession,
    user_id: int,
    preference_id: int,
    rule: str,
    *,
    category: str = "general",
) -> AgentUserPreference | None:
    """Edit one saved rule without allowing cross-account access or duplicates."""
    text = _text(rule)
    if not text:
        raise ValueError("rule 不能为空")
    if category not in {"general", "rectification", "record", "writing"}:
        category = "general"
    row = await db.scalar(select(AgentUserPreference).where(
        AgentUserPreference.id == int(preference_id),
        AgentUserPreference.user_id == int(user_id),
    ))
    if row is None:
        return None
    duplicate = await db.scalar(select(AgentUserPreference.id).where(
        AgentUserPreference.user_id == int(user_id),
        AgentUserPreference.rule == text,
        _binding(row.project_id),
        AgentUserPreference.id != row.id,
    ).limit(1))
    if duplicate is not None:
        raise ValueError("已经有一条相同的记忆")
    row.rule = text
    row.category = category
    row.updated_at = datetime.now()
    await db.flush()
    return row


async def delete_preference(
    db: AsyncSession,
    user_id: int,
    preference_id: int,
) -> bool:
    """Permanently remove one saved preference owned by the user."""
    row = await db.scalar(select(AgentUserPreference).where(
        AgentUserPreference.id == int(preference_id),
        AgentUserPreference.user_id == int(user_id),
    ))
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True


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
        "我的记忆（本用户长期习惯，跨会话有效；与「本项目规约」冲突时以项目规约为准；"
        "除非与本轮用户明确指令冲突，否则请遵守）："
    ]
    for row in rows:
        prefix = "「" + _text(row.rule) + "」"
        lines.append(f"- {prefix}")
    return "\n".join(lines)
