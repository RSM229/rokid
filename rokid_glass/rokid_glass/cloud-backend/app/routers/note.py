"""个人笔记路由 — Markdown 笔记的新建/列表/编辑/删除，可一键保存知识库回答。"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.note import Note
from app.models.user import User
from app.routers.auth import get_current_user

router = APIRouter()


class NoteCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    content: str = ""
    source_kb_id: Optional[int] = None


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    content: Optional[str] = None


def _serialize(note: Note, with_content: bool = False) -> dict:
    data = {
        "id": note.id,
        "title": note.title,
        "source_kb_id": note.source_kb_id,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }
    if with_content:
        data["content"] = note.content
    return data


async def _get_note(db: AsyncSession, note_id: int, user: User) -> Note:
    note = await db.get(Note, note_id)
    if note is None or note.user_id != user.id:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return note


@router.get("")
async def list_notes(
    keyword: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Note).where(Note.user_id == user.id)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.where(Note.title.like(like))
    notes = (await db.execute(query.order_by(Note.updated_at.desc()).limit(200))).scalars().all()
    return {"total": len(notes), "items": [_serialize(n) for n in notes]}


@router.post("")
async def create_note(
    req: NoteCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = Note(
        user_id=user.id,
        title=(req.title or "").strip() or "未命名笔记",
        content=req.content or "",
        source_kb_id=req.source_kb_id,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return _serialize(note, with_content=True)


@router.get("/{note_id}")
async def get_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await _get_note(db, note_id, user)
    return _serialize(note, with_content=True)


@router.put("/{note_id}")
async def update_note(
    note_id: int,
    req: NoteUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await _get_note(db, note_id, user)
    if req.title is not None:
        note.title = req.title.strip() or "未命名笔记"
    if req.content is not None:
        note.content = req.content
    await db.commit()
    await db.refresh(note)
    return _serialize(note, with_content=True)


@router.delete("/{note_id}")
async def delete_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await _get_note(db, note_id, user)
    await db.delete(note)
    await db.commit()
    return {"detail": "笔记已删除", "id": note_id}
