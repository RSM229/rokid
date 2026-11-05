"""Create, resolve, and retire durable collaboration responsibilities."""
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collaboration import CollaborationEvent, CollaborationItem


async def upsert_action_items(
    db: AsyncSession,
    *,
    project_id: int,
    sender_id: int,
    recipient_ids: Iterable[int],
    object_type: str,
    object_id: int,
    action_type: str,
    title: str,
    description: str = "",
    metadata: dict | None = None,
    source_prefix: str = "auto",
    retire_missing: bool = False,
) -> list[CollaborationItem]:
    items: list[CollaborationItem] = []
    normalized_recipients = list(
        dict.fromkeys(int(value) for value in recipient_ids)
    )
    for recipient_id in normalized_recipients:
        source_key = (
            f"{source_prefix}:{object_type}:{object_id}:{action_type}:{recipient_id}"
        )
        item = (
            await db.execute(
                select(CollaborationItem).where(
                    CollaborationItem.source_key == source_key
                )
            )
        ).scalar_one_or_none()
        if item is None:
            item = CollaborationItem(
                project_id=project_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                mode="action",
                object_type=object_type,
                object_id=object_id,
                action_type=action_type,
                status="pending",
                title=title,
                description=description or None,
                metadata_json=metadata or {},
                source_key=source_key,
            )
            db.add(item)
            await db.flush()
            db.add(
                CollaborationEvent(
                    item_id=item.id, actor_id=sender_id, action="created"
                )
            )
        else:
            was_pending = item.status == "pending"
            item.sender_id = sender_id
            item.title = title
            item.description = description or None
            item.metadata_json = metadata or {}
            item.status = "pending"
            item.completed_at = None
            if not was_pending:
                item.read_at = None
                db.add(
                    CollaborationEvent(
                        item_id=item.id, actor_id=sender_id, action="reopened"
                    )
                )
        items.append(item)

    if retire_missing:
        # Workflow recipients are derived from the current project role matrix.
        # When an approver loses the role (or leaves the project), their old
        # pending responsibility must disappear from the badge immediately.
        current_recipients = set(normalized_recipients)
        stale_items = (
            await db.execute(
                select(CollaborationItem).where(
                    CollaborationItem.mode == "action",
                    CollaborationItem.object_type == object_type,
                    CollaborationItem.object_id == object_id,
                    CollaborationItem.action_type == action_type,
                    CollaborationItem.status == "pending",
                    CollaborationItem.source_key.like(f"{source_prefix}:%"),
                )
            )
        ).scalars().all()
        for stale in stale_items:
            if stale.recipient_id in current_recipients:
                continue
            stale.status = "canceled"
            stale.completed_at = datetime.now()
            db.add(
                CollaborationEvent(
                    item_id=stale.id,
                    actor_id=sender_id,
                    action="canceled",
                    detail="当前审批责任已调整，原责任人不再需要处理",
                )
            )
    return items


async def complete_action_items(
    db: AsyncSession,
    *,
    object_type: str,
    object_id: int,
    action_type: str | None = None,
    actor_id: int | None = None,
    recipient_id: int | None = None,
    detail: str = "",
) -> int:
    query = select(CollaborationItem).where(
        CollaborationItem.object_type == object_type,
        CollaborationItem.object_id == object_id,
        CollaborationItem.mode == "action",
        CollaborationItem.status == "pending",
    )
    if action_type:
        query = query.where(CollaborationItem.action_type == action_type)
    if recipient_id:
        query = query.where(CollaborationItem.recipient_id == recipient_id)
    items = (await db.execute(query)).scalars().all()
    now = datetime.now()
    for item in items:
        item.status = "completed"
        item.completed_at = now
        db.add(
            CollaborationEvent(
                item_id=item.id,
                actor_id=actor_id,
                action="completed",
                detail=detail or None,
            )
        )
    return len(items)


async def cancel_action_items(
    db: AsyncSession,
    *,
    object_type: str | None = None,
    object_id: int | None = None,
    action_type: str | None = None,
    project_id: int | None = None,
    recipient_id: int | None = None,
    actor_id: int | None = None,
    detail: str = "",
) -> int:
    """Retire outstanding responsibilities while preserving audit history."""
    query = select(CollaborationItem).where(
        CollaborationItem.mode == "action",
        CollaborationItem.status == "pending",
    )
    if object_type:
        query = query.where(CollaborationItem.object_type == object_type)
    if object_id is not None:
        query = query.where(CollaborationItem.object_id == object_id)
    if action_type:
        query = query.where(CollaborationItem.action_type == action_type)
    if project_id is not None:
        query = query.where(CollaborationItem.project_id == project_id)
    if recipient_id is not None:
        query = query.where(CollaborationItem.recipient_id == recipient_id)
    items = (await db.execute(query)).scalars().all()
    now = datetime.now()
    for item in items:
        item.status = "canceled"
        item.completed_at = now
        db.add(
            CollaborationEvent(
                item_id=item.id,
                actor_id=actor_id,
                action="canceled",
                detail=detail or "关联业务记录已失效或责任人已离开项目",
            )
        )
    return len(items)
