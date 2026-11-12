"""项目级记忆（共享记忆）的读写服务。

口径（2026-11-09 与产品确认）
----------------------------
- 记忆挂在 ``project_id`` 上：换总监/总监离场不丢，新总监直接继承；
- 维护岗位：当前项目 ``chief_supervisor`` 为主，``deputy_chief`` 可代管，
  ``super_admin`` 兜底；普通成员只能提交 ``pending`` 建议，经总监审批后生效；
- 读取：本项目有效成员；公司管理员排障可读；
- 注入优先级：项目规约 > 个人记忆；用户本轮明确指令仍优先于两者。

与 ``agent_preferences``（个人记忆）物理分表，互不影响；发布个人记忆到项目
是**复制**并保留 ``origin_preference_id``，不是引用。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_draft import (
    AgentProjectMemory,
    AgentProjectMemoryAudit,
    AgentUserPreference,
)
from app.models.project_member import ProjectMember
from app.models.user import User
from app.services.agent_scope import PROJECT_MEMORY_MANAGER_ROLES

logger = logging.getLogger(__name__)

CATEGORIES = ("general", "rectification", "record", "writing")
KINDS = ("rule", "fact", "template")
SOURCES = ("chief_declared", "deputy_declared", "member_suggested", "system")

#: 单项目活跃记忆上限，防止项目级 prompt 无上限膨胀（每条约 30~60 token）。
MAX_ACTIVE_PROJECT_MEMORIES = 20
#: 待审批建议上限，避免成员刷屏。
MAX_PENDING_PROJECT_MEMORIES = 20

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_PENDING = "pending"
STATUS_REJECTED = "rejected"


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _source_for_role(role_codes: Sequence[str] | None, *, fallback: str = "chief_declared") -> str:
    held = set(role_codes or ())
    if "chief_supervisor" in held:
        return "chief_declared"
    if "deputy_chief" in held:
        return "deputy_declared"
    return fallback


def _audit(
    db: AsyncSession,
    project_id: int,
    memory_id: int | None,
    action: str,
    actor_id: int | None,
    detail: dict[str, Any] | None = None,
) -> None:
    """写一条项目规约审计流水；由调用方事务提交。"""
    db.add(AgentProjectMemoryAudit(
        project_id=int(project_id),
        memory_id=int(memory_id) if memory_id is not None else None,
        action=action[:24],
        actor_id=int(actor_id) if actor_id else None,
        detail_json=json.dumps(detail or {}, ensure_ascii=False)[:4000],
    ))


async def list_active_memories(
    db: AsyncSession,
    project_id: int,
    *,
    limit: int = MAX_ACTIVE_PROJECT_MEMORIES,
) -> list[AgentProjectMemory]:
    """注入用：只取 active 且 status=active 的项目规约。"""
    if not project_id:
        return []
    rows = (
        await db.execute(
            select(AgentProjectMemory)
            .where(
                AgentProjectMemory.project_id == int(project_id),
                AgentProjectMemory.active.is_(True),
                AgentProjectMemory.status == STATUS_ACTIVE,
            )
            .order_by(
                AgentProjectMemory.pinned.desc(),
                AgentProjectMemory.updated_at.desc(),
            )
            .limit(max(1, min(int(limit or MAX_ACTIVE_PROJECT_MEMORIES),
                              MAX_ACTIVE_PROJECT_MEMORIES)))
        )
    ).scalars().all()
    return list(rows)


async def list_management_memories(
    db: AsyncSession,
    project_id: int,
) -> list[AgentProjectMemory]:
    """管理页：生效中 + 已停用（不含待审/已驳回）。"""
    rows = (
        await db.execute(
            select(AgentProjectMemory)
            .where(
                AgentProjectMemory.project_id == int(project_id),
                AgentProjectMemory.status.in_((STATUS_ACTIVE, STATUS_DISABLED)),
            )
            .order_by(
                AgentProjectMemory.pinned.desc(),
                AgentProjectMemory.updated_at.desc(),
            )
        )
    ).scalars().all()
    return list(rows)


async def list_pending_memories(
    db: AsyncSession,
    project_id: int,
) -> list[AgentProjectMemory]:
    rows = (
        await db.execute(
            select(AgentProjectMemory)
            .where(
                AgentProjectMemory.project_id == int(project_id),
                AgentProjectMemory.status == STATUS_PENDING,
            )
            .order_by(AgentProjectMemory.updated_at.desc())
            .limit(MAX_PENDING_PROJECT_MEMORIES)
        )
    ).scalars().all()
    return list(rows)


async def get_memory(
    db: AsyncSession,
    project_id: int,
    memory_id: int,
) -> AgentProjectMemory | None:
    return (
        await db.execute(
            select(AgentProjectMemory).where(
                AgentProjectMemory.id == int(memory_id),
                AgentProjectMemory.project_id == int(project_id),
            )
        )
    ).scalars().first()


async def _active_count(db: AsyncSession, project_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(AgentProjectMemory.id)).where(
                    AgentProjectMemory.project_id == int(project_id),
                    AgentProjectMemory.active.is_(True),
                    AgentProjectMemory.status == STATUS_ACTIVE,
                )
            )
        ).scalar()
        or 0
    )


async def _pending_count(db: AsyncSession, project_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count(AgentProjectMemory.id)).where(
                    AgentProjectMemory.project_id == int(project_id),
                    AgentProjectMemory.status == STATUS_PENDING,
                )
            )
        ).scalar()
        or 0
    )


async def _find_same_content(
    db: AsyncSession,
    project_id: int,
    content: str,
) -> AgentProjectMemory | None:
    return (
        await db.execute(
            select(AgentProjectMemory).where(
                AgentProjectMemory.project_id == int(project_id),
                AgentProjectMemory.content == content,
            )
        )
    ).scalars().first()


async def create_memory(
    db: AsyncSession,
    project_id: int,
    content: str,
    *,
    actor_id: int | None,
    actor_roles: Sequence[str] | None = None,
    category: str = "general",
    kind: str = "rule",
    status: str = STATUS_ACTIVE,
    source: str | None = None,
    audit_action: str | None = None,
) -> AgentProjectMemory:
    """创建或复用一条项目规约（按内容去重，不堆近似重复行）。

    - 维护岗创建 → 直接 active；
    - 普通成员创建 → ``pending``，等总监审批；
    - 已存在同内容行：active/disabled 会复用并重新激活；pending 原样返回。
    """
    text = _text(content)[:500]
    if not text:
        raise ValueError("项目规约内容不能为空")
    if category not in CATEGORIES:
        category = "general"
    if kind not in KINDS:
        kind = "rule"
    if source not in SOURCES:
        source = _source_for_role(actor_roles)
    if status not in (STATUS_ACTIVE, STATUS_PENDING):
        status = STATUS_ACTIVE

    existing = await _find_same_content(db, project_id, text)
    if existing is not None:
        if existing.status == STATUS_PENDING:
            return existing
        if existing.status == STATUS_REJECTED:
            existing.status = STATUS_DISABLED
        if not existing.active:
            if await _active_count(db, project_id) >= MAX_ACTIVE_PROJECT_MEMORIES:
                raise ValueError(
                    f"本项目项目规约已达 {MAX_ACTIVE_PROJECT_MEMORIES} 条，请先停用旧条目"
                )
            existing.active = True
            existing.status = STATUS_ACTIVE
        existing.category = category
        existing.kind = kind
        existing.updated_at = datetime.now()
        await db.flush()
        _audit(db, project_id, existing.id, "update", actor_id, {
            "content": text[:120], "source": source, "reused": True,
        })
        return existing

    if status == STATUS_ACTIVE and await _active_count(db, project_id) >= MAX_ACTIVE_PROJECT_MEMORIES:
        raise ValueError(f"本项目项目规约已达 {MAX_ACTIVE_PROJECT_MEMORIES} 条，请先停用旧条目")
    if status == STATUS_PENDING and await _pending_count(db, project_id) >= MAX_PENDING_PROJECT_MEMORIES:
        raise ValueError("待总监确认的建议已达上限，请稍后再试")

    row = AgentProjectMemory(
        project_id=int(project_id),
        content=text,
        kind=kind,
        category=category,
        source=source,
        status=status,
        active=status == STATUS_ACTIVE,
        author_id=int(actor_id) if actor_id else None,
        approved_by=int(actor_id) if status == STATUS_ACTIVE and actor_id else None,
    )
    db.add(row)
    await db.flush()
    action = audit_action or (
        "suggest" if status == STATUS_PENDING else "create"
    )
    _audit(db, project_id, row.id, action, actor_id, {
        "content": text[:120], "source": source, "category": category,
    })
    return row


async def update_memory(
    db: AsyncSession,
    project_id: int,
    memory_id: int,
    *,
    content: str | None = None,
    category: str | None = None,
    kind: str | None = None,
    pinned: bool | None = None,
    actor_id: int | None = None,
) -> AgentProjectMemory | None:
    row = await get_memory(db, project_id, memory_id)
    if row is None:
        return None
    changed: list[str] = []
    if content is not None:
        text = _text(content)[:500]
        if not text:
            raise ValueError("项目规约内容不能为空")
        duplicate = await _find_same_content(db, project_id, text)
        if duplicate is not None and duplicate.id != row.id:
            raise ValueError("已经有一条相同的项目规约")
        row.content = text
        changed.append("content")
    if category is not None:
        row.category = category if category in CATEGORIES else "general"
        changed.append("category")
    if kind is not None:
        row.kind = kind if kind in KINDS else "rule"
        changed.append("kind")
    if pinned is not None:
        row.pinned = bool(pinned)
        changed.append("pinned")
    row.updated_at = datetime.now()
    await db.flush()
    if changed:
        _audit(db, project_id, row.id, "update", actor_id, {
            "fields": changed, "content": row.content[:120],
        })
    return row


async def set_memory_active(
    db: AsyncSession,
    project_id: int,
    memory_id: int,
    active: bool,
    *,
    actor_id: int | None = None,
) -> AgentProjectMemory | None:
    row = await get_memory(db, project_id, memory_id)
    if row is None:
        return None
    if row.status == STATUS_PENDING:
        raise ValueError("该条是待审批建议，请先审批")
    if active and not row.active:
        if await _active_count(db, project_id) >= MAX_ACTIVE_PROJECT_MEMORIES:
            raise ValueError(
                f"本项目项目规约已达 {MAX_ACTIVE_PROJECT_MEMORIES} 条，请先停用旧条目"
            )
    row.active = bool(active)
    row.status = STATUS_ACTIVE if active else STATUS_DISABLED
    if active and actor_id:
        row.approved_by = int(actor_id)
    row.updated_at = datetime.now()
    await db.flush()
    _audit(
        db, project_id, row.id, "enable" if active else "disable", actor_id,
        {"content": row.content[:120]},
    )
    return row


async def delete_memory(
    db: AsyncSession,
    project_id: int,
    memory_id: int,
    *,
    actor_id: int | None = None,
) -> bool:
    row = await get_memory(db, project_id, memory_id)
    if row is None:
        return False
    _audit(db, project_id, row.id, "delete", actor_id, {
        "content": row.content[:120], "source": row.source,
    })
    await db.delete(row)
    await db.flush()
    return True


async def approve_memory(
    db: AsyncSession,
    project_id: int,
    memory_id: int,
    *,
    approve: bool,
    approver_id: int | None = None,
) -> AgentProjectMemory | None:
    row = await get_memory(db, project_id, memory_id)
    if row is None:
        return None
    if row.status != STATUS_PENDING:
        raise ValueError("该条建议不在待审批状态")
    if approve:
        if await _active_count(db, project_id) >= MAX_ACTIVE_PROJECT_MEMORIES:
            raise ValueError(
                f"本项目项目规约已达 {MAX_ACTIVE_PROJECT_MEMORIES} 条，请先停用旧条目"
            )
        row.status = STATUS_ACTIVE
        row.active = True
        row.approved_by = int(approver_id) if approver_id else None
    else:
        row.status = STATUS_REJECTED
        row.active = False
        row.approved_by = int(approver_id) if approver_id else None
    row.updated_at = datetime.now()
    await db.flush()
    _audit(
        db, project_id, row.id, "approve" if approve else "reject", approver_id,
        {"content": row.content[:120]},
    )
    return row


async def publish_preference(
    db: AsyncSession,
    project_id: int,
    preference: AgentUserPreference,
    *,
    actor_id: int | None,
    actor_roles: Sequence[str] | None = None,
) -> AgentProjectMemory:
    """把个人记忆复制为项目规约（总监/代管操作，保留来源 id）。"""
    row = await create_memory(
        db,
        project_id,
        preference.rule,
        actor_id=actor_id,
        actor_roles=actor_roles,
        category=preference.category or "general",
        status=STATUS_ACTIVE,
        source=_source_for_role(actor_roles),
        audit_action="publish",
    )
    if row.origin_preference_id is None:
        row.origin_preference_id = int(preference.id)
    await db.flush()
    return row


async def record_usage(
    db: AsyncSession,
    project_id: int,
    memory_ids: Iterable[int] | None,
    *,
    actor_id: int | None = None,
    conversation_id: str | None = None,
) -> int:
    """记一轮项目规约注入：批量 +1 use_count / last_used_at，并写一条 use 审计。

    由聊天主链路在组装 prompt 前调用；调用方负责 commit。返回本次计数条数。
    """
    ids = sorted({int(item) for item in (memory_ids or []) if item})
    if not project_id or not ids:
        return 0
    now = datetime.now()
    await db.execute(
        update(AgentProjectMemory)
        .where(
            AgentProjectMemory.project_id == int(project_id),
            AgentProjectMemory.id.in_(ids),
        )
        .values(
            use_count=AgentProjectMemory.use_count + 1,
            last_used_at=now,
        )
    )
    _audit(db, project_id, None, "use", actor_id, {
        "memory_ids": ids,
        "conversation_id": (conversation_id or "")[:64],
    })
    await db.flush()
    return len(ids)


async def list_audit(
    db: AsyncSession,
    project_id: int,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """最近的项目规约变更/使用流水（带操作人姓名），供管理页审计展示。"""
    if not project_id:
        return []
    capped = max(1, min(int(limit or 50), 200))
    rows = (
        await db.execute(
            select(AgentProjectMemoryAudit)
            .where(AgentProjectMemoryAudit.project_id == int(project_id))
            .order_by(AgentProjectMemoryAudit.id.desc())
            .limit(capped)
        )
    ).scalars().all()
    actor_ids = {
        int(row.actor_id) for row in rows if getattr(row, "actor_id", None) is not None
    }
    names: dict[int, str] = {}
    if actor_ids:
        try:
            users = (
                await db.execute(select(User).where(User.id.in_(actor_ids)))
            ).scalars().all()
            names = {int(user.id): (user.real_name or user.username or "") for user in users}
        except Exception:  # noqa: BLE001 - 审计只是展示层，用户表异常不能拖垮管理页
            logger.warning("项目规约审计补操作人姓名失败", exc_info=True)
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            detail = json.loads(row.detail_json or "{}")
        except (TypeError, ValueError):
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        out.append({
            "id": row.id,
            "memory_id": row.memory_id,
            "action": row.action,
            "actor_id": row.actor_id,
            "actor_name": names.get(int(row.actor_id or 0), ""),
            "detail": detail,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        })
    return out


async def usage_summary(
    db: AsyncSession,
    project_id: int,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """按使用次数排序的项目规约（管理页「使用统计」）。"""
    if not project_id:
        return []
    rows = (
        await db.execute(
            select(AgentProjectMemory)
            .where(AgentProjectMemory.project_id == int(project_id))
            .order_by(
                AgentProjectMemory.use_count.desc(),
                AgentProjectMemory.last_used_at.desc(),
                AgentProjectMemory.updated_at.desc(),
            )
            .limit(max(1, min(int(limit or 20), 100)))
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "content": row.content,
            "status": row.status,
            "active": row.active,
            "use_count": int(row.use_count or 0),
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else "",
        }
        for row in rows
    ]


async def project_maintainers(
    db: AsyncSession,
    project_id: int,
) -> list[dict[str, Any]]:
    """项目规约的维护人（总监/总监代表），供管理页展示。"""
    if not project_id:
        return []
    rows = (
        await db.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(
                ProjectMember.project_id == int(project_id),
                ProjectMember.left_at.is_(None),
                User.is_active.is_(True),
            )
            .order_by(User.id)
        )
    ).all()
    out: list[dict[str, Any]] = []
    for member, user in rows:
        held = [code for code in member.role_codes() if code in PROJECT_MEMORY_MANAGER_ROLES]
        if not held:
            continue
        out.append({
            "user_id": int(user.id),
            "name": _text(user.real_name) or _text(user.username),
            "role": held[0],
        })
    return out


def render_project_memories_block(
    memories: Iterable[AgentProjectMemory] | None,
) -> str:
    """把 active 项目规约渲染成独立的 system prompt 块。"""
    rows = [
        row for row in (memories or [])
        if row is not None and row.active and _text(getattr(row, "content", ""))
    ]
    if not rows:
        return ""
    lines = [
        "本项目规约（由项目总监维护，全项目统一遵守；与「我的记忆」冲突时以本条为准；"
        "与用户本轮明确指令冲突时，先提示用户并由总监确认，不要静默覆盖）："
    ]
    for row in rows[:MAX_ACTIVE_PROJECT_MEMORIES]:
        lines.append("- 「" + _text(row.content) + "」")
    return "\n".join(lines)
