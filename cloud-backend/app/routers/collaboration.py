"""Unified project collaboration inbox for the mini program."""
import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.collaboration import CollaborationEvent, CollaborationFollower, CollaborationItem
from app.models.document import Document
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
    detail: str = Field(default="", max_length=2000)


ACTION_LABELS = {
    "created": "创建事项",
    "reopened": "重新进入待处理",
    "read": "查看详情",
    "resolved": "标记完成",
    "completed": "业务处理完成",
    "canceled": "撤回/失效",
    "transferred": "转派",
    "cc_added": "新增抄送",
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
    follower_ids = [row.user_id for row in followers]
    follower_users = (await db.execute(
        select(User).where(User.id.in_(follower_ids or [-1]))
    )).scalars().all()
    read_at = item.read_at
    if viewer_id and viewer_id != item.recipient_id:
        read_at = next((row.read_at for row in followers if row.user_id == viewer_id), None)
    return {
        "id": item.id,
        "project_id": item.project_id,
        "sender_id": item.sender_id,
        "sender_name": sender.real_name if sender else "未知",
        "recipient_id": item.recipient_id,
        "recipient_name": recipient.real_name if recipient else "未知",
        "cc_recipient_names": [row.real_name for row in follower_users],
        "cc_names_text": "、".join(row.real_name for row in follower_users),
        "mode": item.mode,
        "mode_label": _mode_label(item, viewer_id),
        "object_type": item.object_type,
        "object_id": item.object_id,
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
        "metadata": item.metadata_json or {},
        "read_at": read_at.isoformat() if read_at else None,
        "can_cancel": bool(
            viewer_id == item.sender_id and item.mode == "action"
            and item.action_type == "process" and item.status == "pending"
            and item.source_key.startswith("manual:")
        ),
        "can_transfer": bool(
            viewer_id == item.recipient_id and item.mode == "action"
            and item.action_type == "process" and item.status == "pending"
        ),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


@router.get("/inbox")
async def inbox(
    view: str = Query("action", pattern="^(action|cc|sent|done)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    query = select(CollaborationItem).where(CollaborationItem.project_id == project_id)
    if view == "action":
        query = query.where(
            CollaborationItem.recipient_id == user.id,
            CollaborationItem.mode == "action",
            CollaborationItem.status == "pending",
            _existing_object_clause(),
        )
    elif view == "cc":
        follows = exists(select(CollaborationFollower.id).where(
            CollaborationFollower.item_id == CollaborationItem.id,
            CollaborationFollower.user_id == user.id,
        ))
        query = query.where(or_(
            and_(CollaborationItem.recipient_id == user.id, CollaborationItem.mode == "cc"),
            follows,
        ))
    elif view == "done":
        query = query.where(
            CollaborationItem.recipient_id == user.id,
            CollaborationItem.mode == "action",
            CollaborationItem.status.in_(["completed", "canceled", "transferred"]),
        )
    else:
        query = query.where(CollaborationItem.sender_id == user.id)
    if view == "done":
        query = query.order_by(
            CollaborationItem.completed_at.desc(), CollaborationItem.id.desc()
        )
    else:
        query = query.order_by(CollaborationItem.created_at.desc())
    rows = (await db.execute(query.limit(200))).scalars().all()
    pending = await db.scalar(
        select(func.count(CollaborationItem.id)).where(
            CollaborationItem.project_id == project_id,
            CollaborationItem.recipient_id == user.id,
            CollaborationItem.mode == "action",
            CollaborationItem.status == "pending",
            _existing_object_clause(),
        )
    )
    task_pending = await db.scalar(
        select(func.count(TaskAssignment.id)).where(
            TaskAssignment.project_id == project_id,
            TaskAssignment.assignee_id == user.id,
            TaskAssignment.status.in_([
                TaskStatus.PENDING.value,
                TaskStatus.ACCEPTED.value,
                TaskStatus.IN_PROGRESS.value,
            ]),
        )
    )
    task_query = select(TaskAssignment).where(
        TaskAssignment.project_id == project_id,
        TaskAssignment.assignee_id == user.id,
    )
    tasks = (await db.execute(task_query.order_by(TaskAssignment.created_at.desc()).limit(100))).scalars().all()
    task_senders = {
        row.id: row.real_name
        for row in (await db.execute(select(User).where(User.id.in_([t.assigner_id for t in tasks] or [-1])))).scalars().all()
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
        "pending_count": int(pending or 0) + int(task_pending or 0),
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
    metadata = {"deadline": req.deadline} if req.deadline else {}
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
            "title": req.title.strip(),
            "description": req.description.strip(),
            "deadline": req.deadline,
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
        title=req.title.strip(),
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
    item = await db.get(CollaborationItem, item_id)
    if not item or item.project_id != project_id or item.recipient_id != user.id:
        raise HTTPException(404, "协作事项不存在或无权处理")
    if item.mode != "action" or item.action_type != "process":
        raise HTTPException(400, "该事项必须通过对应业务动作处理")
    if item.status != "pending":
        raise HTTPException(409, "该事项已经处理")
    item.status = "completed"
    item.completed_at = datetime.now()
    db.add(CollaborationEvent(item_id=item.id, actor_id=user.id, action="resolved", detail=req.detail.strip() or None))
    from app.models.notification import Notification
    db.add(Notification(
        user_id=item.sender_id,
        title="协作事项已完成",
        body=f"{user.real_name} 已完成「{item.title}」",
        event_type="collaboration_completed",
        payload=json.dumps({
            "item_id": item.id,
            "object_type": item.object_type,
            "object_id": item.object_id,
            "status": item.status,
        }, ensure_ascii=False),
        ts=datetime.now().timestamp(),
    ))
    await db.commit()
    return {"detail": "处理事项已完成", "status": item.status}


@router.post("/{item_id}/cancel")
async def cancel_item(
    item_id: int,
    req: ResolveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    item = await db.get(CollaborationItem, item_id)
    if not item or item.project_id != project_id or item.sender_id != user.id:
        raise HTTPException(404, "协作事项不存在或无权撤回")
    if item.mode != "action" or item.action_type != "process" or not item.source_key.startswith("manual:"):
        raise HTTPException(400, "仅发起的人工处理事项可以撤回")
    if item.status != "pending":
        raise HTTPException(409, "该事项已处理，不能撤回")
    item.status = "canceled"
    item.completed_at = datetime.now()
    db.add(CollaborationEvent(
        item_id=item.id, actor_id=user.id, action="canceled", detail=req.detail.strip() or None
    ))
    await db.commit()
    return {"detail": "事项已撤回", "status": item.status}


@router.post("/{item_id}/transfer")
async def transfer_item(
    item_id: int,
    req: TransferRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    await _require_project_member(db, user, project_id)
    item = await db.get(CollaborationItem, item_id)
    if not item or item.project_id != project_id or item.recipient_id != user.id:
        raise HTTPException(404, "协作事项不存在或无权转派")
    if item.mode != "action" or item.action_type != "process" or item.status != "pending":
        raise HTTPException(409, "仅待处理的人工事项可以转派")
    if req.recipient_id == user.id:
        raise HTTPException(400, "转派对象不能是自己")
    member = await db.scalar(select(ProjectMember.id).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == req.recipient_id,
        ProjectMember.left_at.is_(None),
    ))
    recipient = await db.get(User, req.recipient_id)
    if not member or not recipient or not recipient.is_active:
        raise HTTPException(400, "转派对象必须是当前项目的有效成员")

    now = datetime.now()
    item.status = "transferred"
    item.completed_at = now
    db.add(CollaborationEvent(
        item_id=item.id, actor_id=user.id, action="transferred",
        detail=f"转派给 {recipient.real_name}（用户 {recipient.id}）" + (f"：{req.detail.strip()}" if req.detail.strip() else ""),
    ))
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
        metadata_json={**(item.metadata_json or {}), "transferred_from": item.id},
        source_key=f"manual:transfer:{item.id}:{now.timestamp()}",
    )
    db.add(replacement)
    await db.flush()
    db.add(CollaborationEvent(
        item_id=replacement.id, actor_id=user.id, action="created",
        detail=f"由 {user.real_name} 转派" + (f"：{req.detail.strip()}" if req.detail.strip() else ""),
    ))
    for follower in (await db.execute(select(CollaborationFollower).where(
        CollaborationFollower.item_id == item.id
    ))).scalars().all():
        if follower.user_id == recipient.id:
            continue
        db.add(CollaborationFollower(item_id=replacement.id, user_id=follower.user_id))
        db.add(CollaborationEvent(
            item_id=replacement.id, actor_id=user.id, action="cc_added", detail=str(follower.user_id)
        ))
    await db.commit()
    return {"detail": "事项已转派", "status": item.status, "new_item_id": replacement.id}
