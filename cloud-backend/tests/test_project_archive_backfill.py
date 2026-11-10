import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import Base
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument
from app.models.project_archive import ProjectArchiveLibrary
from app.models.project_member import ProjectMember
from app.models.user import Project, User
from app.routers.knowledge_base import backfill_project_archive_library
from app.services import project_archive


def _prepare_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    settings.export_dir.mkdir(parents=True)
    (settings.export_dir / "one.docx").write_bytes(b"one")
    (settings.export_dir / "two.docx").write_bytes(b"two")


def test_backfill_only_unsynced_revisions_and_is_idempotent(tmp_path, monkeypatch):
    _prepare_files(tmp_path, monkeypatch)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="项目甲", code="BACKFILL-A")
            other_project = Project(name="项目乙", code="BACKFILL-B")
            db.add_all([project, other_project])
            await db.flush()
            owner = User(
                username="backfill-owner", real_name="档案员", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            outsider = User(
                username="backfill-outsider", real_name="其他项目", role="inspector",
                password_hash="unused", project_id=other_project.id, is_active=True,
            )
            db.add_all([owner, outsider])
            await db.flush()
            db.add_all([
                ProjectMember(project_id=project.id, user_id=owner.id, role="inspector"),
                ProjectMember(project_id=other_project.id, user_id=outsider.id, role="inspector"),
            ])
            fresh = Document(
                doc_type="巡视记录", title="巡视记录-新", content={},
                project_id=project.id, creator_id=owner.id,
                docx_path="/exports/one.docx", content_revision=1,
            )
            stale = Document(
                doc_type="巡视记录", title="巡视记录-改版", content={},
                project_id=project.id, creator_id=owner.id,
                docx_path="/exports/two.docx", content_revision=1,
            )
            missing = Document(
                doc_type="巡视记录", title="巡视记录-文件丢失", content={},
                project_id=project.id, creator_id=owner.id,
                docx_path="/exports/missing.docx", content_revision=1,
            )
            foreign = Document(
                doc_type="巡视记录", title="其他项目记录", content={},
                project_id=other_project.id, creator_id=outsider.id,
                docx_path="/exports/one.docx", content_revision=1,
            )
            db.add_all([fresh, stale, missing, foreign])
            await db.commit()

            first_stale_archive = await project_archive.sync_document_to_project_archive(
                db, stale, owner
            )
            await db.commit()
            stale.content_revision = 2
            await db.commit()

            candidates = await project_archive.list_archive_candidates(db, project.id)
            assert {doc.id for doc in candidates} == {fresh.id, stale.id}
            assert missing.id not in {doc.id for doc in candidates}
            assert foreign.id not in {doc.id for doc in candidates}

            summary = await project_archive.backfill_project_archive(db, project.id, owner)
            assert summary["total"] == 2
            assert summary["processed"] == 2
            assert summary["synced"] == 2
            assert summary["failed"] == 0

            # Backfill follows the same personal-library rule; no shared
            # project archive library is created as a side effect.
            assert (await db.execute(select(ProjectArchiveLibrary))).scalars().all() == []
            home_kb = (await db.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.owner_id == owner.id,
                    KnowledgeBase.kb_type == "personal",
                )
            )).scalar_one()
            assert home_kb.project_id is None
            rows = (
                await db.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.kb_id == home_kb.id
                    )
                )
            ).scalars().all()
            assert len(rows) == 2
            assert first_stale_archive.id in {row.id for row in rows}
            stale_row = next(row for row in rows if row.source_document_id == stale.id)
            assert stale_row.source_revision == 2
            assert stale_row.filename.endswith(".docx")

            again = await project_archive.backfill_project_archive(db, project.id, owner)
            assert again["total"] == 0
            assert again["synced"] == 0
            assert await project_archive.count_archive_candidates(db, project.id) == 0
        await engine.dispose()

    asyncio.run(scenario())


def test_backfill_records_failure_and_retries_same_archive_row(tmp_path, monkeypatch):
    _prepare_files(tmp_path, monkeypatch)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="项目甲", code="BACKFILL-RETRY")
            db.add(project)
            await db.flush()
            owner = User(
                username="retry-owner", real_name="档案员", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add(owner)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=owner.id, role="inspector"))
            doc = Document(
                doc_type="验收记录", title="验收记录-重试", content={},
                project_id=project.id, creator_id=owner.id,
                docx_path="/exports/one.docx", content_revision=1,
            )
            db.add(doc)
            await db.commit()

            original_copy = project_archive.shutil.copy2
            monkeypatch.setattr(
                project_archive.shutil, "copy2",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
            )
            failed = await project_archive.backfill_project_archive(db, project.id, owner)
            assert failed["failed"] == 1
            assert failed["errors"][0]["document_id"] == doc.id
            assert doc.archive_sync_status == "failed"

            monkeypatch.setattr(project_archive.shutil, "copy2", original_copy)
            retried = await project_archive.backfill_project_archive(db, project.id, owner)
            assert retried["synced"] == 1
            assert doc.archive_sync_status == "synced"
            rows = (await db.execute(select(KnowledgeDocument))).scalars().all()
            assert len(rows) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_backfill_endpoint_requires_active_project_member(tmp_path, monkeypatch):
    _prepare_files(tmp_path, monkeypatch)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="项目甲", code="BACKFILL-PERM")
            db.add(project)
            await db.flush()
            outsider = User(
                username="perm-outsider", real_name="外部人员", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add(outsider)
            await db.commit()

            with pytest.raises(HTTPException) as exc:
                await backfill_project_archive_library(outsider, db)
            assert exc.value.status_code == 403
        await engine.dispose()

    asyncio.run(scenario())
