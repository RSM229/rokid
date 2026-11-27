"""资料库目录树拆壳迁移（2026-11-27）。

历史库在根下固定套「个人文件夹 / 项目文件夹」两层。小程序补上
「项目资料 / 我的资料」切换后，这层包壳与切换重复，故就地拆掉：

* 个人库：个人文件夹 → 我的文件；项目文件夹里只保留「工作记录与表单」，
  其余空的预设分类删除；
* 共享库：两个包壳都拆掉，子目录提升到根；
* 文档全部保留（包壳里的落到未分类，子目录里的原地不动）。

这些断言同时保证迁移是幂等的——重复调用不会再改动结构。
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import app  # noqa: F401  （导入全部模型，建表时才有 documents 等外键目标）
from app.models.knowledge_base import (
    KbFolder,
    KnowledgeBase,
    KnowledgeDocument,
    ensure_kb_folder_tree,
)
from app.models.user import User


async def _seed_legacy_tree(db, kb_id: int) -> dict:
    """复刻旧版 PRESET_FOLDER_TREE 的根结构（必须先 flush 才能拿到父目录 id）。"""
    personal = KbFolder(kb_id=kb_id, parent_id=None, name="个人文件夹", is_preset=True)
    project = KbFolder(kb_id=kb_id, parent_id=None, name="项目文件夹", is_preset=True)
    db.add_all([personal, project])
    await db.flush()
    return {"personal": personal, "project": project}


def test_personal_library_flattens_wrappers_and_keeps_archive_folder():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            user = User(
                username="kb-owner", real_name="资料员", role="inspector",
                password_hash="unused", is_active=True,
            )
            db.add(user)
            await db.flush()
            kb = KnowledgeBase(name="我的资料库", kb_type="personal", owner_id=user.id)
            db.add(kb)
            await db.flush()

            roots = await _seed_legacy_tree(db, kb.id)
            work = KbFolder(kb_id=kb.id, parent_id=roots["project"].id, name="工作记录与表单", is_preset=True)
            meeting = KbFolder(kb_id=kb.id, parent_id=roots["project"].id, name="会议记录", is_preset=True)
            db.add_all([work, meeting])
            await db.flush()
            inspection = KbFolder(kb_id=kb.id, parent_id=work.id, name="巡视记录", is_preset=True)
            db.add(inspection)
            await db.flush()

            loose = KnowledgeDocument(
                kb_id=kb.id, uploader_id=user.id, folder_id=roots["personal"].id,
                name="随手记", filename="note.pdf", path="/tmp/note.pdf", size_bytes=1,
            )
            archived = KnowledgeDocument(
                kb_id=kb.id, uploader_id=user.id, folder_id=inspection.id,
                name="巡视文书", filename="xunshi.pdf", path="/tmp/xunshi.pdf", size_bytes=1,
            )
            db.add_all([loose, archived])
            await db.commit()

            await ensure_kb_folder_tree(db, kb.id)
            await db.commit()

            rows = (await db.execute(select(KbFolder).where(KbFolder.kb_id == kb.id))).scalars().all()
            by_id = {row.id: row for row in rows}
            root_names = {row.name for row in rows if row.parent_id is None}

            # 包壳消失，个人库根只剩「我的文件 + 工作记录与表单」
            assert "个人文件夹" not in root_names
            assert "项目文件夹" not in root_names
            assert {"我的文件", "工作记录与表单"} <= root_names
            # 空的会议记录在个人库被清掉，归档目录保留
            assert "会议记录" not in {row.name for row in rows}

            # 两份文档都还在：包壳里的并入「我的文件」，归档目录里的原地不动
            await db.refresh(loose)
            await db.refresh(archived)
            assert by_id[loose.folder_id].name == "我的文件"
            assert by_id[archived.folder_id].name == "巡视记录"

            # 幂等：再跑一次结构不变
            before = sorted((row.id, row.parent_id, row.name) for row in rows)
            await ensure_kb_folder_tree(db, kb.id)
            await db.commit()
            rows2 = (await db.execute(select(KbFolder).where(KbFolder.kb_id == kb.id))).scalars().all()
            after = sorted((row.id, row.parent_id, row.name) for row in rows2)
            assert before == after

        await engine.dispose()

    asyncio.run(scenario())


def test_shared_library_drops_both_wrappers():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            user = User(
                username="kb-chief", real_name="总监", role="chief_supervisor",
                password_hash="unused", is_active=True,
            )
            db.add(user)
            await db.flush()
            kb = KnowledgeBase(name="项目资料库", kb_type="shared", owner_id=user.id)
            db.add(kb)
            await db.flush()

            roots = await _seed_legacy_tree(db, kb.id)
            meeting = KbFolder(kb_id=kb.id, parent_id=roots["project"].id, name="会议记录", is_preset=True)
            db.add(meeting)
            await db.flush()
            loose = KnowledgeDocument(
                kb_id=kb.id, uploader_id=user.id, folder_id=roots["personal"].id,
                name="个人草稿", filename="draft.pdf", path="/tmp/draft.pdf", size_bytes=1,
            )
            db.add(loose)
            await db.commit()

            await ensure_kb_folder_tree(db, kb.id)
            await db.commit()

            rows = (await db.execute(select(KbFolder).where(KbFolder.kb_id == kb.id))).scalars().all()
            root_names = {row.name for row in rows if row.parent_id is None}
            assert "个人文件夹" not in root_names
            assert "项目文件夹" not in root_names
            # 项目分类提升到根，会议记录保留；「工作记录与表单」移进成员文件夹，
            # 共享库根上不再有这一层（空壳会被清掉）
            assert {"会议记录", "合同与资质"} <= root_names
            assert "工作记录与表单" not in root_names
            await db.refresh(loose)
            assert loose.folder_id is None

        await engine.dispose()

    asyncio.run(scenario())


def test_personal_library_keeps_categories_with_custom_folders():
    """用户自建的空目录不能被当「空预设」删掉——整棵提升到根。"""

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            user = User(
                username="kb-owner-2", real_name="资料员", role="inspector",
                password_hash="unused", is_active=True,
            )
            db.add(user)
            await db.flush()
            kb = KnowledgeBase(name="我的资料库", kb_type="personal", owner_id=user.id)
            db.add(kb)
            await db.flush()

            roots = await _seed_legacy_tree(db, kb.id)
            contract = KbFolder(kb_id=kb.id, parent_id=roots["project"].id, name="合同与资质", is_preset=True)
            db.add(contract)
            await db.flush()
            custom = KbFolder(kb_id=kb.id, parent_id=contract.id, name="我的分类", is_preset=False)
            db.add(custom)
            await db.commit()

            await ensure_kb_folder_tree(db, kb.id)
            await db.commit()

            rows = (await db.execute(select(KbFolder).where(KbFolder.kb_id == kb.id))).scalars().all()
            by_id = {row.id: row for row in rows}
            assert by_id[custom.id].name == "我的分类"
            parent = by_id[by_id[custom.id].parent_id]
            assert parent.name == "合同与资质"
            assert parent.parent_id is None  # 整棵提升到根，不再是项目文件夹的子目录

        await engine.dispose()

    asyncio.run(scenario())
