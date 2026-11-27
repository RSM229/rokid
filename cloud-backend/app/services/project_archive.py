"""Synchronize generated business documents into the project knowledge base."""
from __future__ import annotations

import asyncio
import mimetypes
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document, DocType
from app.models.knowledge_base import (
    KbFolder,
    KnowledgeBase,
    KnowledgeBaseMember,
    KnowledgeDocument,
    ensure_kb_folder_tree,
    ensure_member_folder,
)
from app.models.project_archive import ProjectArchiveLibrary
from app.models.project_member import ProjectMember
from app.models.user import Project, User


FOLDER_BY_DOC_TYPE = {
    "巡视记录": "巡视记录",
    "旁站记录": "旁站记录",
    "见证取样记录": "见证记录",
    "验收记录": "验收记录",
    "监理日志": "监理日志",
    "监理日记": "监理日记",
    "监理周报": "监理月报",
    "监理月报": "监理月报",
    "安全监理日志": "监理日志",
    "平行检查记录": "检监测记录",
    "平行检验记录": "检监测记录",
    "材料进场记录": "材料进场记录",
    "材料台账": "材料进场记录",
    "危大工程巡视记录": "巡视记录",
    "监理工程师通知单（质量类）": "监理通知单",
    "安全隐患整改通知单": "监理通知单",
    "安全隐患整改回复单": "监理通知单",
}


# form_code wins over the legacy doc_type when both are present. Several form
# templates share one historical DocType, so doc_type alone is not precise.
FOLDER_BY_FORM_CODE = {
    "acceptance_record": "验收记录",
    "witness_record": "见证记录",
    "gd_b_212_pangzhan": "旁站记录",
    "gd_b_213_pingxing": "检监测记录",
    "gd_b_214_xunshi": "巡视记录",
    "gd_b_215_tongzhi": "监理通知单",
    "gd_b_216_lianxi": "监理通知单",
    "gd_b_219_cailiao": "材料进场记录",
    "gd_b_220_rizhi": "监理日志",
    "jianli_riji": "监理日记",
    "anquan_jianli_rizhi": "监理日志",
    "gd_b_221_yuebao": "监理月报",
    "gd_b_223_zhoubao": "监理月报",
    "gdaq_30306_weida": "巡视记录",
    "gdaq_30307_lianxi": "监理通知单",
    "gdaq_30308_yinhuan": "监理通知单",
    "gdaq_30309_huifu": "监理通知单",
}


PROJECT_ARCHIVE_OWNER_PRIORITY = (
    'chief_supervisor', 'deputy_chief', 'special_supervisor',
    'documenter', 'inspector',
)


def _member_owner_rank(member) -> int:
    roles = (member.role, *(member.extra_roles or []))
    return min(
        (PROJECT_ARCHIVE_OWNER_PRIORITY.index(role)
         for role in roles if role in PROJECT_ARCHIVE_OWNER_PRIORITY),
        default=len(PROJECT_ARCHIVE_OWNER_PRIORITY),
    )


def _select_archive_owner(project_members, preferred_id: int) -> int:
    valid_ids = {row.user_id for row in project_members}
    if not valid_ids:
        return preferred_id
    ranks = {row.user_id: _member_owner_rank(row) for row in project_members}
    best_rank = min(ranks.values())
    if preferred_id in valid_ids and ranks[preferred_id] == best_rank:
        return preferred_id
    return min(valid_ids, key=lambda user_id: (ranks[user_id], user_id))


def _archive_folder_name(form_code: str | None, doc_type: str) -> str:
    if form_code and form_code in FOLDER_BY_FORM_CODE:
        return FOLDER_BY_FORM_CODE[form_code]
    return FOLDER_BY_DOC_TYPE.get(doc_type, "各类台账")


def _kb_root() -> Path:
    root = settings.knowledge_base_path / "user_kb"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _source_path(path: str | None) -> Path | None:
    if not path:
        return None
    if path.startswith("/exports/"):
        return settings.export_dir / path[len("/exports/") :]
    if path.startswith("/uploads/"):
        return settings.upload_dir / path[len("/uploads/") :]
    candidate = Path(path)
    return candidate if candidate.is_absolute() else None


async def _sync_members(
    db: AsyncSession, kb: KnowledgeBase, project_id: int, owner_id: int
) -> None:
    project = await db.get(Project, project_id)
    project_closed = bool(
        project and (project.status or "active") != "active"
    )
    project_members = (await db.execute(
        select(ProjectMember.user_id, ProjectMember.role, ProjectMember.extra_roles)
        .join(User, User.id == ProjectMember.user_id)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.left_at.is_(None),
            User.is_active.is_(True),
        )
    )).all()
    if project_members:
        owner_id = _select_archive_owner(project_members, owner_id)
        if kb.owner_id != owner_id:
            kb.owner_id = owner_id
    member_ids = {row.user_id for row in project_members}

    existing = {
        row.user_id: row
        for row in (
            await db.execute(
                select(KnowledgeBaseMember).where(
                    KnowledgeBaseMember.kb_id == kb.id
                )
            )
        ).scalars().all()
    }
    for user_id, member in existing.items():
        if user_id not in member_ids:
            if project_closed:
                # Completed projects keep former project members able to read
                # the formal archive. They never regain write responsibility.
                member.role = "viewer"
            else:
                await db.delete(member)
    for user_id in member_ids:
        role = "owner" if user_id == owner_id else "editor"
        if user_id in existing:
            existing[user_id].role = role
        else:
            db.add(KnowledgeBaseMember(kb_id=kb.id, user_id=user_id, role=role))


async def ensure_project_archive_library(
    db: AsyncSession, project_id: int, owner_id: int
) -> KnowledgeBase:
    """Return the one archive KB mapped to ``project_id``.

    The mapping table is the only source of truth. Names are deliberately not
    used, and a stale mapping to a personal/builtin/other-project KB is repaired
    by creating a fresh, correctly scoped shared KB.
    """
    mapping = (
        await db.execute(
            select(ProjectArchiveLibrary).where(
                ProjectArchiveLibrary.project_id == project_id
            )
        )
    ).scalar_one_or_none()
    if mapping:
        kb = await db.get(KnowledgeBase, mapping.kb_id)
        if (
            kb
            and kb.kb_type == "shared"
            and kb.project_id == project_id
            and not kb.is_builtin
        ):
            # Archive libraries must never be discoverable or joinable outside
            # the project membership sync.
            kb.is_public = False
            kb.invite_code = None
            await _sync_members(db, kb, project_id, kb.owner_id)
            await ensure_kb_folder_tree(db, kb.id, commit=False)
            return kb
        await db.delete(mapping)
        await db.flush()

    project_members = (await db.execute(
        select(ProjectMember.user_id, ProjectMember.role, ProjectMember.extra_roles)
        .join(User, User.id == ProjectMember.user_id)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.left_at.is_(None),
            User.is_active.is_(True),
        )
    )).all()
    if not project_members:
        raise RuntimeError("项目没有在岗成员，无法创建项目资料库")

    # Projects created before the mapping table may already have a shared KB.
    # Adopt that project-scoped library instead of creating a duplicate. The
    # lookup is by project_id, never by library name.
    legacy_kb = (await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.project_id == project_id,
            KnowledgeBase.kb_type == "shared",
            KnowledgeBase.is_builtin.is_(False),
        ).order_by(KnowledgeBase.id)
    )).scalars().first()
    if legacy_kb is not None:
        owner_id = _select_archive_owner(project_members, legacy_kb.owner_id)
        legacy_kb.owner_id = owner_id
        legacy_kb.is_public = False
        legacy_kb.invite_code = None
        db.add(ProjectArchiveLibrary(project_id=project_id, kb_id=legacy_kb.id))
        await db.flush()
        await _sync_members(db, legacy_kb, project_id, owner_id)
        await ensure_kb_folder_tree(db, legacy_kb.id, commit=False)
        return legacy_kb

    owner_id = _select_archive_owner(project_members, owner_id)
    project = await db.get(Project, project_id)
    kb = KnowledgeBase(
        name=f"{project.name if project else '项目'}资料库",
        description="项目成员共享的正式工程档案资料库",
        kb_type="shared",
        project_id=project_id,
        owner_id=owner_id,
        is_public=False,
    )
    db.add(kb)
    await db.flush()
    db.add(ProjectArchiveLibrary(project_id=project_id, kb_id=kb.id))
    await db.flush()
    await _sync_members(db, kb, project_id, kb.owner_id)
    await ensure_kb_folder_tree(db, kb.id, commit=False)
    return kb

async def ensure_personal_archive_library(
    db: AsyncSession, owner_id: int
) -> KnowledgeBase:
    """Return the owner's home KB, creating it with the preset tree if needed.

    Business-document archiving intentionally targets the creator's personal
    library (我的资料库 / 工作记录与表单). There is exactly one
    home library per user, so the lookup only uses owner_id + kb_type and never
    a library name.
    """
    kb = (
        await db.execute(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.owner_id == owner_id,
                KnowledgeBase.kb_type == "personal",
            )
            .order_by(KnowledgeBase.id)
        )
    ).scalars().first()
    if kb is None:
        kb = KnowledgeBase(
            name="我的资料库", kb_type="personal", owner_id=owner_id
        )
        db.add(kb)
        await db.flush()

    member = (
        await db.execute(
            select(KnowledgeBaseMember).where(
                KnowledgeBaseMember.kb_id == kb.id,
                KnowledgeBaseMember.user_id == owner_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        db.add(
            KnowledgeBaseMember(kb_id=kb.id, user_id=owner_id, role="owner")
        )
        await db.flush()
    elif member.role != "owner":
        member.role = "owner"
    await ensure_kb_folder_tree(db, kb.id, commit=False)
    return kb


async def sync_project_archive_members(
    db: AsyncSession, project_id: int, actor_id: int | None = None
) -> bool:
    """Apply project membership changes to an existing archive KB.

    Never creates a KB as a side effect of a membership change. Returns whether a
    valid mapped archive library was synchronized.
    """
    mapping = (
        await db.execute(
            select(ProjectArchiveLibrary).where(
                ProjectArchiveLibrary.project_id == project_id
            )
        )
    ).scalar_one_or_none()
    if mapping is None:
        return False
    kb = await db.get(KnowledgeBase, mapping.kb_id)
    if not (
        kb
        and kb.kb_type == "shared"
        and kb.project_id == project_id
        and not kb.is_builtin
    ):
        await db.delete(mapping)
        return False
    await _sync_members(db, kb, project_id, kb.owner_id)
    await ensure_kb_folder_tree(db, kb.id, commit=False)
    return True


async def _target_folder_id(
    db: AsyncSession,
    kb_id: int,
    doc_type: str,
    form_code: str | None = None,
    parent_id: int | None = None,
) -> int:
    """定位归档目录。

    2026-11-27 起结构是「<成员文件夹> / 工作记录与表单 / <类型>」：
    项目资料库根下每个在岗成员一个文件夹，文书只存一份落在创建人自己的
    文件夹里；``parent_id`` 传成员文件夹 id。旧库根上的
    「工作记录与表单 / 类型」（无 parent_id 约束）仍可命中。
    """
    folders = (
        await db.execute(select(KbFolder).where(KbFolder.kb_id == kb_id))
    ).scalars().all()
    by_id = {row.id: row for row in folders}
    target_name = _archive_folder_name(form_code, doc_type)
    candidates = [row for row in folders if row.name == target_name]
    for row in candidates:
        parent = by_id.get(row.parent_id or 0)
        if not parent or parent.name != "工作记录与表单":
            continue
        if parent_id is not None and parent.parent_id != parent_id:
            continue
        return row.id
    raise RuntimeError(f"资料库缺少归档目录：{target_name}")


async def _ensure_document_file(
    db: AsyncSession, doc: Document
) -> Path | None:
    """Return a current DOCX/PDF/Markdown, rendering one for legacy rows when possible."""
    source = _current_source_path(doc)
    if source is not None:
        return source
    if not doc.form_code:
        return None
    try:
        from app.services.form_renderer import render_form

        rendered = await asyncio.to_thread(
            render_form, doc.form_code, dict(doc.content or {})
        )
        doc.docx_path = f"/exports/{rendered.name}"
        doc.pdf_path = None
        doc.signed_pdf_path = None
        doc.archive_sync_status = "pending"
        doc.archive_sync_error = None
        await db.flush()
        return rendered
    except Exception as exc:  # Rendering failure stays pending for an explicit retry.
        doc.archive_sync_status = "pending"
        doc.archive_sync_error = f"文书文件缺失，重新渲染失败：{exc}"[:1000]
        return None


def _is_personal_only_doc(doc: Document) -> bool:
    """监理日记是个人工作记录（原型「仅个人可见」），不进项目资料库。"""
    return (
        doc.doc_type == DocType.SUPERVISION_DIARY.value
        or doc.form_code == "jianli_riji"
    )


async def sync_document_to_project_archive(
    db: AsyncSession, doc: Document, actor: User
) -> KnowledgeDocument | None:
    """把业务文书同步成资料库里的一份文档（**只写一份**）。

    2026-11-27 口径：
      * 普通文书 → 项目资料库 / <创建人文件夹> / 工作记录与表单 / <类型>，
        项目在岗成员都看得到；
      * 监理日记（个人工作记录）仍只进创建人的个人库，不进项目库。
    文档不复制：项目资料库是唯一存储，「我的资料」只是定位到创建人文件夹。
    """
    source = await _ensure_document_file(db, doc)
    if source is None:
        doc.archive_sync_status = "pending"
        doc.archive_sync_error = doc.archive_sync_error or "文书尚无可归档的 DOCX、PDF 或 Markdown 文件"
        return None
    target: Path | None = None
    previous_path: Path | None = None
    try:
        async with db.begin_nested():
            owner_id = doc.creator_id or actor.id
            if _is_personal_only_doc(doc) or not doc.project_id:
                # 个人文书：仍落创建人自己的库（只有本人可见）
                kb = await ensure_personal_archive_library(db, owner_id)
                folder_id = await _target_folder_id(
                    db, kb.id, doc.doc_type, doc.form_code
                )
            else:
                # 项目文书：落项目库 / 创建人文件夹 / 工作记录与表单 / 类型
                kb = await ensure_project_archive_library(db, doc.project_id, actor.id)
                creator = actor if actor.id == owner_id else await db.get(User, owner_id)
                member = await ensure_member_folder(db, kb.id, creator or actor)
                folder_id = await _target_folder_id(
                    db, kb.id, doc.doc_type, doc.form_code, parent_id=member.id
                )
            extension = source.suffix.lower() or ".docx"
            revision = doc.content_revision or 1
            directory = _kb_root() / str(kb.id)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"document_{doc.id}_r{revision}{extension}"
            temporary = directory / f".{target.name}.{uuid.uuid4().hex}.tmp"
            archived = (
                await db.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.source_document_id == doc.id
                    )
                )
            ).scalar_one_or_none()
            previous_path = Path(archived.path) if archived and archived.path else None
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

            filename = f"{doc.title}{extension}"
            if archived is None:
                archived = KnowledgeDocument(
                    kb_id=kb.id,
                    # 上传人 = 文书的创建人（列表里显示「谁生成的」）
                    uploader_id=owner_id,
                    folder_id=folder_id,
                    name=doc.title,
                    filename=filename,
                    path=str(target),
                    mime_type=mimetypes.guess_type(filename)[0],
                    size_bytes=target.stat().st_size,
                    source_document_id=doc.id,
                    source_revision=revision,
                    source_document_ref=f"document:{doc.id}",
                )
                db.add(archived)
                await db.flush()
            else:
                archived.kb_id = kb.id
                archived.uploader_id = owner_id
                archived.folder_id = folder_id
                archived.name = doc.title
                archived.filename = filename
                archived.path = str(target)
                archived.mime_type = mimetypes.guess_type(filename)[0]
                archived.size_bytes = target.stat().st_size
                archived.source_revision = revision
                archived.source_document_ref = f"document:{doc.id}"
                archived.is_deleted = False
                archived.deleted_at = None
            await db.flush()
            kb.updated_at = datetime.now()
            doc.archive_sync_status = "synced"
            doc.archive_sync_error = None
            doc.archive_synced_revision = revision
            doc.archive_kb_document_id = archived.id
            # 归档位置从「个人库」迁到「项目库/成员文件夹」时，旧目录里的文件
            # 不再被任何行引用，直接清掉，避免 user_kb 里堆孤儿。
            if (
                previous_path is not None
                and previous_path != target
                and previous_path.parent != directory
            ):
                try:
                    previous_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return archived
    except Exception as exc:  # Archive failure must not lose the business document.
        # A revision filename is unique, so a failed database transaction cannot
        # leave a half-written replacement for an older archive. Remove only the
        # new orphan; an existing same-revision file was atomically replaced and
        # remains a valid previous copy.
        if target is not None and (previous_path is None or previous_path != target):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        doc.archive_sync_status = "failed"
        doc.archive_sync_error = str(exc)[:1000]
        return None


def _current_source_path(doc: Document) -> Path | None:
    """Return the file that would be archived for the document's current state."""
    return next(
        (
            candidate
            for candidate in (
                _source_path(doc.signed_pdf_path),
                _source_path(doc.pdf_path),
                _source_path(doc.markdown_path),
                _source_path(doc.docx_path),
            )
            if candidate is not None and candidate.exists()
        ),
        None,
    )


async def list_archive_candidates(
    db: AsyncSession, project_id: int
) -> list[Document]:
    """Documents whose current file still needs a project-archive copy.

    只有「已同步 + 版本一致 + 归档行确实在本项目的共享库、且未进回收站」才算
    已归档。2026-11-27 归档目标从个人库改成项目库后，历史行仍留在创建人的
    个人库里（kb_type=personal）——这些必须视为待同步，否则永远迁不过来，
    不同账号看到的份数就会对不上。
    """
    rows = (
        await db.execute(
            select(
                Document,
                KnowledgeBase.kb_type,
                KnowledgeBase.project_id,
                KnowledgeDocument.is_deleted,
            )
            .outerjoin(
                KnowledgeDocument,
                KnowledgeDocument.id == Document.archive_kb_document_id,
            )
            .outerjoin(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.kb_id)
            .where(
                Document.project_id == project_id,
                (Document.docx_path.is_not(None))
                | (Document.pdf_path.is_not(None))
                | (Document.markdown_path.is_not(None))
                | (Document.signed_pdf_path.is_not(None))
                | (Document.form_code.is_not(None)),
            )
            .order_by(Document.id)
        )
    ).all()
    candidates: list[Document] = []
    for doc, kb_type, kb_project_id, archived_deleted in rows:
        # 监理日记本来就只进个人库，不存在「迁到项目库」这回事；
        # 否则它会被永远判成待同步，每次回填都重试一遍。
        if _is_personal_only_doc(doc):
            continue
        revision = doc.content_revision or 1
        archived_in_project = (
            kb_type == "shared"
            and kb_project_id == project_id
            and not archived_deleted
        )
        if (
            doc.archive_sync_status == "synced"
            and doc.archive_kb_document_id
            and doc.archive_synced_revision == revision
            and archived_in_project
        ):
            continue
        if _current_source_path(doc) is None and not doc.form_code:
            continue
        candidates.append(doc)
    return candidates


async def count_archive_candidates(db: AsyncSession, project_id: int) -> int:
    return len(await list_archive_candidates(db, project_id))


async def backfill_project_archive(
    db: AsyncSession, project_id: int, actor: User, limit: int = 200
) -> dict:
    """Archive existing business documents that predate the sync feature.

    Idempotent: each successful call makes the document's status ``synced`` at
    its current revision, so a second call selects nothing. Every document is
    committed independently so one broken file cannot roll back the batch.
    """
    candidates = await list_archive_candidates(db, project_id)
    selected = candidates[: max(1, limit)]
    summary = {
        "total": len(candidates),
        "processed": 0,
        "synced": 0,
        "pending": 0,
        "failed": 0,
        "remaining": max(0, len(candidates) - len(selected)),
        "errors": [],
    }
    for doc in selected:
        doc_id = doc.id
        title = doc.title
        await sync_document_to_project_archive(db, doc, actor)
        status = doc.archive_sync_status
        error = doc.archive_sync_error
        await db.commit()
        summary["processed"] += 1
        if status == "synced":
            summary["synced"] += 1
        elif status == "failed":
            summary["failed"] += 1
            summary["errors"].append(
                {"document_id": doc_id, "title": title, "error": error or ""}
            )
        else:
            summary["pending"] += 1
    return summary


async def is_project_archive_library(db: AsyncSession, kb_id: int) -> bool:
    return bool(
        await db.scalar(
            select(ProjectArchiveLibrary.id).where(
                ProjectArchiveLibrary.kb_id == kb_id
            )
        )
    )


async def detach_project_archive_copy(
    db: AsyncSession, doc_id: int
) -> int:
    """Keep the user-visible archive file while removing the source FK.

    Business-document deletion must not cascade into a project archive that the
    project team manages as a formal record. The stable reference remains
    searchable in the audit trail.
    """
    rows = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_document_id == doc_id
            )
        )
    ).scalars().all()
    for row in rows:
        row.source_document_ref = f"deleted-document:{doc_id}"
        row.source_document_id = None
    return len(rows)
