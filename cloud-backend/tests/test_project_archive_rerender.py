import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import Base
from app.main import app  # noqa: F401
from app.models.document import Document
from app.models.knowledge_base import KnowledgeDocument
from app.models.project_member import ProjectMember
from app.models.user import Project, User
from app.services import project_archive


def test_archive_rerenders_missing_file_for_legacy_document(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    settings.export_dir.mkdir(parents=True)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="Archive rerender", code="ARCHIVE-RERENDER")
            db.add(project)
            await db.flush()
            owner = User(
                username="rerender-owner", real_name="Archive owner", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add(owner)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=owner.id, role="inspector"))
            doc = Document(
                doc_type="inspection", title="Legacy generated record", content={"doc_number": "X-1"},
                project_id=project.id, creator_id=owner.id, form_code="gd_b_214_xunshi",
                content_revision=2, archive_sync_status="pending",
            )
            db.add(doc)
            await db.commit()

            def fake_render(form_code, data):
                assert form_code == "gd_b_214_xunshi"
                assert data["doc_number"] == "X-1"
                output = settings.export_dir / "legacy-rendered.docx"
                output.write_bytes(b"rendered docx")
                return output

            monkeypatch.setattr("app.services.form_renderer.render_form", fake_render)
            candidates = await project_archive.list_archive_candidates(db, project.id)
            assert [row.id for row in candidates] == [doc.id]

            archived = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert archived is not None
            assert doc.archive_sync_status == "synced"
            assert doc.docx_path == "/exports/legacy-rendered.docx"
            assert archived.source_document_id == doc.id
            assert open(archived.path, "rb").read() == b"rendered docx"
            rows = (await db.execute(select(KnowledgeDocument))).scalars().all()
            assert len(rows) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_archive_render_failure_stays_pending_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    settings.export_dir.mkdir(parents=True)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="Archive retry", code="ARCHIVE-RERENDER-RETRY")
            db.add(project)
            await db.flush()
            owner = User(
                username="rerender-retry", real_name="Archive owner", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add(owner)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=owner.id, role="inspector"))
            doc = Document(
                doc_type="inspection", title="Legacy retry record", content={},
                project_id=project.id, creator_id=owner.id, form_code="gd_b_214_xunshi",
                content_revision=1, archive_sync_status="pending",
            )
            db.add(doc)
            await db.commit()

            def broken_render(form_code, data):
                raise RuntimeError("template unavailable")

            monkeypatch.setattr("app.services.form_renderer.render_form", broken_render)
            assert await project_archive.sync_document_to_project_archive(db, doc, owner) is None
            await db.commit()
            assert doc.archive_sync_status == "pending"
            assert "template unavailable" in (doc.archive_sync_error or "")
            assert (await db.execute(select(KnowledgeDocument))).scalars().all() == []
            assert await project_archive.count_archive_candidates(db, project.id) == 1

            def recovered_render(form_code, data):
                output = settings.export_dir / "recovered.docx"
                output.write_bytes(b"recovered")
                return output

            monkeypatch.setattr("app.services.form_renderer.render_form", recovered_render)
            archived = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert archived is not None
            assert doc.archive_sync_status == "synced"
            assert open(archived.path, "rb").read() == b"recovered"
        await engine.dispose()

    asyncio.run(scenario())
