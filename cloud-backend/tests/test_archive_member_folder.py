"""项目资料库的成员文件夹结构（2026-11-27）。

口径：
  * 生成的文书只写一份，落「项目资料库 / <创建人> / 工作记录与表单 / <类型>」；
  * 每个在岗成员一个根文件夹（KbFolder.owner_id），「我的资料」只是定位过去；
  * 监理日记是个人工作记录，仍只进创建人个人库，不进项目库。
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db import Base
from app.main import app  # noqa: F401
from app.models.document import Document
from app.models.knowledge_base import KbFolder, KnowledgeBase, KnowledgeDocument
from app.models.project_member import ProjectMember
from app.models.user import Project, User
from app.services import project_archive


def test_legacy_archive_in_personal_library_is_resynced_into_project(tmp_path, monkeypatch):
    """历史归档行在个人库时必须视为「待同步」。

    2026-11-27 之前文书归档到创建人个人库；切换目标后如果只按
    archive_sync_status 判断，这些行会永远留在个人库，不同账号看到的份数
    就会对不上（实测同事 80+ / 本人 10）。
    """
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    settings.export_dir.mkdir(parents=True)
    (settings.export_dir / "legacy.docx").write_bytes(b"legacy")

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="项目甲", code="LEGACY-A")
            db.add(project)
            await db.flush()
            owner = User(
                username="legacy-owner", real_name="张三", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add(owner)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=owner.id, role="inspector"))
            personal_kb = KnowledgeBase(name="我的资料库", kb_type="personal", owner_id=owner.id)
            db.add(personal_kb)
            await db.flush()
            doc = Document(
                doc_type="巡视记录", title="老文书", content={},
                project_id=project.id, creator_id=owner.id,
                docx_path="/exports/legacy.docx", content_revision=1,
            )
            db.add(doc)
            await db.flush()
            legacy_row = KnowledgeDocument(
                kb_id=personal_kb.id, uploader_id=owner.id, folder_id=None,
                name="老文书", filename="老文书.docx", path="/exports/legacy.docx",
                size_bytes=6, source_document_id=doc.id, source_revision=1,
                source_document_ref=f"document:{doc.id}",
            )
            db.add(legacy_row)
            await db.flush()
            doc.archive_sync_status = "synced"
            doc.archive_kb_document_id = legacy_row.id
            doc.archive_synced_revision = 1
            await db.commit()

            candidates = await project_archive.list_archive_candidates(db, project.id)
            assert doc.id in {item.id for item in candidates}, "个人库里的历史归档必须重新同步"

            moved = await project_archive.sync_document_to_project_archive(db, doc, owner)
            await db.commit()
            assert moved.id == legacy_row.id
            kb = await db.get(KnowledgeBase, moved.kb_id)
            assert kb.kb_type == "shared"
            assert kb.project_id == project.id
            # 迁移后不再是候选
            assert doc.id not in {
                item.id for item in await project_archive.list_archive_candidates(db, project.id)
            }

        await engine.dispose()

    asyncio.run(scenario())


def test_member_folders_per_creator_and_diary_stays_personal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_base_path", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    settings.export_dir.mkdir(parents=True)
    (settings.export_dir / "record.docx").write_bytes(b"doc")
    (settings.export_dir / "diary.docx").write_bytes(b"diary")

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            project = Project(name="项目甲", code="MEMBER-A")
            db.add(project)
            await db.flush()
            owner = User(
                username="owner-a", real_name="张三", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            colleague = User(
                username="owner-b", real_name="李四", role="inspector",
                password_hash="unused", project_id=project.id, is_active=True,
            )
            db.add_all([owner, colleague])
            await db.flush()
            db.add_all([
                ProjectMember(project_id=project.id, user_id=owner.id, role="inspector"),
                ProjectMember(project_id=project.id, user_id=colleague.id, role="inspector"),
            ])
            inspection = Document(
                doc_type="巡视记录", title="张三的巡视", content={},
                project_id=project.id, creator_id=owner.id,
                docx_path="/exports/record.docx", content_revision=1,
            )
            acceptance = Document(
                doc_type="验收记录", title="李四的验收", content={},
                project_id=project.id, creator_id=colleague.id,
                docx_path="/exports/record.docx", content_revision=1,
            )
            diary = Document(
                doc_type="监理日记", title="张三的日记", content={},
                project_id=project.id, creator_id=owner.id,
                docx_path="/exports/diary.docx", content_revision=1,
            )
            db.add_all([inspection, acceptance, diary])
            await db.commit()

            row_a = await project_archive.sync_document_to_project_archive(db, inspection, owner)
            row_b = await project_archive.sync_document_to_project_archive(db, acceptance, colleague)
            row_diary = await project_archive.sync_document_to_project_archive(db, diary, owner)
            await db.commit()

            project_kb = await db.get(KnowledgeBase, row_a.kb_id)
            assert project_kb.kb_type == "shared"
            assert row_b.kb_id == project_kb.id
            # 上传人 = 各自的创建人（「要有我的信息」）
            assert row_a.uploader_id == owner.id
            assert row_b.uploader_id == colleague.id

            member_a = await db.get(KbFolder, (await db.get(KbFolder, row_a.folder_id)).parent_id)
            member_b = await db.get(KbFolder, (await db.get(KbFolder, row_b.folder_id)).parent_id)
            folder_a = await db.get(KbFolder, member_a.parent_id)
            folder_b = await db.get(KbFolder, member_b.parent_id)
            assert folder_a.owner_id == owner.id and folder_a.name == "张三"
            assert folder_b.owner_id == colleague.id and folder_b.name == "李四"
            assert folder_a.parent_id is None and folder_b.parent_id is None

            # 项目库里只有两份正式文书；日记不在项目库
            project_docs = (
                await db.execute(
                    select(KnowledgeDocument).where(KnowledgeDocument.kb_id == project_kb.id)
                )
            ).scalars().all()
            assert {row.id for row in project_docs} == {row_a.id, row_b.id}

            # 日记落个人库（仅本人）
            diary_kb = await db.get(KnowledgeBase, row_diary.kb_id)
            assert diary_kb.kb_type == "personal"
            assert diary_kb.owner_id == owner.id
            assert row_diary.uploader_id == owner.id

        await engine.dispose()

    asyncio.run(scenario())
