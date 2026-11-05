"""Unified project collaboration inbox for the mini program."""
import hashlib
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.collaboration import CollaborationEvent, CollaborationFollower, CollaborationItem
from app.models.document import Document
from app.models.notification import Notification
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.work_record import WorkRecord
from app.models.task_assignment import TaskAssignment, TaskStatus
from app.routers.auth import get_current_user, require_project_id

router = APIRouter()


class CreateCollaborationRequest(BaseModel):
    object_type: str
    object_id: int
    recipient_id: int
    cc_recipient_ids: list[int] = Field(default_factory=list, max_length=50)
    mode: str = "action"
    action_type: str = "process"
    signoff_slot: str | None = None
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2000)
    deadline: str | None = None
    client_request_id: str | None = Field(default=None, max_length=128)


class ResolveRequest(BaseModel):
    detail: str = Field(default="", max_length=2000)


class TransferRequest(BaseModel):
    recipient_id: int
    cc_recipient_ids: list[int] = Field(default_factory=list, max_length=50)
    detail: str = Field(default="", max_length=2000)
    client_request_id: str | None = Field(default=None, max_length=128)


class RemindRequest(BaseModel):
    note: str = Field(default="", max_length=200)


ACTION_LABELS = {
    "created": "创建事项",
    "reopened": "重新进入待处理",
    "read": "查看详情",
    "resolved": "标记完成",
    "completed": "业务处理完成",
    "canceled": "撤回/失效",
    "transferred": "转派",
    "cc_added": "新增抄送",
    "reminded": "催办",
}

STATUS_LABELS = {
    "pending": "待处理",
    "tracking": "跟踪中",
    "completed": "已完成",
    "canceled": "已撤回",
    "transferred": "已转派",
}

ACTION_TYPE_LABELS = {
    "approve": "业务审批",
    "process": "请处理",
    "track": "抄送跟踪",
}

TERMINAL_STATUSES = ("completed", "canceled", "transferred")
REMIND_COOLDOWN_SECONDS = 10 * 60


def _parse_deadline(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise HTTPException(400, "截止日期必须使用 YYYY-MM-DD 格式") from exc


def _deadline_fields(item: CollaborationItem) -> tuple[str | None, str, str, int | None]:
    raw = (item.metadata_json or {}).get("deadline")
    if not raw:
        return None, "none", "", None
    try:
        due = date.fromisoformat(str(raw))
    except ValueError:
        return str(raw), "none", "", None
    if item.status in TERMINAL_STATUSES:
        return due.isoformat(), "closed", "", None
    delta = (due - date.today()).days
    if delta < 0:
        return due.isoformat(), "overdue", f"已逾期 {-delta} 天", delta
    if delta == 0:
        return due.isoformat(), "due_today", "今天截止", 0
    return due.isoformat(), "normal", f"截止 {due.isoformat()}", delta


def _source_label(item: CollaborationItem) -> str:
    key = item.source_key or ""
    if key.startswith("manual:transfer:"):
        return "转派"
    if key.startswith("manual:"):
        return "人工发起"
    if key.startswith("workflow"):
        return "流程自动"
    return "系统"


def _viewer_role(item: CollaborationItem, viewer_id: int | None, follower_ids: set[int]) -> str:
    if viewer_id == item.sender_id:
        return "sender"
    if viewer_id == item.recipient_id:
        return "recipient"
    if viewer_id in follower_ids:
        return "follower"
    return "observer"


def _remind_state(item: CollaborationItem) -> tuple[bool, str | None]:
    if item.mode != "action" or item.status != "pending":
        return False, None
    raw = (item.metadata_json or {}).get("last_reminded_at")
    if not raw:
        return True, None
    try:
        last = datetime.fromisoformat(str(raw))
    except ValueError:
        return True, None
    next_at = last + timedelta(seconds=REMIND_COOLDOWN_SECONDS)
    if datetime.now() >= next_at:
        return True, None
    return False, next_at.isoformat()


async def _notify(
    db: AsyncSession,
    user_ids: list[int | None],
    *,
    title: str,
    body: str,
    event_type: str,
    item: CollaborationItem,
    actor_id: int | None = None,
) -> None:
    payload = json.dumps({
        "item_id": item.id,
        "project_id": item.project_id,
        "object_type": item.object_type,
        "object_id": item.object_id,
        "status": item.status,
        "route": f"/pages/todo/index?item_id={item.id}",
    }, ensure_ascii=False)
    now = datetime.now().timestamp()
    for user_id in dict.fromkeys(int(value) for value in user_ids if value):
        if actor_id is not None and user_id == int(actor_id):
            continue
        db.add(Notification(
            user_id=user_id,
            title=title,
            body=body,
            event_type=event_type,
            payload=payload,
            ts=now,
        ))


async def _locked_item(
    db: AsyncSession, item_id: int, project_id: int
) -> CollaborationItem | None:
    return (
        await db.execute(
            select(CollaborationItem)
            .where(
                CollaborationItem.id == item_id,
                CollaborationItem.project_id == project_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _require_project_member(db: AsyncSession, user: User, project_id: int) -> None:
    member_id = await db.scalar(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
            ProjectMember.left_at.is_(None),
        )
    )
    if not member_id:
        raise HTTPException(403, "您不是该项目在岗成员，无法访问协作事项")


def _existing_object_clause():
    """Only count responsibilities whose linked business object still exists."""
    return or_(
        and_(
            CollaborationItem.object_type == "document",
            exists(select(Document.id).where(Document.id == CollaborationItem.object_id)),
        ),
        and_(
            CollaborationItem.object_type == "work_record",
            exists(select(WorkRecord.id).where(WorkRecord.id == CollaborationItem.object_id)),
        ),
    )


async def _business_object(
    db: AsyncSession,
    project_id: int,
    object_type: str,
    object_id: int,
    *,
    required: bool = True,
):
    model = {"work_record": WorkRecord, "document": Document}.get(object_type)
    if model is None:
        raise HTTPException(400, "object_type 仅支持 work_record/document")
    obj = await db.get(model, object_id)
    if not obj or obj.project_id != project_id:
        if not required:
            return None
        raise HTTPException(404, "关联业务记录不存在")
    return obj


def _mode_label(item: CollaborationItem, viewer_id: int | None) -> str:
    if viewer_id == item.sender_id:
        return "我发出的"
    if viewer_id == item.recipient_id:
        if item.status == "completed":
            return "已完成"
        if item.status == "canceled":
            return "已撤回"
        if item.status == "transferred":
            return "已转派"
        if item.mode == "cc":
            return "抄送跟踪"
        return "待我处理"
    return "抄送跟踪"


async def _serialize(db: AsyncSession, item: CollaborationItem, viewer_id: int | None = None) -> dict:
    sender = await db.get(User, item.sender_id)
    recipient = await db.get(User, item.recipient_id)
    followers = (await db.execute(
        select(CollaborationFollower).where(CollaborationFollower.item_id == item.id)
    )).scalars().all()
    follower_ids = {row.user_id for row in followers}
    follower_users = (await db.execute(
        select(User).where(User.id.in_(follower_ids or {-1}))
    )).scalars().all()
    follower_by_id = {row.id: row for row in follower_users}
    follower_rows = {row.user_id: row for row in followers}
    read_at = item.read_at
    if viewer_id and viewer_id != item.recipient_id:
        read_at = follower_rows[viewer_id].read_at if viewer_id in follower_rows else None
    deadline, deadline_state, deadline_label, days_until_due = _deadline_fields(item)
    metadata = item.metadata_json or {}
    can_remind, remind_available_at = _remind_state(item) if viewer_id == item.sender_id else (False, None)
    return {
        "id": item.id,
        "project_id": item.project_id,
        "sender_id": item.sender_id,
        "sender_name": sender.real_name if sender else "未知",
        "recipient_id": item.recipient_id,
        "recipient_name": recipient.real_name if recipient else "未知",
        "cc_recipient_names": [row.real_name for row in follower_users],
        "cc_names_text": "、".join(row.real_name for row in follower_users),
        "cc_recipients": [
            {
                "id": user_id,
                "real_name": follower_by_id[user_id].real_name if user_id in follower_by_id else "未知",
                "read_at": follower_rows[user_id].read_at.isoformat() if follower_rows[user_id].read_at else None,
            }
            for user_id in sorted(follower_ids)
        ],
        "viewer_role": _viewer_role(item, viewer_id, follower_ids),
        "mode": item.mode,
        "mode_label": _mode_label(item, viewer_id),
        "object_type": item.object_type,
        "object_id": item.object_id,
        "object_label": "工作记录" if item.object_type == "work_record" else "工作文书",
        "action_type": item.action_type,
        "is_signoff": item.action_type.startswith("signoff:"),
        "status": item.status,
        "status_label": STATUS_LABELS.get(item.status, item.status),
        "action_label": (
            "文书签认" if item.action_type.startswith("signoff:")
            else ACTION_TYPE_LABELS.get(item.action_type, item.action_type)
        ),
        "title": item.title,
        "description": item.description or "",
        "metadata": metadata,
        "deadline": deadline,
        "deadline_state": deadline_state,
        "deadline_label": deadline_label,
        "days_until_due": days_until_due,
        "source": _source_label(item),
        "resolution": metadata.get("resolution") or "",
        "completed_by_id": metadata.get("resolved_by_id"),
        "completed_by_name": metadata.get("resolved_by_name") or "",
        "last_reminded_at": metadata.get("last_reminded_at"),
        "last_reminder_note": metadata.get("last_reminder_note") or "",
        "read_at": read_at.isoformat() if read_at else None,
        "is_read": bool(read_at),
        "can_resolve": bool(
            viewer_id == item.recipient_id and item.mode == "action"
            and item.action_type == "process" and item.status == "pending"
        ),
        "can_cancel": bool(
            viewer_id == item.sender_id and item.status == "pending"
            and item.source_key.startswith("manual:")
            and (item.mode == "cc" or item.action_type == "process")
        ),
        "can_transfer": bool(
            viewer_id == item.recipient_id and item.mode == "action"
            and item.action_type == "process" and item.status == "pending"
        ),
        "can_remind": can_remind,
        "remind_available_at": remind_available_at,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


@router.get("/inbox")
async def inbox(
    view: str = "action",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
    object_type: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    if view not in {"action", "cc", "sent", "done", "tasks"}:
        raise HTTPException(400, "view 仅支持 action/cc/sent/done/tasks")
    if status and status not in {"pending", "tracking", "completed", "canceled", "transferred"}:
        raise HTTPException(400, "status 不是有效的协作状态")
    if object_type and object_type not in {"work_record", "document"}:
        raise HTTPException(400, "object_type 仅支持 work_record/document")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    follows = exists(select(CollaborationFollower.id).where(
        CollaborationFollower.item_id == CollaborationItem.id,
        CollaborationFollower.user_id == user.id,
    ))
    base = [CollaborationItem.project_id == project_id]
    action_conditions = base + [
        CollaborationItem.recipient_id == user.id,
        CollaborationItem.mode == "action",
        CollaborationItem.status == "pending",
        _existing_object_clause(),
    ]
    cc_conditions = base + [or_(
        and_(CollaborationItem.recipient_id == user.id, CollaborationItem.mode == "cc"),
        follows,
    )]
    sent_conditions = base + [CollaborationItem.sender_id == user.id]
    done_conditions = base + [
        CollaborationItem.recipient_id == user.id,
        CollaborationItem.mode == "action",
        CollaborationItem.status.in_(TERMINAL_STATUSES),
    ]
    conditions = {
        "action": action_conditions,
        "cc": cc_conditions,
        "sent": sent_conditions,
        "done": done_conditions,
        "tasks": action_conditions,
    }[view]
    query = select(CollaborationItem).where(and_(*conditions))
    if status and view not in {"action", "tasks"}:
        query = query.where(CollaborationItem.status == status)
    if object_type:
        query = query.where(CollaborationItem.object_type == object_type)
    normalized_keyword = (keyword or "").strip().lower()
    if normalized_keyword:
        query = query.where(or_(
            func.lower(CollaborationItem.title).contains(normalized_keyword),
            func.lower(CollaborationItem.description).contains(normalized_keyword),
        ))
    if view == "done":
        query = query.order_by(
            CollaborationItem.completed_at.desc(), CollaborationItem.id.desc()
        )
    else:
        query = query.order_by(CollaborationItem.created_at.desc(), CollaborationItem.id.desc())
    page_size = limit
    fetched = (await db.execute(
        query.offset(offset).limit(page_size + 1)
    )).scalars().all()
    has_more = len(fetched) > page_size
    rows = fetched[:page_size]

    action_count = int(await db.scalar(
        select(func.count(CollaborationItem.id)).where(and_(*action_conditions))
    ) or 0)
    cc_count = int(await db.scalar(
        select(func.count(CollaborationItem.id)).where(and_(*cc_conditions))
    ) or 0)
    sent_count = int(await db.scalar(
        select(func.count(CollaborationItem.id)).where(and_(*sent_conditions))
    ) or 0)
    done_count = int(await db.scalar(
        select(func.count(CollaborationItem.id)).where(and_(*done_conditions))
    ) or 0)
    task_pending = int(await db.scalar(
        select(func.count(TaskAssignment.id)).where(
            TaskAssignment.project_id == project_id,
            TaskAssignment.assignee_id == user.id,
            TaskAssignment.status.in_([
                TaskStatus.PENDING.value,
                TaskStatus.ACCEPTED.value,
                TaskStatus.IN_PROGRESS.value,
            ]),
        )
    ) or 0)
    task_total = int(await db.scalar(
        select(func.count(TaskAssignment.id)).where(
            TaskAssignment.project_id == project_id,
            TaskAssignment.assignee_id == user.id,
        )
    ) or 0)

    task_query = select(TaskAssignment).where(
        TaskAssignment.project_id == project_id,
        TaskAssignment.assignee_id == user.id,
    )
    tasks = (await db.execute(task_query.order_by(TaskAssignment.created_at.desc()).limit(100))).scalars().all()
    task_senders = {
        row.id: row.real_name
        for row in (await db.execute(select(User).where(User.id.in_([t.assigner_id for t in tasks] or [-1])))).scalars().all()
    }
    counts = {
        "action": action_count + task_pending,
        "collaboration_action": action_count,
        "task_pending": task_pending,
        "cc": cc_count,
        "sent": sent_count,
        "done": done_count,
        "tasks": task_total,
    }
    return {
        "items": [await _serialize(db, row, user.id) for row in rows],
        "tasks": [
            {"id": row.id, "title": row.title, "description": row.description or "",
             "status": row.status, "priority": row.priority, "category": row.category,
             "assigner_name": task_senders.get(row.assigner_id, "未知"),
             "deadline": row.deadline.isoformat() if row.deadline else None,
             "location": row.location, "section": row.section, "created_at": row.created_at.isoformat()}
            for row in tasks
        ],
        "pending_count": counts["action"],
        "counts": counts,
        "has_more": has_more,
        "next_offset": offset + len(rows) if has_more else None,
    }


@router.get("/members")
async def project_members(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    rows = (
        await db.execute(
            select(User.id, User.real_name, ProjectMember.role)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.left_at.is_(None),
                User.is_active.is_(True),
            )
            .order_by(User.real_name)
        )
    ).all()
    return [{"id": row.id, "real_name": row.real_name, "role": row.role} for row in rows]


@router.post("")
async def create_item(
    req: CreateCollaborationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    obj = await _business_object(db, project_id, req.object_type, req.object_id)
    title = req.title.strip()
    if not title:
        raise HTTPException(400, "事项标题不能为空")
    if req.mode not in {"action", "cc"}:
        raise HTTPException(400, "mode 仅支持 action/cc")
    if req.mode == "cc" and req.action_type != "process":
        raise HTTPException(400, "抄送事项不能改变业务处理类型")
    if req.mode == "action" and req.recipient_id == user.id:
        raise HTTPException(400, "不能将待处理事项指派给自己")
    member_id = await db.scalar(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == req.recipient_id,
            ProjectMember.left_at.is_(None),
        )
    )
    recipient = await db.get(User, req.recipient_id)
    if not member_id or not recipient or not recipient.is_active:
        raise HTTPException(400, "接收人必须是当前项目的有效成员")
    cc_ids = list(dict.fromkeys(int(value) for value in req.cc_recipient_ids))
    if req.recipient_id in cc_ids:
        raise HTTPException(400, "主处理人不能同时作为抄送人")
    if user.id in cc_ids:
        raise HTTPException(400, "不能抄送给自己")
    if cc_ids:
        valid_cc = set((await db.execute(
            select(ProjectMember.user_id)
            .join(User, User.id == ProjectMember.user_id)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id.in_(cc_ids),
                ProjectMember.left_at.is_(None),
                User.is_active.is_(True),
            )
        )).scalars().all())
        if valid_cc != set(cc_ids):
            raise HTTPException(400, "所有抄送人都必须是当前项目的有效成员")
    if isinstance(obj, WorkRecord) and obj.creator_id != user.id and not user.has_permission("task_manage"):
        raise HTTPException(403, "无权发起该记录的协作")
    if isinstance(obj, Document) and obj.creator_id != user.id and not user.has_permission("document_approve"):
        raise HTTPException(403, "无权发起该文书的协作")
    action_type = "process"
    deadline = _parse_deadline(req.deadline)
    metadata = {"deadline": deadline} if deadline else {}
    if req.mode == "action" and req.action_type == "signoff":
        if not isinstance(obj, Document) or not req.signoff_slot:
            raise HTTPException(400, "文书签认待办必须关联文书和签认位置")
        from app.services.document_signoffs import (
            assert_slot_authorized, current_offline_signoffs, current_signoffs,
            required_any_slot_groups_for, required_slots_for,
        )
        slot = req.signoff_slot
        required = required_slots_for(obj)
        required.update(slot for group in required_any_slot_groups_for(obj) for slot in group)
        if slot not in required:
            raise HTTPException(400, "该签认位置不是此文书当前的必签项")
        signed_slots = {row.slot for row in await current_signoffs(db, obj)}
        signed_slots.update(current_offline_signoffs(obj))
        if slot in signed_slots:
            raise HTTPException(409, "该签认位置已经完成")
        previous_project_id = recipient.project_id
        previous_permissions = getattr(recipient, "effective_permissions", None)
        recipient.project_id = project_id
        from app.services.permissions import attach_effective_permissions
        await attach_effective_permissions(recipient, db)
        try:
            await assert_slot_authorized(db, obj, recipient, slot)
        finally:
            recipient.project_id = previous_project_id
            if previous_permissions is None:
                recipient.__dict__.pop("effective_permissions", None)
            else:
                recipient.effective_permissions = previous_permissions
        action_type = f"signoff:{slot}"
        metadata = {**metadata, "slot": slot, "revision": obj.content_revision or 1}
    elif req.mode == "action" and req.action_type != "process":
        raise HTTPException(400, "action_type 仅支持 process/signoff")
    if req.client_request_id:
        fingerprint_payload = {
            "object_type": req.object_type,
            "object_id": req.object_id,
            "recipient_id": req.recipient_id,
            "cc_recipient_ids": sorted(cc_ids),
            "mode": req.mode,
            "action_type": req.action_type,
            "signoff_slot": req.signoff_slot,
            "title": title,
            "description": req.description.strip(),
            "deadline": deadline,
        }
        request_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        metadata = {**metadata, "request_fingerprint": request_fingerprint}
        digest = hashlib.sha256(req.client_request_id.strip().encode("utf-8")).hexdigest()
        source_key = f"manual:{user.id}:request:{digest}"
        existing = (
            await db.execute(
                select(CollaborationItem).where(
                    CollaborationItem.source_key == source_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.project_id != project_id or existing.sender_id != user.id:
                raise HTTPException(409, "该幂等请求编号已被其他事项占用")
            stored_fingerprint = (existing.metadata_json or {}).get("request_fingerprint")
            if stored_fingerprint and stored_fingerprint != request_fingerprint:
                raise HTTPException(409, "同一幂等请求编号对应的事项内容不一致")
            return {"item": await _serialize(db, existing, user.id), "idempotent": True}
    else:
        source_key = (
            f"manual:{user.id}:{req.object_type}:{req.object_id}:"
            f"{req.recipient_id}:{req.mode}:{datetime.now().timestamp()}"
        )
    item = CollaborationItem(
        project_id=project_id,
        sender_id=user.id,
        recipient_id=req.recipient_id,
        mode=req.mode,
        object_type=req.object_type,
        object_id=req.object_id,
        action_type=action_type if req.mode == "action" else "track",
        status="pending" if req.mode == "action" else "tracking",
        title=title,
        description=req.description.strip() or None,
        metadata_json=metadata,
        source_key=source_key,
    )
    db.add(item)
    await db.flush()
    db.add(CollaborationEvent(item_id=item.id, actor_id=user.id, action="created"))
    for cc_id in cc_ids:
        db.add(CollaborationFollower(item_id=item.id, user_id=cc_id))
        db.add(CollaborationEvent(
            item_id=item.id, actor_id=user.id, action="cc_added", detail=str(cc_id)
        ))
    if item.mode == "action":
        await _notify(
            db, [item.recipient_id],
            title="你有一个待处理事项",
            body=f"{user.real_name} 请你处理「{item.title}」",
            event_type="collaboration_assigned", item=item, actor_id=user.id,
        )
    else:
        await _notify(
            db, [item.recipient_id],
            title="你有一个跟踪事项",
            body=f"{user.real_name} 邀请你跟踪「{item.title}」",
            event_type="collaboration_cc", item=item, actor_id=user.id,
        )
    if cc_ids:
        await _notify(
            db, cc_ids,
            title="你收到一个抄送事项",
            body=f"{user.real_name} 抄送了「{item.title}」",
            event_type="collaboration_cc", item=item, actor_id=user.id,
        )
    await db.commit()
    return {"item": await _serialize(db, item, user.id), "idempotent": False}


@router.get("/{item_id}")
async def detail(
    item_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    item = await db.get(CollaborationItem, item_id)
    is_follower = bool(item and await db.scalar(select(CollaborationFollower.id).where(
        CollaborationFollower.item_id == item_id,
        CollaborationFollower.user_id == user.id,
    )))
    if not item or item.project_id != project_id or user.id not in {item.sender_id, item.recipient_id} and not is_follower:
        raise HTTPException(404, "协作事项不存在或无权查看")
    if item.recipient_id == user.id and item.read_at is None:
        item.read_at = datetime.now()
        db.add(CollaborationEvent(item_id=item.id, actor_id=user.id, action="read"))
        await db.commit()
    elif is_follower:
        follower = (await db.execute(select(CollaborationFollower).where(
            CollaborationFollower.item_id == item_id,
            CollaborationFollower.user_id == user.id,
        ))).scalar_one()
        if follower.read_at is None:
            follower.read_at = datetime.now()
            db.add(CollaborationEvent(item_id=item.id, actor_id=user.id, action="read"))
            await db.commit()
    result = await _serialize(db, item, user.id)
    obj = await _business_object(
        db, project_id, item.object_type, item.object_id, required=False
    )
    result["object_deleted"] = obj is None
    result["object"] = (
        {
            "id": obj.id, "record_type": obj.record_type, "status": obj.status,
            "section": obj.section, "location": obj.location,
            "record_date": str(obj.record_date or ""), "work_content": obj.work_content,
            "reject_reason": obj.reject_reason,
        }
        if isinstance(obj, WorkRecord)
        else {
            "id": obj.id, "doc_type": obj.doc_type, "title": obj.title,
            "status": obj.status, "docx_url": obj.docx_path,
            "revision": obj.content_revision,
        }
        if isinstance(obj, Document)
        else None
    )
    history = (
        await db.execute(
            select(CollaborationEvent)
            .where(CollaborationEvent.item_id == item.id)
            .order_by(CollaborationEvent.created_at, CollaborationEvent.id)
        )
    ).scalars().all()
    actor_ids = {row.actor_id for row in history if row.actor_id}
    actors = {
        row.id: row.real_name
        for row in (await db.execute(
            select(User).where(User.id.in_(actor_ids or {-1}))
        )).scalars().all()
    }
    result["history"] = [
        {
            "action": row.action,
            "action_label": ACTION_LABELS.get(row.action, row.action),
            "actor_id": row.actor_id,
            "actor_name": actors.get(row.actor_id, ""),
            "detail": row.detail or "",
            "created_at": row.created_at.isoformat(),
        }
        for row in history
    ]
    return result


@router.post("/{item_id}/resolve")
async def resolve(
    item_id: int,
    req: ResolveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    item = await _locked_item(db, item_id, project_id)
    if not item or item.recipient_id != user.id:
        raise HTTPException(404, "协作事项不存在或无权处理")
    if item.mode != "action" or item.action_type != "process":
        raise HTTPException(400, "该事项必须通过对应业务动作处理")
    if item.status == "completed":
        return {"detail": "事项已完成", "status": "completed", "idempotent": True}
    if item.status != "pending":
        raise HTTPException(409, "该事项已经处理")
    now = datetime.now()
    note = req.detail.strip() or "已完成"
    item.status = "completed"
    item.completed_at = now
    item.metadata_json = {
        **(item.metadata_json or {}),
        "resolution": note,
        "resolved_by_id": user.id,
        "resolved_by_name": user.real_name,
        "resolved_at": now.isoformat(),
    }
    db.add(CollaborationEvent(item_id=item.id, actor_id=user.id, action="resolved", detail=note))
    followers = (await db.execute(select(CollaborationFollower).where(
        CollaborationFollower.item_id == item.id
    ))).scalars().all()
    await _notify(
        db, [item.sender_id, *[row.user_id for row in followers]],
        title="协作事项已完成",
        body=f"{user.real_name} 已完成「{item.title}」",
        event_type="collaboration_completed", item=item, actor_id=user.id,
    )
    await db.commit()
    return {"detail": "处理事项已完成", "status": item.status, "idempotent": False}


@router.post("/{item_id}/cancel")
async def cancel_item(
    item_id: int,
    req: ResolveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    item = await _locked_item(db, item_id, project_id)
    if not item or item.sender_id != user.id:
        raise HTTPException(404, "协作事项不存在或无权撤回")
    if not item.source_key.startswith("manual:") or not (
        item.mode == "cc" or item.action_type == "process"
    ):
        raise HTTPException(400, "仅发起的人工事项可以撤回")
    if item.status == "canceled":
        return {"detail": "事项已撤回", "status": "canceled", "idempotent": True}
    if item.status != "pending" and item.status != "tracking":
        raise HTTPException(409, "该事项已处理，不能撤回")
    now = datetime.now()
    note = req.detail.strip() or "发起人已撤回事项"
    item.status = "canceled"
    item.completed_at = now
    item.metadata_json = {
        **(item.metadata_json or {}),
        "canceled_by_id": user.id,
        "canceled_by_name": user.real_name,
        "canceled_at": now.isoformat(),
        "cancel_reason": note,
    }
    db.add(CollaborationEvent(item_id=item.id, actor_id=user.id, action="canceled", detail=note))
    followers = (await db.execute(select(CollaborationFollower).where(
        CollaborationFollower.item_id == item.id
    ))).scalars().all()
    await _notify(
        db, [item.recipient_id, *[row.user_id for row in followers]],
        title="协作事项已撤回",
        body=f"{user.real_name} 撤回了「{item.title}」",
        event_type="collaboration_canceled", item=item, actor_id=user.id,
    )
    await db.commit()
    return {"detail": "事项已撤回", "status": item.status, "idempotent": False}


@router.post("/{item_id}/remind")
async def remind_item(
    item_id: int,
    req: RemindRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    item = await _locked_item(db, item_id, project_id)
    if not item or item.sender_id != user.id:
        raise HTTPException(404, "协作事项不存在或无权催办")
    if item.mode != "action" or item.status != "pending":
        raise HTTPException(409, "仅待处理的协作事项可以催办")
    can_remind, next_at = _remind_state(item)
    if not can_remind:
        friendly = next_at[11:16] if next_at else "稍后"
        raise HTTPException(429, f"催办过于频繁，请在 {friendly} 后再试")
    now = datetime.now()
    note = req.note.strip() or "请尽快处理"
    item.metadata_json = {
        **(item.metadata_json or {}),
        "last_reminded_at": now.isoformat(),
        "last_reminder_note": note,
    }
    db.add(CollaborationEvent(item_id=item.id, actor_id=user.id, action="reminded", detail=note))
    await _notify(
        db, [item.recipient_id],
        title="协作事项催办",
        body=f"{user.real_name} 催办了「{item.title}」：{note}",
        event_type="collaboration_reminded", item=item, actor_id=user.id,
    )
    await db.commit()
    return {
        "detail": "已提醒处理人",
        "next_available_at": (now + timedelta(seconds=REMIND_COOLDOWN_SECONDS)).isoformat(),
    }


@router.post("/{item_id}/transfer")
async def transfer_item(
    item_id: int,
    req: TransferRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    item = await _locked_item(db, item_id, project_id)
    if not item or item.recipient_id != user.id:
        raise HTTPException(404, "协作事项不存在或无权转派")
    if item.mode != "action" or item.action_type != "process":
        raise HTTPException(409, "仅待处理的人工事项可以转派")
    if req.recipient_id == user.id:
        raise HTTPException(400, "转派对象不能是自己")
    cc_ids = list(dict.fromkeys(int(value) for value in req.cc_recipient_ids))
    now = datetime.now()
    if req.client_request_id:
        digest = hashlib.sha256(req.client_request_id.strip().encode("utf-8")).hexdigest()
        replacement_key = f"manual:transfer:{item.id}:request:{digest}"
    else:
        replacement_key = f"manual:transfer:{item.id}:{now.timestamp()}"
    existing = (await db.execute(
        select(CollaborationItem).where(CollaborationItem.source_key == replacement_key)
    )).scalar_one_or_none()
    if existing is not None:
        if existing.recipient_id != req.recipient_id:
            raise HTTPException(409, "同一转派请求编号对应了不同接收人")
        stored_cc_ids = set((existing.metadata_json or {}).get("transfer_cc_ids") or [])
        if stored_cc_ids != set(cc_ids):
            raise HTTPException(409, "同一转派请求编号对应了不同抄送人")
        return {
            "detail": "事项已转派",
            "status": existing.status,
            "new_item_id": existing.id,
            "idempotent": True,
        }
    if item.status != "pending":
        raise HTTPException(409, "仅待处理的人工事项可以转派")
    member = await db.scalar(select(ProjectMember.id).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == req.recipient_id,
        ProjectMember.left_at.is_(None),
    ))
    recipient = await db.get(User, req.recipient_id)
    if not member or not recipient or not recipient.is_active:
        raise HTTPException(400, "转派对象必须是当前项目的有效成员")
    if req.recipient_id in cc_ids:
        raise HTTPException(400, "主处理人不能同时作为抄送人")
    if user.id in cc_ids:
        raise HTTPException(400, "不能抄送给自己")
    if cc_ids:
        valid_cc = set((await db.execute(
            select(ProjectMember.user_id)
            .join(User, User.id == ProjectMember.user_id)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id.in_(cc_ids),
                ProjectMember.left_at.is_(None),
                User.is_active.is_(True),
            )
        )).scalars().all())
        if valid_cc != set(cc_ids):
            raise HTTPException(400, "所有抄送人都必须是当前项目的有效成员")

    item.status = "transferred"
    item.completed_at = now
    db.add(CollaborationEvent(
        item_id=item.id, actor_id=user.id, action="transferred",
        detail=f"转派给 {recipient.real_name}（用户 {recipient.id}）" + (f"：{req.detail.strip()}" if req.detail.strip() else ""),
    ))
    inherited = {
        key: value for key, value in (item.metadata_json or {}).items()
        if key not in {
            "last_reminded_at", "last_reminder_note", "resolution",
            "resolved_by_id", "resolved_by_name", "resolved_at",
            "canceled_by_id", "canceled_by_name", "canceled_at", "cancel_reason",
        }
    }
    replacement = CollaborationItem(
        project_id=item.project_id,
        sender_id=user.id,
        recipient_id=recipient.id,
        mode="action",
        object_type=item.object_type,
        object_id=item.object_id,
        action_type="process",
        status="pending",
        title=item.title,
        description=item.description,
        metadata_json={**inherited, "transferred_from": item.id, "transfer_cc_ids": cc_ids},
        source_key=replacement_key,
    )
    db.add(replacement)
    await db.flush()
    db.add(CollaborationEvent(
        item_id=replacement.id, actor_id=user.id, action="created",
        detail=f"由 {user.real_name} 转派" + (f"：{req.detail.strip()}" if req.detail.strip() else ""),
    ))
    copied_followers: list[int] = []
    existing_follower_ids: set[int] = set()
    for follower in (await db.execute(select(CollaborationFollower).where(
        CollaborationFollower.item_id == item.id
    ))).scalars().all():
        if follower.user_id == recipient.id:
            continue
        existing_follower_ids.add(follower.user_id)
        db.add(CollaborationFollower(item_id=replacement.id, user_id=follower.user_id))
        db.add(CollaborationEvent(
            item_id=replacement.id, actor_id=user.id, action="cc_added", detail=str(follower.user_id)
        ))
        copied_followers.append(follower.user_id)
    for cc_id in cc_ids:
        if cc_id in existing_follower_ids:
            continue
        db.add(CollaborationFollower(item_id=replacement.id, user_id=cc_id))
        db.add(CollaborationEvent(
            item_id=replacement.id, actor_id=user.id, action="cc_added", detail=str(cc_id)
        ))
        copied_followers.append(cc_id)
    await _notify(
        db, [recipient.id, *copied_followers],
        title="你收到一个转派事项",
        body=f"{user.real_name} 将「{item.title}」转派给你",
        event_type="collaboration_transferred", item=replacement, actor_id=user.id,
    )
    await db.commit()
    return {
        "detail": "事项已转派",
        "status": item.status,
        "new_item_id": replacement.id,
        "idempotent": False,
    }
