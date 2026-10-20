"""个人/共享知识库路由 — 多库、成员权限、文档管理与按库问答。

对标 ima 的核心能力：
- 个人知识库 / 共享知识库（多库）；
- 共享库成员角色：owner / admin / editor / viewer；
- 文档上传与解析、库内检索；
- 基于知识库的 LLM 问答并返回引用来源。
"""
import asyncio
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.knowledge_base import (
    KB_ROLE_LEVEL,
    KB_ROLES,
    KnowledgeBase,
    KnowledgeBaseMember,
    KnowledgeDocument,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.services import model_gateway
from app.services.ai_client import ai_client
from app.services.knowledge_service import (
    _extract_docx_text,
    _extract_pdf_text,
    _extract_snippet,
    _extract_xlsx_text,
    iter_knowledge_documents,
)
from app.services.media_validation import assert_valid_audio
from app.services.text_clean import strip_markdown

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_DOC_EXTS = {".docx", ".xlsx", ".pdf", ".doc", ".xls", ".txt", ".md"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".webm", ".ogg", ".amr"}


def _kb_root() -> Path:
    root = settings.knowledge_base_path / "user_kb"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _reindex_knowledge_docs() -> None:
    """把新上传的知识库文档增量编进检索索引。

    走 FastAPI 的 BackgroundTasks（同步函数 → 线程池），所以不阻塞上传响应；
    ``wait=True`` 保证队列里那次刷新一定执行到，而不是和后台守护撞上就放弃 ——
    用户上传完立刻搜索是常态，不能让文档「暂时检索不到」。
    """
    try:
        from app.services import rag_index

        report = rag_index.refresh(only=[rag_index.SOURCE_TYPE_KNOWLEDGE], wait=True)
        logger.info(
            "[RAG] 上传后索引刷新：%s（更新 %s 个来源）",
            report.get("status"), report.get("indexed"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RAG] 上传后索引刷新失败：%s", exc)


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx_text(path)
    if suffix == ".xlsx":
        return _extract_xlsx_text(path)
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    if suffix in {".txt", ".md"}:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
    return ""


async def _get_kb(db: AsyncSession, kb_id: int) -> KnowledgeBase:
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


def _reject_builtin(kb: KnowledgeBase, action: str = "修改") -> None:
    if kb.is_builtin:
        raise HTTPException(status_code=403, detail=f"系统内置知识库不支持{action}")


async def _get_member(db: AsyncSession, kb_id: int, user_id: int) -> Optional[KnowledgeBaseMember]:
    result = await db.execute(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.kb_id == kb_id,
            KnowledgeBaseMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _require_role(
    db: AsyncSession, kb: KnowledgeBase, user: User, min_role: str
) -> KnowledgeBaseMember:
    member = await _get_member(db, kb.id, user.id)
    if member is None:
        if kb.is_builtin:
            # 内置知识库对所有登录用户开放为“普通成员”
            return KnowledgeBaseMember(kb_id=kb.id, user_id=user.id, role="viewer")
        raise HTTPException(status_code=403, detail="您不是该知识库成员")
    if KB_ROLE_LEVEL.get(member.role, 0) < KB_ROLE_LEVEL[min_role]:
        raise HTTPException(status_code=403, detail="当前角色权限不足")
    return member


async def _ensure_builtin_synced(db: AsyncSession, kb: KnowledgeBase) -> None:
    """内置知识库的文档来自规范库文件，按需同步到 knowledge_documents。"""
    if not kb.is_builtin:
        return
    sources = {d["filename"]: d for d in iter_knowledge_documents()}
    existing = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb.id))
    ).scalars().all()
    existing_by_name = {doc.filename: doc for doc in existing}
    changed = False
    for filename, src in sources.items():
        if filename in existing_by_name:
            continue
        path = Path(src["path"])
        size = path.stat().st_size if path.exists() else 0
        db.add(
            KnowledgeDocument(
                kb_id=kb.id,
                uploader_id=kb.owner_id,
                name=src["name"],
                filename=filename,
                path=str(path),
                mime_type=None,
                size_bytes=size,
            )
        )
        changed = True
    for filename, doc in existing_by_name.items():
        if filename not in sources:
            await db.delete(doc)
            changed = True
    if changed:
        await db.commit()


def _user_info(user: User | None) -> dict:
    if user is None:
        return {"id": 0, "username": "", "real_name": "未知", "role": ""}
    return {
        "id": user.id,
        "username": user.username,
        "real_name": user.real_name,
        "role": user.role,
    }


def _serialize_doc(doc: KnowledgeDocument, uploader: User | None = None) -> dict:
    info = _user_info(uploader)
    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "name": doc.name,
        "filename": doc.filename,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "uploader_id": doc.uploader_id,
        "uploader_name": info["real_name"],
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)
    kb_type: str = Field(default="personal")
    project_id: Optional[int] = None


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)


class MemberAdd(BaseModel):
    username: Optional[str] = None
    user_id: Optional[int] = None
    role: str = "viewer"


class MemberRoleUpdate(BaseModel):
    role: str


class JoinRequest(BaseModel):
    invite_code: str


class PublishRequest(BaseModel):
    is_public: bool


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    use_llm: bool = True


# ============ 知识库管理 ============


@router.get("")
async def list_knowledge_bases(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出我参与的知识库（我创建的 + 我加入的 + 系统内置）。"""
    result = await db.execute(
        select(KnowledgeBase, KnowledgeBaseMember.role)
        .join(KnowledgeBaseMember, KnowledgeBaseMember.kb_id == KnowledgeBase.id)
        .where(KnowledgeBaseMember.user_id == user.id)
    )
    entries: dict[int, tuple[KnowledgeBase, str]] = {kb.id: (kb, role) for kb, role in result.all()}

    builtins = (
        await db.execute(select(KnowledgeBase).where(KnowledgeBase.is_builtin.is_(True)))
    ).scalars().all()
    for kb in builtins:
        if kb.id not in entries:
            entries[kb.id] = (kb, "viewer")

    doc_counts = dict(
        (await db.execute(
            select(KnowledgeDocument.kb_id, func.count(KnowledgeDocument.id)).group_by(
                KnowledgeDocument.kb_id
            )
        )).all()
    )
    member_counts = dict(
        (await db.execute(
            select(KnowledgeBaseMember.kb_id, func.count(KnowledgeBaseMember.id)).group_by(
                KnowledgeBaseMember.kb_id
            )
        )).all()
    )

    items = [
        {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "kb_type": kb.kb_type,
            "owner_id": kb.owner_id,
            "role": role,
            "is_owner": kb.owner_id == user.id,
            "is_builtin": bool(kb.is_builtin),
            "is_public": bool(kb.is_public),
            "document_count": int(doc_counts.get(kb.id, 0)),
            "member_count": int(member_counts.get(kb.id, 0)),
            "created_at": kb.created_at.isoformat() if kb.created_at else None,
            "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
        }
        for kb, role in entries.values()
    ]
    items.sort(key=lambda item: item["updated_at"] or "", reverse=True)
    items.sort(key=lambda item: 0 if item["is_builtin"] else 1)
    return {"items": items}


@router.post("")
async def create_knowledge_base(
    req: KnowledgeBaseCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.kb_type not in {"personal", "shared"}:
        raise HTTPException(status_code=400, detail="kb_type 仅支持 personal/shared")
    kb = KnowledgeBase(
        name=req.name.strip(),
        description=(req.description or "").strip() or None,
        kb_type=req.kb_type,
        owner_id=user.id,
        project_id=req.project_id if req.kb_type == "shared" else None,
        invite_code=uuid.uuid4().hex[:8].upper() if req.kb_type == "shared" else None,
    )
    db.add(kb)
    await db.flush()
    db.add(KnowledgeBaseMember(kb_id=kb.id, user_id=user.id, role="owner"))
    await db.commit()
    await db.refresh(kb)
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "kb_type": kb.kb_type,
        "role": "owner",
        "invite_code": kb.invite_code,
    }


@router.get("/rag/stats")
async def rag_stats(user: User = Depends(get_current_user)):
    """检索索引健康度自检 —— 索引块数、各来源分布、嵌入模型是否可用。

    放在 ``/{kb_id}`` 之前：静态路径优先注册，避免被动态段吃掉。三段路径
    本身也与两段的 ``/{kb_id}`` 不冲突，这里是显式表意。
    """
    from app.services import rag_index

    try:
        info = await asyncio.to_thread(rag_index.stats)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"索引自检失败：{exc}") from exc
    return info


@router.get("/plaza")
async def list_plaza(
    keyword: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """知识库广场：列出所有已公开的知识库，供发现与订阅。"""
    query = select(KnowledgeBase).where(KnowledgeBase.is_public.is_(True))
    if keyword:
        query = query.where(KnowledgeBase.name.like(f"%{keyword.strip()}%"))
    kbs = (
        await db.execute(query.order_by(KnowledgeBase.updated_at.desc()).limit(100))
    ).scalars().all()

    my_roles = dict(
        (await db.execute(
            select(KnowledgeBaseMember.kb_id, KnowledgeBaseMember.role).where(
                KnowledgeBaseMember.user_id == user.id
            )
        )).all()
    )
    doc_counts = dict(
        (await db.execute(
            select(KnowledgeDocument.kb_id, func.count(KnowledgeDocument.id)).group_by(
                KnowledgeDocument.kb_id
            )
        )).all()
    )
    member_counts = dict(
        (await db.execute(
            select(KnowledgeBaseMember.kb_id, func.count(KnowledgeBaseMember.id)).group_by(
                KnowledgeBaseMember.kb_id
            )
        )).all()
    )
    owner_ids = {kb.owner_id for kb in kbs}
    owners = {
        u.id: u.real_name
        for u in (
            await db.execute(select(User).where(User.id.in_(owner_ids or {0})))
        ).scalars().all()
    }
    return {
        "items": [
            {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "owner_id": kb.owner_id,
                "owner_name": owners.get(kb.owner_id, ""),
                "is_builtin": bool(kb.is_builtin),
                "document_count": int(doc_counts.get(kb.id, 0)),
                "member_count": int(member_counts.get(kb.id, 0)),
                "joined": kb.id in my_roles,
                "my_role": my_roles.get(kb.id),
                "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
            }
            for kb in kbs
        ]
    }


@router.post("/{kb_id}/publish")
async def publish_knowledge_base(
    kb_id: int,
    req: PublishRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把共享知识库发布/取消发布到广场（需管理员）。"""
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "admin")
    _reject_builtin(kb, "修改发布状态")
    if req.is_public and kb.kb_type != "shared":
        raise HTTPException(status_code=400, detail="仅共享知识库可发布到广场")
    kb.is_public = bool(req.is_public)
    await db.commit()
    return {"id": kb.id, "is_public": kb.is_public}


@router.post("/{kb_id}/subscribe")
async def subscribe_knowledge_base(
    kb_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """订阅公开知识库（内置库或已发布到广场的共享库）。"""
    kb = await _get_kb(db, kb_id)
    if not (kb.is_public or kb.is_builtin):
        raise HTTPException(status_code=403, detail="该知识库未公开，无法订阅")
    existing = await _get_member(db, kb.id, user.id)
    if existing:
        return {"detail": "已订阅该知识库", "kb_id": kb.id, "role": existing.role}
    db.add(KnowledgeBaseMember(kb_id=kb.id, user_id=user.id, role="viewer"))
    await db.commit()
    return {"detail": "订阅成功", "kb_id": kb.id, "role": "viewer"}


@router.get("/{kb_id}")
async def get_knowledge_base(
    kb_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    member = await _require_role(db, kb, user, "viewer")
    await _ensure_builtin_synced(db, kb)
    document_count = (
        await db.execute(
            select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.kb_id == kb_id)
        )
    ).scalar() or 0
    member_count = (
        await db.execute(
            select(func.count(KnowledgeBaseMember.id)).where(KnowledgeBaseMember.kb_id == kb_id)
        )
    ).scalar() or 0
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "kb_type": kb.kb_type,
        "owner_id": kb.owner_id,
        "role": member.role,
        "is_owner": kb.owner_id == user.id,
        "is_builtin": bool(kb.is_builtin),
        "is_public": bool(kb.is_public),
        "invite_code": kb.invite_code if KB_ROLE_LEVEL[member.role] >= 2 else None,
        "document_count": int(document_count),
        "member_count": int(member_count),
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


@router.put("/{kb_id}")
async def update_knowledge_base(
    kb_id: int,
    req: KnowledgeBaseUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "admin")
    _reject_builtin(kb)
    if req.name is not None:
        kb.name = req.name.strip()
    if req.description is not None:
        kb.description = req.description.strip() or None
    await db.commit()
    return {"id": kb.id, "name": kb.name, "description": kb.description}


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    if kb.owner_id != user.id:
        raise HTTPException(status_code=403, detail="仅创建者可删除知识库")
    _reject_builtin(kb, "删除")
    docs = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id))
    ).scalars().all()
    await db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id))
    await db.execute(delete(KnowledgeBaseMember).where(KnowledgeBaseMember.kb_id == kb_id))
    await db.delete(kb)
    await db.commit()
    for doc in docs:
        try:
            Path(doc.path).unlink(missing_ok=True)
        except OSError:
            pass
    directory = _kb_root() / str(kb_id)
    if directory.exists():
        for item in directory.glob("*"):
            try:
                item.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
    return {"detail": "知识库已删除", "id": kb_id}


# ============ 成员与邀请 ============


@router.get("/{kb_id}/members")
async def list_members(
    kb_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "viewer")
    rows = (
        await db.execute(
            select(KnowledgeBaseMember, User)
            .join(User, User.id == KnowledgeBaseMember.user_id)
            .where(KnowledgeBaseMember.kb_id == kb_id)
            .order_by(KnowledgeBaseMember.created_at)
        )
    ).all()
    return {
        "items": [
            {
                "user_id": m.user_id,
                "role": m.role,
                "real_name": u.real_name,
                "username": u.username,
                "project_id": u.project_id,
                "joined_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m, u in rows
        ]
    }


@router.post("/{kb_id}/members")
async def add_member(
    kb_id: int,
    req: MemberAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    actor = await _require_role(db, kb, user, "admin")
    _reject_builtin(kb)
    if req.role not in {"admin", "editor", "viewer"}:
        raise HTTPException(status_code=400, detail="角色仅支持 admin/editor/viewer")

    target = None
    if req.user_id is not None:
        target = await db.get(User, req.user_id)
    elif req.username:
        target = (
            await db.execute(select(User).where(User.username == req.username.strip()))
        ).scalar_one_or_none()
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="用户不存在或已禁用")

    existing = await _get_member(db, kb_id, target.id)
    if existing:
        raise HTTPException(status_code=409, detail="该用户已是知识库成员")

    db.add(KnowledgeBaseMember(kb_id=kb_id, user_id=target.id, role=req.role))
    kb.updated_at = datetime.now()
    await db.commit()
    return {"detail": "成员已添加", "user_id": target.id, "role": req.role}


@router.put("/{kb_id}/members/{member_user_id}")
async def update_member_role(
    kb_id: int,
    member_user_id: int,
    req: MemberRoleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    actor = await _require_role(db, kb, user, "admin")
    _reject_builtin(kb)
    if req.role not in {"admin", "editor", "viewer"}:
        raise HTTPException(status_code=400, detail="角色仅支持 admin/editor/viewer")
    member = await _get_member(db, kb_id, member_user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    if member.role == "owner":
        raise HTTPException(status_code=400, detail="不能修改创建者角色")
    # 仅创建者可将成员设为 admin
    if req.role == "admin" and KB_ROLE_LEVEL[actor.role] < KB_ROLE_LEVEL["owner"]:
        raise HTTPException(status_code=403, detail="仅创建者可设置管理员")
    member.role = req.role
    await db.commit()
    return {"user_id": member_user_id, "role": member.role}


@router.delete("/{kb_id}/members/{member_user_id}")
async def remove_member(
    kb_id: int,
    member_user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    actor = await _require_role(db, kb, user, "admin")
    _reject_builtin(kb)
    member = await _get_member(db, kb_id, member_user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    if member.role == "owner":
        raise HTTPException(status_code=400, detail="不能移除创建者")
    if member.role == "admin" and KB_ROLE_LEVEL[actor.role] < KB_ROLE_LEVEL["owner"]:
        raise HTTPException(status_code=403, detail="仅创建者可移除管理员")
    # 允许成员自行退出
    if member_user_id != user.id and KB_ROLE_LEVEL[actor.role] < KB_ROLE_LEVEL["admin"]:
        raise HTTPException(status_code=403, detail="权限不足")
    await db.delete(member)
    await db.commit()
    return {"detail": "成员已移除", "user_id": member_user_id}


@router.post("/{kb_id}/invite")
async def get_invite(
    kb_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "admin")
    _reject_builtin(kb)
    if kb.kb_type != "shared":
        raise HTTPException(status_code=400, detail="仅共享知识库支持邀请")
    if not kb.invite_code:
        kb.invite_code = uuid.uuid4().hex[:8].upper()
        await db.commit()
    return {"invite_code": kb.invite_code}


@router.post("/join/{invite_code}")
async def join_knowledge_base(
    invite_code: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = (
        await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.invite_code == invite_code.strip().upper())
        )
    ).scalar_one_or_none()
    if kb is None or kb.kb_type != "shared":
        raise HTTPException(status_code=404, detail="邀请码无效")
    existing = await _get_member(db, kb.id, user.id)
    if existing:
        return {"detail": "已加入该知识库", "kb_id": kb.id, "role": existing.role}
    db.add(KnowledgeBaseMember(kb_id=kb.id, user_id=user.id, role="viewer"))
    await db.commit()
    return {"detail": "已加入知识库", "kb_id": kb.id, "role": "viewer"}


# ============ 文档 ============


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: int,
    keyword: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "viewer")
    await _ensure_builtin_synced(db, kb)
    query = select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.where(KnowledgeDocument.name.like(like))
    docs = (await db.execute(query.order_by(KnowledgeDocument.created_at.desc()))).scalars().all()
    uploaders = {
        u.id: u
        for u in (
            await db.execute(
                select(User).where(User.id.in_({d.uploader_id for d in docs} or {0}))
            )
        ).scalars().all()
    }
    return {"total": len(docs), "items": [_serialize_doc(d, uploaders.get(d.uploader_id)) for d in docs]}


@router.post("/{kb_id}/documents")
async def upload_document(
    kb_id: int,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "editor")
    _reject_builtin(kb, "上传")
    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择文件")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_DOC_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{suffix or '未知'}")

    data = await file.read(settings.max_document_bytes + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > settings.max_document_bytes:
        raise HTTPException(status_code=413, detail="文件超过大小限制")

    directory = _kb_root() / str(kb_id)
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    target = directory / stored_name
    target.write_bytes(data)

    doc = KnowledgeDocument(
        kb_id=kb_id,
        uploader_id=user.id,
        name=(name or Path(file.filename).stem).strip() or Path(file.filename).stem,
        filename=file.filename,
        path=str(target),
        mime_type=file.content_type,
        size_bytes=len(data),
    )
    db.add(doc)
    kb.updated_at = datetime.now()
    await db.commit()
    await db.refresh(doc)
    # 上传即可检索：把这份文档增量编进 RAG 索引（响应之后在后台线程执行）
    background.add_task(_reindex_knowledge_docs)
    return _serialize_doc(doc, user)


@router.post("/{kb_id}/documents/audio")
async def upload_audio_transcript(
    kb_id: int,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    language: str = Form("zh"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传录音/音频，语音转写为文本文档入库。"""
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "editor")
    if kb.is_builtin:
        raise HTTPException(status_code=403, detail="系统内置知识库不支持上传")
    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择音频文件")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in AUDIO_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的音频类型：{suffix or '未知'}")

    data = await file.read(settings.max_audio_bytes + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail="音频超过大小限制")
    assert_valid_audio(data, suffix)

    try:
        result = await model_gateway.transcribe_audio(
            data,
            filename=file.filename,
            content_type=file.content_type or "application/octet-stream",
            language=language,
        )
    except model_gateway.ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=f"语音识别不可用：{exc}") from exc

    text = (result.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="未识别到语音内容，请重试或更换音频")

    directory = _kb_root() / str(kb_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{uuid.uuid4().hex}.txt"
    payload = text.encode("utf-8")
    target.write_bytes(payload)

    doc_name = (name or f"录音转写_{datetime.now().strftime('%Y%m%d_%H%M%S')}").strip()
    doc = KnowledgeDocument(
        kb_id=kb_id,
        uploader_id=user.id,
        name=doc_name,
        filename=f"{doc_name}.txt",
        path=str(target),
        mime_type="text/plain",
        size_bytes=len(payload),
    )
    db.add(doc)
    kb.updated_at = datetime.now()
    await db.commit()
    await db.refresh(doc)
    return {**_serialize_doc(doc, user), "text": text, "duration": result.get("duration", 0)}


async def _get_doc(db: AsyncSession, kb_id: int, doc_id: int) -> KnowledgeDocument:
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.delete("/{kb_id}/documents/{doc_id}")
async def delete_document(
    kb_id: int,
    doc_id: int,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    member = await _require_role(db, kb, user, "editor")
    _reject_builtin(kb, "删除文档")
    doc = await _get_doc(db, kb_id, doc_id)
    if doc.uploader_id != user.id and KB_ROLE_LEVEL[member.role] < KB_ROLE_LEVEL["admin"]:
        raise HTTPException(status_code=403, detail="仅上传者或管理员可删除该文档")
    # 先取出路径：commit 后对象进入 deleted 状态，再访问属性会触发刷新查询而报错
    doc_path = doc.path
    await db.delete(doc)
    await db.commit()
    try:
        Path(doc_path).unlink(missing_ok=True)
    except OSError:
        pass
    # 与上传对称：删掉的分块必须尽快回收，否则检索会继续召回已删除的文档内容
    background.add_task(_reindex_knowledge_docs)
    return {"detail": "文档已删除", "id": doc_id}


@router.get("/{kb_id}/documents/{doc_id}/content")
async def get_document_content(
    kb_id: int,
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "viewer")
    doc = await _get_doc(db, kb_id, doc_id)
    text = _extract_text(Path(doc.path))
    return {"id": doc.id, "name": doc.name, "filename": doc.filename, "content": text}


@router.get("/{kb_id}/documents/{doc_id}/download")
async def download_document(
    kb_id: int,
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "viewer")
    doc = await _get_doc(db, kb_id, doc_id)
    path = Path(doc.path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path=str(path), filename=doc.filename)


@router.get("/{kb_id}/search")
async def search_documents(
    kb_id: int,
    keyword: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "viewer")
    await _ensure_builtin_synced(db, kb)
    docs = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id))
    ).scalars().all()
    needle = keyword.strip()
    results = []
    matched_ids: set[int] = set()

    # 1) 文件名命中优先（用户直接搜文档名时最符合预期）
    if needle:
        for doc in docs:
            if needle.lower() in (doc.name or "").lower():
                results.append({**_serialize_doc(doc), "snippet": "", "match": "name"})
                matched_ids.add(doc.id)

    # 2) 内容检索：RAG 语义检索优先，索引不可用时回退子串匹配
    if needle:
        rag_hits = _retrieve_by_rag(docs, needle, top_k=max(len(docs), 10))
        if rag_hits is not None:
            by_id = {doc.id: doc for doc in docs}
            for hit in rag_hits:
                if hit["id"] in matched_ids:
                    continue
                doc = by_id.get(hit["id"])
                if doc is None:
                    continue
                results.append({
                    **_serialize_doc(doc),
                    "snippet": hit["snippet"],
                    "match": "content",
                    "score": hit["score"],
                })
                matched_ids.add(hit["id"])
        else:
            for doc in docs:
                if doc.id in matched_ids:
                    continue
                text = _extract_text(Path(doc.path))
                if needle in text:
                    results.append({
                        **_serialize_doc(doc),
                        "snippet": _extract_snippet(text, needle),
                        "match": "content",
                    })
                    matched_ids.add(doc.id)

    return {"keyword": keyword, "total": len(results), "results": results}


# ============ 按库问答（带引用） ============


def _query_terms(question: str) -> list[str]:
    terms = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", question)
    extra: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", question):
        if len(run) > 2:
            extra.extend(run[i : i + 2] for i in range(len(run) - 1))
    return list(dict.fromkeys(terms + extra))[:24]


def _retrieve_by_rag(
    docs: list[KnowledgeDocument], question: str, top_k: int
) -> list[dict] | None:
    """RAG 混合检索召回。

    返回 None 表示「RAG 不可用或索引未构建」，调用方应回退关键词检索；
    返回 [] 表示「索引可用但确实没有相关内容」。
    """
    try:
        from app.services import rag_index
    except Exception:  # noqa: BLE001
        return None

    try:
        hits = rag_index.search(
            question,
            top_k=max(top_k * 3, 10),
            source_types=[rag_index.SOURCE_TYPE_KNOWLEDGE],
        )
    except Exception:  # noqa: BLE001
        return None

    if not hits and not rag_index.has_index():
        return None

    by_filename = {doc.filename: doc for doc in docs if doc.filename}
    by_stem = {Path(doc.filename).stem: doc for doc in docs if doc.filename}

    results: list[dict] = []
    seen: set[int] = set()
    for hit in hits:
        filename = Path(hit["source_id"]).name
        doc = by_filename.get(filename) or by_stem.get(hit["source_name"])
        if doc is None or doc.id in seen:
            continue
        seen.add(doc.id)
        results.append({
            "id": doc.id,
            "name": doc.name,
            "score": hit["score"],
            "snippet": hit["snippet"],
            "matched_by": hit["matched_by"],
        })
        if len(results) >= top_k:
            break
    return results


def _retrieve_by_terms(
    docs: list[KnowledgeDocument], question: str, top_k: int
) -> list[dict]:
    """关键词词频召回（RAG 不可用时的兜底实现）。"""
    terms = _query_terms(question)
    scored = []
    for doc in docs:
        text = _extract_text(Path(doc.path))
        if not text:
            continue
        score = 0
        first_term = ""
        for term in terms:
            count = text.count(term)
            if count:
                score += count
                if not first_term:
                    first_term = term
        if score > 0:
            snippet = _extract_snippet(text, first_term, context=60) if first_term else text[:160]
            scored.append((score, doc, snippet))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {"id": doc.id, "name": doc.name, "score": score, "snippet": snippet}
        for score, doc, snippet in scored[:top_k]
    ]


def _retrieve(docs: list[KnowledgeDocument], question: str, top_k: int = 4) -> list[dict]:
    """按库问答的召回：优先 RAG 语义检索，不可用时回退关键词词频。"""
    semantic = _retrieve_by_rag(docs, question, top_k)
    if semantic is not None:
        return semantic
    return _retrieve_by_terms(docs, question, top_k)


@router.post("/{kb_id}/query")
async def query_knowledge_base(
    kb_id: int,
    req: QueryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "viewer")
    await _ensure_builtin_synced(db, kb)
    docs = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id))
    ).scalars().all()
    citations = _retrieve(docs, req.question)

    if not req.use_llm:
        return {
            "question": req.question,
            "answer": None,
            "citations": citations,
            "source": "search_only",
        }

    if not citations:
        return {
            "question": req.question,
            "answer": "知识库中未检索到与该问题相关的内容，请尝试更换关键词，或先上传相关资料。",
            "citations": [],
            "source": "no_context",
        }

    context = "\n\n".join(
        f"【{c['name']}】\n{c['snippet']}" for c in citations
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是知识库问答助手。请严格根据提供的知识库片段回答问题，"
                "不要编造；回答末尾用 [文档名] 标注引用来源。若片段不足以回答，请说明。"
                "使用纯文本回答，禁止 Markdown 标记（#、*、代码块等），段落间只用单个换行。"
            ),
        },
        {
            "role": "user",
            "content": f"知识库片段：\n{context}\n\n问题：{req.question}\n\n请作答：",
        },
    ]
    try:
        answer = await ai_client.chat_completion(messages, temperature=0.2, max_tokens=1200)
        source = "llm"
    except Exception as exc:  # noqa: BLE001
        answer = None
        source = "llm_error"
        citations_error = str(exc)
    else:
        citations_error = None

    payload = {
        "question": req.question,
        "answer": strip_markdown(answer) if answer else answer,
        "citations": citations,
        "source": source,
    }
    if citations_error:
        payload["error"] = citations_error
    return payload
