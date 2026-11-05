import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import Base
from app.main import app  # noqa: F401
from app.models.collaboration import CollaborationEvent, CollaborationItem
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBaseMember, KnowledgeDocument
from app.models.project_member import ProjectMember
from app.models.user import Project, User
from app.models.work_record import WorkRecord, WorkRecordStatus
from app.routers import work_record as work_record_router
from app.routers.collaboration import detail, inbox
from app.routers.document import approve_document, delete_document, submit_for_approval
from app.routers.project import AddMemberRequest, add_member, remove_member
from app.routers.work_record import archive_work_record
from app.services import document_signoffs, notification, project_archive, record_fields
from app.services.collaboration import upsert_action_items
from app.services.permissions import invalidate_matrix


@pytest.fixture(autouse=True)
def _isolate_permission_matrix_cache():
    # Other suites may load a partial test matrix into the process-wide cache.
    # Production loads the complete matrix at startup; tests must not depend on
    # execution order.
    invalidate_matrix()
    yield
    invalidate_matrix()


async def _new_database():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_document_delete_retires_action_but_keeps_archive_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_base_path', tmp_path / 'knowledge')
    monkeypatch.setattr(settings, 'export_dir', tmp_path / 'exports')
    settings.export_dir.mkdir(parents=True)
    source = settings.export_dir / 'source.docx'
    source.write_bytes(b'archived business document')

    async def scenario():
        engine, sessions = await _new_database()
        async with sessions() as db:
            project = Project(name='协作归档项目', code='COLLAB-DEL')
            db.add(project)
            await db.flush()
            user = User(
                username='collab-delete', real_name='处理人', role='inspector',
                password_hash='unused', project_id=project.id, is_active=True,
            )
            db.add(user)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=user.id, role='inspector'))
            doc = Document(
                doc_type='巡视记录', title='待删除协作文书', content={},
                project_id=project.id, creator_id=user.id,
                docx_path='/exports/source.docx', content_revision=1,
            )
            db.add(doc)
            await db.commit()

            archived = await project_archive.sync_document_to_project_archive(db, doc, user)
            await db.commit()
            assert archived is not None
            archive_id = archived.id
            archive_path = Path(archived.path)
            await upsert_action_items(
                db, project_id=project.id, sender_id=user.id,
                recipient_ids=[user.id], object_type='document', object_id=doc.id,
                action_type='approve', title='待审批删除测试',
                source_prefix='workflow', retire_missing=True,
            )
            await db.commit()
            item = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.object_type == 'document',
                CollaborationItem.object_id == doc.id,
            ))).scalar_one()
            item_id = item.id
            document_id = doc.id
            assert (await inbox('action', user, db))['pending_count'] == 1

            await delete_document(document_id, user, db)

            assert await db.get(Document, document_id) is None
            await db.refresh(item)
            assert item.status == 'canceled'
            assert (await inbox('action', user, db))['pending_count'] == 0
            survivor = await db.get(KnowledgeDocument, archive_id)
            assert survivor is not None
            assert survivor.source_document_id is None
            assert survivor.source_document_ref == f'deleted-document:{document_id}'
            assert archive_path.read_bytes() == b'archived business document'
            result = await detail(item_id, user, db)
            assert result['object_deleted'] is True
            assert any(row['action'] == 'canceled' for row in result['history'])
        await engine.dispose()

    asyncio.run(scenario())


def test_work_record_archive_completes_approval_responsibility(monkeypatch):
    async def scenario():
        engine, sessions = await _new_database()
        async with sessions() as db:
            project = Project(name='记录归档项目', code='COLLAB-ARCH')
            db.add(project)
            await db.flush()
            chief = User(
                username='collab-chief', real_name='总监', role='chief_supervisor',
                password_hash='unused', project_id=project.id, is_active=True,
            )
            db.add(chief)
            await db.flush()
            db.add(ProjectMember(
                project_id=project.id, user_id=chief.id, role='chief_supervisor',
            ))
            record = WorkRecord(
                record_type='巡视', status=WorkRecordStatus.APPROVED.value,
                project_id=project.id, creator_id=chief.id,
            )
            db.add(record)
            await db.commit()
            await upsert_action_items(
                db, project_id=project.id, sender_id=chief.id,
                recipient_ids=[chief.id], object_type='work_record',
                object_id=record.id, action_type='approve', title='待审批记录',
                source_prefix='workflow', retire_missing=True,
            )
            await db.commit()
            item = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.object_type == 'work_record',
                CollaborationItem.object_id == record.id,
            ))).scalar_one()

            async def ready(_db, _record):
                return None

            monkeypatch.setattr(work_record_router, '_validate_record_ready', ready)
            await archive_work_record(record.id, chief, db)

            await db.refresh(item)
            assert item.status == 'completed'
            assert (await inbox('action', chief, db))['pending_count'] == 0
            events = (await db.execute(select(CollaborationEvent).where(
                CollaborationEvent.item_id == item.id,
            ))).scalars().all()
            assert any(event.action == 'completed' for event in events)
        await engine.dispose()

    asyncio.run(scenario())


def test_project_role_downgrade_and_member_removal_cancel_old_approvals():
    async def scenario():
        engine, sessions = await _new_database()
        async with sessions() as db:
            project = Project(name='岗位变更项目', code='COLLAB-ROLE')
            db.add(project)
            await db.flush()
            manager = User(
                username='collab-manager', real_name='管理员', role='inspector',
                org_role='super_admin', password_hash='unused', is_active=True,
            )
            target = User(
                username='collab-target', real_name='原审批人', role='chief_supervisor',
                password_hash='unused', project_id=project.id, is_active=True,
            )
            db.add_all([manager, target])
            await db.flush()
            membership = ProjectMember(
                project_id=project.id, user_id=target.id, role='chief_supervisor',
            )
            db.add(membership)
            doc = Document(
                doc_type='巡视记录', title='岗位变更文书', content={},
                project_id=project.id, creator_id=target.id,
            )
            db.add(doc)
            await db.commit()

            await upsert_action_items(
                db, project_id=project.id, sender_id=target.id,
                recipient_ids=[target.id], object_type='document', object_id=doc.id,
                action_type='approve', title='待审批事项',
                source_prefix='workflow', retire_missing=True,
            )
            await db.commit()
            first = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.source_key.like('workflow:%'),
            ))).scalar_one()

            await add_member(
                project.id,
                AddMemberRequest(user_id=target.id, role='inspector'),
                manager,
                db,
            )
            await db.refresh(first)
            assert first.status == 'canceled'
            await db.refresh(membership)
            assert membership.role == 'inspector'

            await upsert_action_items(
                db, project_id=project.id, sender_id=target.id,
                recipient_ids=[target.id], object_type='document', object_id=doc.id,
                action_type='approve', title='离场前待审批事项',
                source_prefix='workflow', retire_missing=True,
            )
            await db.commit()
            second = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.source_key.like('workflow:%'),
                CollaborationItem.status == 'pending',
            ))).scalar_one()

            await remove_member(project.id, target.id, manager, db)
            await db.refresh(second)
            assert second.status == 'canceled'
            await db.refresh(membership)
            assert membership.left_at is not None
            assert target.project_id is None
        await engine.dispose()

    asyncio.run(scenario())


def test_submit_and_approve_document_assigns_every_current_approver(monkeypatch):
    async def scenario():
        engine, sessions = await _new_database()
        async with sessions() as db:
            project = Project(name='审批投递项目', code='COLLAB-SUBMIT')
            db.add(project)
            await db.flush()
            chief = User(
                username='submit-chief', real_name='总监', role='chief_supervisor',
                password_hash='unused', project_id=project.id, is_active=True,
            )
            deputy = User(
                username='submit-deputy', real_name='副总监', role='deputy_chief',
                password_hash='unused', project_id=project.id, is_active=True,
            )
            db.add_all([chief, deputy])
            await db.flush()
            db.add_all([
                ProjectMember(project_id=project.id, user_id=chief.id, role='chief_supervisor'),
                ProjectMember(project_id=project.id, user_id=deputy.id, role='deputy_chief'),
            ])
            doc = Document(
                doc_type='监理月报', title='审批投递测试', content={},
                project_id=project.id, creator_id=chief.id,
            )
            db.add(doc)
            await db.commit()

            monkeypatch.setattr(record_fields, 'validate_document', lambda _doc: None)

            async def allow_signoffs(_db, _doc):
                return None

            async def no_push(*_args, **_kwargs):
                return True

            monkeypatch.setattr(document_signoffs, 'assert_required_signoffs', allow_signoffs)
            monkeypatch.setattr(notification, 'push_to_glasses', no_push)
            monkeypatch.setattr(notification, 'push_realtime', no_push)

            await submit_for_approval(doc.id, chief, db)
            rows = (await db.execute(select(CollaborationItem).where(
                CollaborationItem.object_type == 'document',
                CollaborationItem.object_id == doc.id,
                CollaborationItem.action_type == 'approve',
            ))).scalars().all()
            assert {row.recipient_id for row in rows} == {chief.id, deputy.id}
            assert all(row.status == 'pending' for row in rows)
            assert (await inbox('action', chief, db))['pending_count'] == 1
            assert (await inbox('action', deputy, db))['pending_count'] == 1

            await approve_document(doc.id, chief, db)
            for row in rows:
                await db.refresh(row)
                assert row.status == 'completed'
            assert (await inbox('action', deputy, db))['pending_count'] == 0
        await engine.dispose()

    asyncio.run(scenario())


def test_archive_owner_prefers_project_leader_and_form_code_maps_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_base_path', tmp_path / 'knowledge')

    async def scenario():
        engine, sessions = await _new_database()
        async with sessions() as db:
            project = Project(name='负责人优先项目', code='COLLAB-OWNER')
            db.add(project)
            await db.flush()
            chief = User(
                username='archive-chief-owner', real_name='负责人', role='chief_supervisor',
                password_hash='unused', project_id=project.id, is_active=True,
            )
            inspector = User(
                username='archive-inspector-owner', real_name='检查员', role='inspector',
                password_hash='unused', project_id=project.id, is_active=True,
            )
            db.add_all([chief, inspector])
            await db.flush()
            db.add_all([
                ProjectMember(project_id=project.id, user_id=inspector.id, role='inspector'),
                ProjectMember(project_id=project.id, user_id=chief.id, role='chief_supervisor'),
            ])
            await db.commit()

            kb = await project_archive.ensure_project_archive_library(
                db, project.id, inspector.id,
            )
            await db.commit()
            assert kb.owner_id == chief.id
            roles = dict((await db.execute(select(
                KnowledgeBaseMember.user_id, KnowledgeBaseMember.role,
            ).where(KnowledgeBaseMember.kb_id == kb.id))).all())
            assert roles == {chief.id: 'owner', inspector.id: 'editor'}
        await engine.dispose()

    asyncio.run(scenario())
    assert project_archive._archive_folder_name('gd_b_220_rizhi', '验收记录') == '监理日志'
    assert project_archive._archive_folder_name('gdaq_30305_qizhong', '巡视记录') == '巡视记录'
    assert project_archive._archive_folder_name(None, '见证取样记录') == '见证记录'
    assert project_archive._archive_folder_name(None, '未登记类型') == '各类台账'
