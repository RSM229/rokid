import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import Base
from app.main import app  # noqa: F401
from app.models.document import Document
from app.models.knowledge_base import (
    KbFolder,
    KnowledgeBase,
    KnowledgeBaseMember,
    KnowledgeDocument,
)
from app.models.project_archive import ProjectArchiveLibrary
from app.models.project_member import ProjectMember
from app.models.user import Project, User
from app.routers.knowledge_base import list_knowledge_bases
from app.routers import form_doc
from app.routers.form_doc import export_form_doc
from app.services import project_archive


def test_archive_sync_uses_creator_personal_library_and_is_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    settings.export_dir.mkdir(parents=True)
    source_file = settings.export_dir / "record.docx"
    source_file.write_bytes(b"revision one")

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="项目甲", code="ARCHIVE-A")
            other_project = Project(name="项目乙", code="ARCHIVE-B")
            db.add_all([project, other_project])
            await db.flush()
            owner = User(
                username="archive-owner", real_name="档案员", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            colleague = User(
                username="archive-colleague", real_name="项目成员", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            outsider = User(
                username="archive-outsider", real_name="其他项目成员", role="inspector",
                password_hash="unused", project_id=other_project.id, is_active=True,
            )
            db.add_all([owner, colleague, outsider])
            await db.flush()
            db.add_all([
                ProjectMember(project_id=project.id, user_id=owner.id, role="inspector"),
                ProjectMember(project_id=project.id, user_id=colleague.id, role="inspector"),
                ProjectMember(project_id=other_project.id, user_id=outsider.id, role="inspector"),
            ])
            doc = Document(
                doc_type="巡视记录", title="巡视记录-A", content={},
                project_id=project.id, creator_id=owner.id,
                docx_path="/exports/record.docx", content_revision=1,
            )
            db.add(doc)
            await db.commit()

            archived = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert archived is not None
            original_archive_id = archived.id
            revision_one_path = archived.path
            kb_id = archived.kb_id
            assert archived.source_revision == 1
            assert open(archived.path, "rb").read() == b"revision one"

            # New archives live in the creator's own home library, not in a
            # newly-created shared project library.
            assert (await db.execute(select(ProjectArchiveLibrary))).scalars().all() == []
            kb = await db.get(KnowledgeBase, kb_id)
            assert kb.kb_type == "personal"
            assert kb.owner_id == owner.id
            assert kb.project_id is None
            members = set((await db.execute(select(KnowledgeBaseMember.user_id).where(
                KnowledgeBaseMember.kb_id == kb_id
            ))).scalars().all())
            assert members == {owner.id}
            assert outsider.id not in members
            libraries = await list_knowledge_bases(colleague, db)
            assert not any(item["id"] == kb_id for item in libraries["items"])
            folder = await db.get(KbFolder, archived.folder_id)
            parent = await db.get(KbFolder, folder.parent_id)
            grandparent = await db.get(KbFolder, parent.parent_id)
            assert (grandparent.name, parent.name, folder.name) == (
                "项目文件夹", "工作记录与表单", "巡视记录"
            )

            source_file.write_bytes(b"revision two")
            doc.content_revision = 2
            same_row = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert same_row.id == original_archive_id
            assert same_row.source_revision == 2
            assert open(same_row.path, "rb").read() == b"revision two"
            assert same_row.path != revision_one_path

            signed_file = settings.export_dir / "signed.pdf"
            signed_file.write_bytes(b"signed copy")
            doc.signed_pdf_path = "/exports/signed.pdf"
            signed = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert signed.id == original_archive_id
            assert signed.filename.endswith(".pdf")
            assert open(signed.path, "rb").read() == b"signed copy"

            pdf_only = settings.export_dir / "formal.pdf"
            pdf_only.write_bytes(b"formal unsigned pdf")
            doc.signed_pdf_path = None
            doc.docx_path = None
            doc.pdf_path = "/exports/formal.pdf"
            pdf_archive = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert pdf_archive.id == original_archive_id
            assert open(pdf_archive.path, "rb").read() == b"formal unsigned pdf"

            original_copy = project_archive.shutil.copy2
            monkeypatch.setattr(
                project_archive.shutil, "copy2",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
            )
            doc.content_revision = 3
            doc.signed_pdf_path = None
            failed = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert failed is None
            assert doc.archive_sync_status == "failed"
            # The DB session remains usable after the archive savepoint rolls back.
            assert (await db.execute(select(ProjectArchiveLibrary))).scalars().all() == []

            monkeypatch.setattr(project_archive.shutil, "copy2", original_copy)
            retried = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert retried.id == original_archive_id
            assert retried.source_revision == 3
            assert doc.archive_sync_status == "synced"

            # Archiving as another user still targets the creator's library and
            # cannot redirect the copy into their own home library.
            colleague_synced = await project_archive.sync_document_to_project_archive(
                db, doc, colleague
            )
            await db.commit()
            await db.refresh(kb)
            assert colleague_synced.id == original_archive_id
            assert colleague_synced.kb_id == kb_id
            assert kb.owner_id == owner.id

            def render_pdf(_docx_path):
                output = settings.export_dir / "form-export.pdf"
                output.write_bytes(b"official exported PDF")
                return output

            monkeypatch.setattr(form_doc, "docx_to_pdf", render_pdf)
            doc.docx_path = "/exports/record.docx"
            doc.pdf_path = None
            doc.signed_pdf_path = None
            doc.content_revision = 4
            await db.commit()
            original_sync = form_doc.sync_document_to_project_archive
            automatic_sync_calls = []

            async def track_export_sync(session, document, actor):
                automatic_sync_calls.append(document.pdf_path)
                return await original_sync(session, document, actor)

            monkeypatch.setattr(
                form_doc, "sync_document_to_project_archive", track_export_sync
            )
            response = await export_form_doc(
                doc.id, "pdf", False, colleague, db
            )
            assert response.path.endswith("form-export.pdf")
            assert doc.pdf_path == "/exports/form-export.pdf"
            assert (settings.export_dir / "form-export.pdf").exists()
            assert automatic_sync_calls == ["/exports/form-export.pdf"]
            synced_pdf = (await db.execute(select(KnowledgeDocument).where(
                KnowledgeDocument.source_document_id == doc.id
            ))).scalar_one()
            assert doc.archive_sync_status == "synced", doc.archive_sync_error
            assert synced_pdf.filename.endswith(".pdf")
            assert open(synced_pdf.path, "rb").read() == b"official exported PDF"
        await engine.dispose()

    asyncio.run(scenario())
