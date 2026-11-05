import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import Base
from app.main import app  # noqa: F401
from app.models.document import Document
from app.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseMember,
    KnowledgeDocument,
)
from app.models.project_archive import ProjectArchiveLibrary
from app.models.project_member import ProjectMember
from app.models.user import Project, User
from app.routers.knowledge_base import (
    KnowledgeBaseCreate,
    MemberAdd,
    MemberRoleUpdate,
    PublishRequest,
    add_member,
    create_knowledge_base,
    delete_knowledge_base,
    get_knowledge_base,
    project_archive_library,
    publish_knowledge_base,
    remove_member,
    update_member_role,
)
from app.services import project_archive


def test_project_archive_library_is_unique_member_synced_and_manual_members_are_blocked(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    settings.export_dir.mkdir(parents=True)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="资料库项目", code="ARCHIVE-C")
            other_project = Project(name="其他项目", code="ARCHIVE-D")
            db.add_all([project, other_project])
            await db.flush()
            owner = User(
                username="archive-perm-owner", real_name="项目负责人", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            colleague = User(
                username="archive-perm-colleague", real_name="同事", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            outsider = User(
                username="archive-perm-outsider", real_name="外项目", role="inspector",
                password_hash="unused", project_id=other_project.id, is_active=True,
            )
            db.add_all([owner, colleague, outsider])
            await db.flush()
            owner_member = ProjectMember(
                project_id=project.id, user_id=owner.id, role="chief_supervisor"
            )
            colleague_member = ProjectMember(
                project_id=project.id, user_id=colleague.id, role="inspector"
            )
            db.add_all([
                owner_member,
                colleague_member,
                ProjectMember(project_id=other_project.id, user_id=outsider.id, role="inspector"),
            ])
            await db.commit()

            kb = await project_archive.ensure_project_archive_library(db, project.id, owner.id)
            await db.commit()
            mapping = (await db.execute(select(ProjectArchiveLibrary))).scalar_one()
            assert mapping.project_id == project.id and mapping.kb_id == kb.id

            # A same-named shared KB in another project must never be selected.
            decoy = KnowledgeBase(
                name=kb.name, kb_type="shared", owner_id=outsider.id,
                project_id=other_project.id,
            )
            db.add(decoy)
            await db.commit()
            selected = await project_archive.ensure_project_archive_library(
                db, project.id, owner.id
            )
            await db.commit()
            assert selected.id == kb.id
            endpoint = await project_archive_library(owner, db)
            assert endpoint["id"] == kb.id and endpoint["is_project_archive"] is True

            with pytest.raises(HTTPException) as add_blocked:
                await add_member(
                    kb.id, MemberAdd(user_id=outsider.id, role="viewer"), owner, db
                )
            assert add_blocked.value.status_code == 409
            with pytest.raises(HTTPException) as update_blocked:
                await update_member_role(
                    kb.id, colleague.id, MemberRoleUpdate(role="viewer"), owner, db
                )
            assert update_blocked.value.status_code == 409
            with pytest.raises(HTTPException) as remove_blocked:
                await remove_member(kb.id, colleague.id, owner, db)
            assert remove_blocked.value.status_code == 409
            with pytest.raises(HTTPException) as publish_blocked:
                await publish_knowledge_base(
                    kb.id, PublishRequest(is_public=True), owner, db
                )
            assert publish_blocked.value.status_code == 409
            with pytest.raises(HTTPException) as delete_blocked:
                await delete_knowledge_base(kb.id, owner, db)
            assert delete_blocked.value.status_code == 409

            # Project membership is the single source of KB access.
            colleague_member.left_at = datetime.now()
            await db.flush()
            assert await project_archive.sync_project_archive_members(
                db, project.id, owner.id
            )
            await db.commit()

            db.add(ProjectMember(project_id=project.id, user_id=outsider.id, role="inspector"))
            await db.flush()
            assert await project_archive.sync_project_archive_members(
                db, project.id, owner.id
            )
            await db.commit()

            roles = dict((await db.execute(
                select(KnowledgeBaseMember.user_id, KnowledgeBaseMember.role).where(
                    KnowledgeBaseMember.kb_id == kb.id
                )
            )).all())
            assert roles[owner.id] == "owner"
            assert roles[outsider.id] == "editor"
            assert colleague.id not in roles

            # A stale mapping to a personal KB is repaired by the project mapping.
            personal = KnowledgeBase(name="个人库", kb_type="personal", owner_id=owner.id)
            db.add(personal)
            await db.flush()
            mapping.kb_id = personal.id
            await db.flush()
            repaired = await project_archive.ensure_project_archive_library(
                db, project.id, owner.id
            )
            await db.commit()
            assert repaired.id != personal.id
            assert repaired.kb_type == "shared" and repaired.project_id == project.id
            mapping = (await db.execute(select(ProjectArchiveLibrary))).scalar_one()
            assert mapping.kb_id == repaired.id
        await engine.dispose()

    asyncio.run(scenario())


def test_archive_detach_keeps_copy_after_business_document_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    settings.export_dir.mkdir(parents=True)
    source = settings.export_dir / "keep.docx"
    source.write_bytes(b"keep me")

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="保留项目", code="ARCHIVE-K")
            db.add(project)
            await db.flush()
            user = User(
                username="archive-keeper", real_name="成员", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add(user)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=user.id, role="inspector"))
            doc = Document(
                doc_type="巡视记录", title="待保留归档", content={},
                project_id=project.id, creator_id=user.id,
                docx_path="/exports/keep.docx", content_revision=1,
            )
            db.add(doc)
            await db.commit()
            archived = await project_archive.sync_document_to_project_archive(db, doc, user)
            await db.commit()
            assert archived is not None
            archived_path = archived.path
            archived_id = archived.id
            document_id = doc.id

            assert await project_archive.detach_project_archive_copy(db, document_id) == 1
            await db.delete(doc)
            await db.commit()

            survivor = await db.get(KnowledgeDocument, archived_id)
            assert survivor is not None
            assert survivor.source_document_id is None
            assert survivor.source_document_ref == f"deleted-document:{document_id}"
            assert open(archived_path, "rb").read() == b"keep me"
        await engine.dispose()

    asyncio.run(scenario())


def test_existing_project_shared_kb_is_adopted_and_duplicate_creation_is_blocked(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="历史共享库项目", code="ARCHIVE-LEGACY")
            db.add(project)
            await db.flush()
            owner = User(
                username="archive-legacy-owner", real_name="负责人", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add(owner)
            await db.flush()
            db.add(ProjectMember(
                project_id=project.id, user_id=owner.id, role="inspector",
            ))
            legacy = KnowledgeBase(
                name="项目共享资料库", description="历史共享库", kb_type="shared",
                owner_id=owner.id, project_id=project.id, is_public=True,
                invite_code="LEGACY01",
            )
            db.add(legacy)
            await db.commit()
            legacy_id = legacy.id

            adopted = await project_archive.ensure_project_archive_library(
                db, project.id, owner.id,
            )
            await db.commit()
            assert adopted.id == legacy_id
            assert adopted.is_public is False
            assert adopted.invite_code is None
            mapping = (await db.execute(select(ProjectArchiveLibrary))).scalar_one()
            assert mapping.project_id == project.id and mapping.kb_id == legacy_id

            with pytest.raises(HTTPException) as duplicate:
                await create_knowledge_base(
                    KnowledgeBaseCreate(
                        name="另一个共享库", kb_type="shared", project_id=project.id,
                    ),
                    owner,
                    db,
                )
            assert duplicate.value.status_code == 409
            shared_count = (await db.execute(select(KnowledgeBase).where(
                KnowledgeBase.project_id == project.id,
                KnowledgeBase.kb_type == "shared",
            ))).scalars().all()
            assert len(shared_count) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_completed_project_keeps_former_members_as_read_only_viewers(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="结项项目", code="ARCHIVE-CLOSED")
            db.add(project)
            await db.flush()
            owner = User(
                username="archive-closed-owner", real_name="负责人", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            colleague = User(
                username="archive-closed-colleague", real_name="成员", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add_all([owner, colleague])
            await db.flush()
            owner_member = ProjectMember(
                project_id=project.id, user_id=owner.id, role="inspector",
            )
            colleague_member = ProjectMember(
                project_id=project.id, user_id=colleague.id, role="inspector",
            )
            db.add_all([owner_member, colleague_member])
            await db.commit()

            kb = await project_archive.ensure_project_archive_library(
                db, project.id, owner.id,
            )
            await db.commit()
            initial_roles = dict((await db.execute(select(
                KnowledgeBaseMember.user_id, KnowledgeBaseMember.role,
            ).where(KnowledgeBaseMember.kb_id == kb.id))).all())
            assert initial_roles == {owner.id: "owner", colleague.id: "editor"}

            now = datetime.now()
            project.status = "completed"
            owner_member.left_at = now
            colleague_member.left_at = now
            await db.flush()
            assert await project_archive.sync_project_archive_members(
                db, project.id, owner.id,
            )
            await db.commit()

            closed_roles = dict((await db.execute(select(
                KnowledgeBaseMember.user_id, KnowledgeBaseMember.role,
            ).where(KnowledgeBaseMember.kb_id == kb.id))).all())
            assert closed_roles == {owner.id: "viewer", colleague.id: "viewer"}
            readable = await get_knowledge_base(kb.id, owner, db)
            assert readable["role"] == "viewer"

            # Reopening the project removes historical viewers until members are
            # appointed again; project membership remains the access source.
            project.status = "active"
            await db.flush()
            assert await project_archive.sync_project_archive_members(
                db, project.id, owner.id,
            )
            await db.commit()
            remaining = (await db.execute(select(KnowledgeBaseMember).where(
                KnowledgeBaseMember.kb_id == kb.id
            ))).scalars().all()
            assert remaining == []
        await engine.dispose()

    asyncio.run(scenario())