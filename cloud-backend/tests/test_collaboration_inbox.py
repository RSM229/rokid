import asyncio
import json
from datetime import date, datetime, timedelta

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
    RemindRequest,
    ResolveRequest,
    TransferRequest,
    cancel_item,
    create_item,
    detail,
    inbox,
    remind_item,
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


def test_collaboration_inbox_counts_filters_deadlines_and_viewer_roles():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="筛选项目", code="COLLAB-FILTER")
            db.add(project)
            await db.flush()
            sender = User(username="filter-sender", real_name="发起人", role="inspector",
                          password_hash="unused", project_id=project.id, is_active=True)
            recipient = User(username="filter-recipient", real_name="处理人", role="inspector",
                             password_hash="unused", project_id=project.id, is_active=True)
            follower = User(username="filter-follower", real_name="抄送人", role="inspector",
                            password_hash="unused", project_id=project.id, is_active=True)
            db.add_all([sender, recipient, follower])
            await db.flush()
            for member in (sender, recipient, follower):
                db.add(ProjectMember(project_id=project.id, user_id=member.id, role="inspector"))
            doc = Document(doc_type="巡视记录", title="筛选测试文书", content={},
                           project_id=project.id, creator_id=sender.id)
            db.add(doc)
            await db.commit()

            overdue = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="逾期检查", description="现场复核",
                deadline=(date.today() - timedelta(days=2)).isoformat(),
            ), sender, db)
            upcoming = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="例行检查", description="按计划复核",
                deadline=(date.today() + timedelta(days=2)).isoformat(),
            ), sender, db)
            cc_item = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                cc_recipient_ids=[follower.id], mode="cc", title="跟踪现场进展",
            ), sender, db)
            canceled = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="将被撤回",
            ), sender, db)
            resolved_item = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="将被完成",
            ), sender, db)
            await resolve(resolved_item["item"]["id"], ResolveRequest(detail="已复核"), recipient, db)
            await cancel_item(canceled["item"]["id"], ResolveRequest(detail="计划调整"), sender, db)

            action = await inbox("action", recipient, db)
            assert action["counts"] == {
                "action": 2,
                "collaboration_action": 2,
                "task_pending": 0,
                "cc": 1,
                "sent": 0,
                "done": 2,
                "tasks": 0,
            }
            assert action["pending_count"] == 2
            upcoming_row = next(row for row in action["items"] if row["id"] == upcoming["item"]["id"])
            assert upcoming_row["deadline_state"] == "normal"
            assert upcoming_row["deadline_label"].startswith("截止 ")
            assert upcoming_row["viewer_role"] == "recipient"
            assert upcoming_row["can_resolve"] is True
            assert upcoming_row["can_remind"] is False

            done = await inbox("done", recipient, db, limit=1)
            assert len(done["items"]) == 1
            assert done["has_more"] is True
            assert done["next_offset"] == 1
            assert done["items"][0]["status"] in {"completed", "canceled"}
            second_page = await inbox("done", recipient, db, limit=1, offset=1)
            assert len(second_page["items"]) == 1 and second_page["has_more"] is False

            completed = await inbox("sent", sender, db, status="completed")
            assert [row["id"] for row in completed["items"]] == [resolved_item["item"]["id"]]
            assert completed["items"][0]["resolution"] == "已复核"
            assert completed["items"][0]["completed_by_name"] == "处理人"
            canceled_rows = await inbox("sent", sender, db, status="canceled")
            assert [row["id"] for row in canceled_rows["items"]] == [canceled["item"]["id"]]

            keyword = await inbox("sent", sender, db, keyword="逾期")
            assert [row["id"] for row in keyword["items"]] == [overdue["item"]["id"]]
            overdue_row = keyword["items"][0]
            assert overdue_row["deadline_state"] == "overdue"
            assert overdue_row["deadline_label"] == "已逾期 2 天"
            assert overdue_row["viewer_role"] == "sender"
            assert overdue_row["source"] == "人工发起"

            cc_as_recipient = await inbox("cc", recipient, db)
            assert [row["id"] for row in cc_as_recipient["items"]] == [cc_item["item"]["id"]]
            cc_as_follower = await inbox("cc", follower, db)
            assert [row["id"] for row in cc_as_follower["items"]] == [cc_item["item"]["id"]]
            assert cc_as_follower["items"][0]["viewer_role"] == "follower"
            assert cc_as_follower["items"][0]["is_read"] is False
            await detail(cc_item["item"]["id"], follower, db)
            cc_after_read = await inbox("cc", follower, db)
            assert cc_after_read["items"][0]["is_read"] is True

        await engine.dispose()

    asyncio.run(scenario())


def test_collaboration_notifications_idempotency_reminders_and_permissions():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="通知项目", code="COLLAB-NOTICE")
            db.add(project)
            await db.flush()
            sender = User(username="notice-sender", real_name="发起人", role="inspector",
                          password_hash="unused", project_id=project.id, is_active=True)
            recipient = User(username="notice-recipient", real_name="处理人", role="inspector",
                             password_hash="unused", project_id=project.id, is_active=True)
            follower = User(username="notice-follower", real_name="抄送人", role="inspector",
                            password_hash="unused", project_id=project.id, is_active=True)
            newcomer = User(username="notice-newcomer", real_name="接手人", role="inspector",
                            password_hash="unused", project_id=project.id, is_active=True)
            db.add_all([sender, recipient, follower, newcomer])
            await db.flush()
            for member in (sender, recipient, follower, newcomer):
                db.add(ProjectMember(project_id=project.id, user_id=member.id, role="inspector"))
            doc = Document(doc_type="巡视记录", title="通知测试文书", content={},
                           project_id=project.id, creator_id=sender.id)
            db.add(doc)
            await db.commit()

            created = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                cc_recipient_ids=[follower.id], title="需要处理",
            ), sender, db)
            item_id = created["item"]["id"]
            recipient_notices = (await db.execute(select(Notification).where(
                Notification.user_id == recipient.id,
                Notification.event_type == "collaboration_assigned",
            ))).scalars().all()
            follower_notices = (await db.execute(select(Notification).where(
                Notification.user_id == follower.id,
                Notification.event_type == "collaboration_cc",
            ))).scalars().all()
            assert len(recipient_notices) == 1 and len(follower_notices) == 1
            payload = json.loads(recipient_notices[0].payload)
            assert payload["item_id"] == item_id
            assert payload["route"].endswith(f"item_id={item_id}")

            first = await resolve(item_id, ResolveRequest(detail="处理完毕"), recipient, db)
            second = await resolve(item_id, ResolveRequest(detail="处理完毕"), recipient, db)
            assert first["idempotent"] is False and second["idempotent"] is True
            sender_completed = (await db.execute(select(Notification).where(
                Notification.user_id == sender.id,
                Notification.event_type == "collaboration_completed",
            ))).scalars().all()
            follower_completed = (await db.execute(select(Notification).where(
                Notification.user_id == follower.id,
                Notification.event_type == "collaboration_completed",
            ))).scalars().all()
            assert len(sender_completed) == 1 and len(follower_completed) == 1
            events = (await db.execute(select(CollaborationEvent).where(
                CollaborationEvent.item_id == item_id
            ))).scalars().all()
            assert sum(1 for event in events if event.action == "resolved") == 1

            cancellable = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="可撤回事项",
            ), sender, db)
            cancel_id = cancellable["item"]["id"]
            await cancel_item(cancel_id, ResolveRequest(detail="取消"), sender, db)
            repeated_cancel = await cancel_item(cancel_id, ResolveRequest(detail="取消"), sender, db)
            assert repeated_cancel["idempotent"] is True
            canceled_notices = (await db.execute(select(Notification).where(
                Notification.user_id == recipient.id,
                Notification.event_type == "collaboration_canceled",
            ))).scalars().all()
            assert len(canceled_notices) == 1

            transferable = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="需要转派",
            ), sender, db)
            transfer_id = transferable["item"]["id"]
            transfer_payload = TransferRequest(
                recipient_id=newcomer.id, cc_recipient_ids=[follower.id],
                detail="请接手", client_request_id="transfer-stable-1",
            )
            transferred = await transfer_item(transfer_id, transfer_payload, recipient, db)
            repeated_transfer = await transfer_item(transfer_id, transfer_payload, recipient, db)
            assert transferred["idempotent"] is False
            assert repeated_transfer["idempotent"] is True
            assert repeated_transfer["new_item_id"] == transferred["new_item_id"]
            replacements = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.source_key.like("manual:transfer:%")
            ))).scalars().all()
            assert len(replacements) == 1
            replacement = replacements[0]
            replacement_followers = (await db.execute(select(CollaborationFollower).where(
                CollaborationFollower.item_id == replacement.id
            ))).scalars().all()
            assert [row.user_id for row in replacement_followers] == [follower.id]
            transferred_notices = (await db.execute(select(Notification).where(
                Notification.user_id == newcomer.id,
                Notification.event_type == "collaboration_transferred",
            ))).scalars().all()
            assert len(transferred_notices) == 1
            transferred_cc_notices = (await db.execute(select(Notification).where(
                Notification.user_id == follower.id,
                Notification.event_type == "collaboration_transferred",
            ))).scalars().all()
            assert len(transferred_cc_notices) == 1

            remindable = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="需要催办",
            ), sender, db)
            remind_id = remindable["item"]["id"]
            reminded = await remind_item(remind_id, RemindRequest(note="请今天反馈"), sender, db)
            assert reminded["next_available_at"]
            remind_notices = (await db.execute(select(Notification).where(
                Notification.user_id == recipient.id,
                Notification.event_type == "collaboration_reminded",
            ))).scalars().all()
            assert len(remind_notices) == 1
            with pytest.raises(HTTPException) as cooldown:
                await remind_item(remind_id, RemindRequest(note="再次"), sender, db)
            assert cooldown.value.status_code == 429
            row = await db.get(CollaborationItem, remind_id)
            row.metadata_json = {
                **(row.metadata_json or {}),
                "last_reminded_at": (datetime.now() - timedelta(minutes=11)).isoformat(),
            }
            await db.commit()
            again = await remind_item(remind_id, RemindRequest(note="再次"), sender, db)
            assert again["detail"] == "已提醒处理人"

            with pytest.raises(HTTPException) as sender_resolve:
                await resolve(remind_id, ResolveRequest(), sender, db)
            assert sender_resolve.value.status_code == 404
            with pytest.raises(HTTPException) as recipient_remind:
                await remind_item(remind_id, RemindRequest(), recipient, db)
            assert recipient_remind.value.status_code == 404
            with pytest.raises(HTTPException) as follower_cancel:
                await cancel_item(remind_id, ResolveRequest(), follower, db)
            assert follower_cancel.value.status_code == 404
            with pytest.raises(HTTPException) as sender_transfer:
                await transfer_item(remind_id, TransferRequest(recipient_id=newcomer.id), sender, db)
            assert sender_transfer.value.status_code == 404

        await engine.dispose()

    asyncio.run(scenario())


def test_collaboration_create_validates_deadline_and_title():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="校验项目", code="COLLAB-VALID")
            db.add(project)
            await db.flush()
            sender = User(username="valid-sender", real_name="发起人", role="inspector",
                          password_hash="unused", project_id=project.id, is_active=True)
            recipient = User(username="valid-recipient", real_name="处理人", role="inspector",
                             password_hash="unused", project_id=project.id, is_active=True)
            db.add_all([sender, recipient])
            await db.flush()
            db.add_all([
                ProjectMember(project_id=project.id, user_id=sender.id, role="inspector"),
                ProjectMember(project_id=project.id, user_id=recipient.id, role="inspector"),
            ])
            doc = Document(doc_type="巡视记录", title="校验文书", content={},
                           project_id=project.id, creator_id=sender.id)
            db.add(doc)
            await db.commit()

            with pytest.raises(HTTPException) as bad_deadline:
                await create_item(CreateCollaborationRequest(
                    object_type="document", object_id=doc.id, recipient_id=recipient.id,
                    title="日期错误", deadline="2026-13-40",
                ), sender, db)
            assert bad_deadline.value.status_code == 400
            with pytest.raises(HTTPException) as blank_title:
                await create_item(CreateCollaborationRequest(
                    object_type="document", object_id=doc.id, recipient_id=recipient.id,
                    title="   ",
                ), sender, db)
            assert blank_title.value.status_code == 400
            normalized = await create_item(CreateCollaborationRequest(
                object_type="document", object_id=doc.id, recipient_id=recipient.id,
                title="正常日期", deadline=" 2026-12-31 ",
            ), sender, db)
            assert normalized["item"]["deadline"] == "2026-12-31"

        await engine.dispose()

    asyncio.run(scenario())
