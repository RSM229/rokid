import asyncio
import json
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import app  # noqa: F401
from app.models.collaboration import CollaborationEvent, CollaborationFollower, CollaborationItem
from app.models.document import Document
from app.models.notification import Notification
from app.models.project_member import ProjectMember
from app.models.task_assignment import TaskAssignment, TaskStatus
from app.models.user import Project, User
from app.routers.collaboration import (
    CreateCollaborationRequest,
    ResolveRequest,
    TransferRequest,
    cancel_item,
    create_item,
    detail,
    inbox,
    resolve,
    transfer_item,
)
from app.routers.task import get_my_tasks
from app.services.collaboration import complete_action_items, upsert_action_items


def test_collaboration_http_routes_are_registered():
    paths = set(app.openapi()["paths"])
    assert "/api/collaboration/inbox" in paths
    assert "/api/collaboration/members" in paths
    assert "/api/collaboration/{item_id}" in paths
    assert "/api/kb/project-archive" in paths
    assert "/api/document/{doc_id}/archive-sync" in paths


def test_collaboration_inbox_permissions_read_state_and_transitions():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project_a = Project(name="甲项目", code="COLLAB-A")
            project_b = Project(name="乙项目", code="COLLAB-B")
            db.add_all([project_a, project_b])
            await db.flush()
            sender = User(username="sender", real_name="发起人", role="inspector",
                          password_hash="unused", project_id=project_a.id, is_active=True)
            recipient = User(username="recipient", real_name="处理人", role="inspector",
                             password_hash="unused", project_id=project_a.id, is_active=True)
            follower = User(username="follower", real_name="抄送人", role="inspector",
                            password_hash="unused", project_id=project_a.id, is_active=True)
            outsider = User(username="outsider", real_name="外项目用户", role="inspector",
                            password_hash="unused", project_id=project_b.id, is_active=True)
            db.add_all([sender, recipient, follower, outsider])
            await db.flush()
            for user, project in ((sender, project_a), (recipient, project_a),
                                  (follower, project_a), (outsider, project_b)):
                db.add(ProjectMember(project_id=project.id, user_id=user.id, role="inspector"))
            doc = Document(doc_type="巡视记录", title="现场巡视", content={},
                           project_id=project_a.id, creator_id=sender.id)
            db.add(doc)
            await db.commit()

            created = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                cc_recipient_ids=[follower.id], title="检查现场问题",
                description="请核实并反馈",
            ), sender, db)
            item_id = created["item"]["id"]
            assert created['item']['mode_label'] == '我发出的'
            assert (await inbox("action", recipient, db))["pending_count"] == 1
            assert (await inbox("action", follower, db))["pending_count"] == 0
            assert len((await inbox("cc", follower, db))["items"]) == 1
            assert (await inbox('cc', follower, db))['items'][0]['mode_label'] == '抄送跟踪'

            await detail(item_id, recipient, db)
            assert (await inbox("action", recipient, db))["pending_count"] == 1
            await detail(item_id, follower, db)
            follower_row = (await db.execute(select(CollaborationFollower).where(
                CollaborationFollower.item_id == item_id,
                CollaborationFollower.user_id == follower.id,
            ))).scalar_one()
            assert follower_row.read_at is not None
            assert (await inbox("action", recipient, db))["pending_count"] == 1

            with pytest.raises(HTTPException) as cross_project:
                await create_item(CreateCollaborationRequest(
                    object_type="document", object_id=doc.id,
                    recipient_id=outsider.id, title="跨项目指派",
                ), sender, db)
            assert cross_project.value.status_code == 400
            with pytest.raises(HTTPException) as private_item:
                await detail(item_id, outsider, db)
            assert private_item.value.status_code == 404

            with pytest.raises(HTTPException) as cc_cannot_resolve:
                await resolve(item_id, ResolveRequest(detail="已看"), follower, db)
            assert cc_cannot_resolve.value.status_code == 404
            await resolve(item_id, ResolveRequest(detail="已核实"), recipient, db)
            assert (await inbox("action", recipient, db))["pending_count"] == 0
            done = await inbox("done", recipient, db)
            assert [row["id"] for row in done["items"]] == [item_id]
            assert done["items"][0]["status"] == "completed"
            assert done["items"][0]["mode_label"] == "已完成"
            assert done["items"][0]["completed_at"]
            assert (await inbox("done", follower, db))["items"] == []
            note = (await db.execute(select(Notification).where(
                Notification.user_id == sender.id,
                Notification.event_type == "collaboration_completed",
            ))).scalars().all()
            assert len(note) == 1
            payload = json.loads(note[0].payload)
            assert payload["item_id"] == item_id
            assert payload["object_type"] == "document"

            # Workflow approval items cannot be completed by the generic resolver.
            approval = CollaborationItem(
                project_id=project_a.id, sender_id=sender.id, recipient_id=recipient.id,
                mode="action", object_type="document", object_id=doc.id,
                action_type="approve", status="pending", title="待审批",
                metadata_json={}, source_key="test:approval:1",
            )
            db.add(approval)
            await db.commit()
            with pytest.raises(HTTPException) as generic_approval:
                await resolve(approval.id, ResolveRequest(), recipient, db)
            assert generic_approval.value.status_code == 400
            assert await complete_action_items(
                db, object_type="document", object_id=doc.id,
                action_type="approve", actor_id=recipient.id, detail="实际审批通过",
            ) == 1
            await db.commit()
            assert (await inbox("action", recipient, db))["pending_count"] == 0

            signoff = await create_item(CreateCollaborationRequest(
                object_type='document', object_id=doc.id, recipient_id=recipient.id,
                action_type='signoff', signoff_slot='inspector', title='现场签认',
            ), sender, db)
            with pytest.raises(HTTPException) as signoff_resolver:
                await resolve(signoff['item']['id'], ResolveRequest(), recipient, db)
            assert signoff_resolver.value.status_code == 400
            assert await complete_action_items(
                db, object_type='document', object_id=doc.id,
                action_type='signoff:inspector', actor_id=recipient.id,
            ) == 1
            await db.commit()
            assert (await inbox('action', recipient, db))['pending_count'] == 0

            cancellable = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="可撤回事项",
            ), sender, db)
            await cancel_item(cancellable["item"]["id"], ResolveRequest(), sender, db)
            canceled = await db.get(CollaborationItem, cancellable["item"]["id"])
            assert canceled.status == "canceled"
            assert (await inbox("action", recipient, db))["pending_count"] == 0
            done = await inbox("done", recipient, db)
            canceled_row = next(row for row in done["items"] if row["id"] == cancellable["item"]["id"])
            assert canceled_row["status"] == "canceled"
            assert canceled_row["mode_label"] == "已撤回"

            transferable = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                cc_recipient_ids=[follower.id], title="转派事项",
            ), sender, db)
            old_id = transferable["item"]["id"]
            await transfer_item(old_id, TransferRequest(
                recipient_id=follower.id, detail="由现场负责人跟进",
            ), recipient, db)
            replacement = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.metadata_json["transferred_from"].as_integer() == old_id
            ))).scalar_one()
            assert (await db.get(CollaborationItem, old_id)).status == "transferred"
            done = await inbox("done", recipient, db)
            transferred_row = next(row for row in done["items"] if row["id"] == old_id)
            assert transferred_row["status"] == "transferred"
            assert transferred_row["mode_label"] == "已转派"
            assert replacement.recipient_id == follower.id and replacement.status == "pending"
            duplicate_cc = await db.scalar(select(CollaborationFollower.id).where(
                CollaborationFollower.item_id == replacement.id,
                CollaborationFollower.user_id == follower.id,
            ))
            assert duplicate_cc is None
            assert (await db.scalar(select(CollaborationFollower.id).where(
                CollaborationFollower.item_id == replacement.id,
            ))) is None
            assert (await inbox("action", follower, db))["pending_count"] == 1
            events = (await db.execute(select(CollaborationEvent).where(
                CollaborationEvent.item_id == old_id
            ))).scalars().all()
            assert any(event.action == "transferred" for event in events)

            db.add_all([
                TaskAssignment(title="待接单任务", status=TaskStatus.PENDING.value,
                               assigner_id=sender.id, assignee_id=recipient.id, project_id=project_a.id),
                TaskAssignment(title="已接单任务", status=TaskStatus.ACCEPTED.value,
                               assigner_id=sender.id, assignee_id=recipient.id, project_id=project_a.id),
                TaskAssignment(title="进行中任务", status=TaskStatus.IN_PROGRESS.value,
                               assigner_id=sender.id, assignee_id=recipient.id, project_id=project_a.id),
                TaskAssignment(title="已完成任务", status=TaskStatus.COMPLETED.value,
                               assigner_id=sender.id, assignee_id=recipient.id, project_id=project_a.id),
            ])
            await db.commit()
            assert (await inbox("action", recipient, db))["pending_count"] == 3
            # Reading collaboration details does not clear actionable work; terminal tasks are excluded.
            assert (await inbox("action", recipient, db))["pending_count"] == 3
            open_tasks = await get_my_tasks("待处理", False, recipient, db)
            assert {task["status"] for task in open_tasks["tasks"]} == {
                TaskStatus.PENDING.value,
                TaskStatus.ACCEPTED.value,
                TaskStatus.IN_PROGRESS.value,
            }
        await engine.dispose()

    asyncio.run(scenario())


def test_collaboration_idempotency_reopens_responsibilities_and_replaces_approvers():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="协作项目", code="COLLAB-C")
            db.add(project)
            await db.flush()
            sender = User(username="sender-2", real_name="发起人", role="inspector",
                          password_hash="unused", project_id=project.id, is_active=True)
            first = User(username="first-2", real_name="处理人甲", role="inspector",
                         password_hash="unused", project_id=project.id, is_active=True)
            second = User(username="second-2", real_name="处理人乙", role="inspector",
                          password_hash="unused", project_id=project.id, is_active=True)
            stranger = User(username="stranger-2", real_name="非成员", role="inspector",
                            password_hash="unused", project_id=project.id, is_active=True)
            db.add_all([sender, first, second, stranger])
            await db.flush()
            for member in (sender, first, second):
                db.add(ProjectMember(project_id=project.id, user_id=member.id, role="inspector"))
            doc = Document(doc_type="巡视记录", title="协作文书", content={},
                           project_id=project.id, creator_id=sender.id)
            db.add(doc)
            await db.commit()

            payload = CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=first.id,
                title="请核实", description="现场核实后反馈", client_request_id="stable-1",
            )
            created = await create_item(payload, sender, db)
            repeated = await create_item(payload, sender, db)
            assert created["idempotent"] is False
            assert repeated["idempotent"] is True
            assert repeated["item"]["id"] == created["item"]["id"]
            assert len((await db.execute(select(CollaborationItem))).scalars().all()) == 1

            with pytest.raises(HTTPException) as changed:
                await create_item(payload.model_copy(update={"title": "另一个事项"}), sender, db)
            assert changed.value.status_code == 409

            with pytest.raises(HTTPException) as non_member:
                await inbox("action", stranger, db)
            assert non_member.value.status_code == 403
            with pytest.raises(HTTPException) as sender_not_recipient:
                await resolve(created["item"]["id"], ResolveRequest(), sender, db)
            assert sender_not_recipient.value.status_code == 404

            await upsert_action_items(
                db, project_id=project.id, sender_id=sender.id,
                recipient_ids=[first.id, second.id], object_type="document",
                object_id=doc.id, action_type="approve", title="待审批",
                source_prefix="workflow", retire_missing=True,
            )
            await db.commit()
            first_item = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.object_type == "document",
                CollaborationItem.object_id == doc.id,
                CollaborationItem.action_type == "approve",
                CollaborationItem.recipient_id == first.id,
            ))).scalar_one()
            second_item = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.object_type == "document",
                CollaborationItem.object_id == doc.id,
                CollaborationItem.action_type == "approve",
                CollaborationItem.recipient_id == second.id,
            ))).scalar_one()
            first_item.read_at = datetime.now()
            first_item.status = "completed"
            first_item.completed_at = datetime.now()
            await db.commit()

            await upsert_action_items(
                db, project_id=project.id, sender_id=sender.id,
                recipient_ids=[first.id], object_type="document",
                object_id=doc.id, action_type="approve", title="待审批（更新）",
                source_prefix="workflow", retire_missing=True,
            )
            await db.commit()
            await db.refresh(first_item)
            await db.refresh(second_item)
            assert first_item.status == "pending"
            assert first_item.read_at is None
            assert first_item.title == "待审批（更新）"
            assert second_item.status == "canceled"
            events = (await db.execute(select(CollaborationEvent).where(
                CollaborationEvent.item_id == second_item.id
            ))).scalars().all()
            assert any(event.action == "canceled" for event in events)
            # The manual process item plus the renewed approval responsibility.
            assert (await inbox("action", first, db))["pending_count"] == 2
        await engine.dispose()

    asyncio.run(scenario())
