"""AI 扩展能力 — 任务式报告生成、播客（脚本+TTS）、AI 生图。

对标 ima 的 copilot 任务、播客与 AI 生图：
- 任务模式：结合知识库内容生成结构化报告，可保存为笔记；
- 播客：把内容改写为口播文案并用 TTS 合成音频；
- 生图：调用阿里云百炼文生图（需对应的模型与密钥支持）。
"""
import json
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.knowledge_base import KnowledgeDocument
from app.models.note import Note
from app.models.user import User
from app.routers.auth import get_current_user
from app.routers.knowledge_base import _ensure_builtin_synced, _get_kb, _require_role, _retrieve
from app.services import model_gateway
from app.services.ai_client import ai_client
from app.services.model_config_service import get_runtime_model_config

router = APIRouter()


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    kb_id: Optional[int] = None
    save_note: bool = False
    model: Optional[str] = None


class PodcastRequest(BaseModel):
    text: Optional[str] = Field(default=None, max_length=8000)
    note_id: Optional[int] = None
    kb_id: Optional[int] = None
    question: Optional[str] = Field(default=None, max_length=2000)
    model: Optional[str] = None


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    size: str = "1024*1024"
    model: Optional[str] = None


async def _kb_context(db: AsyncSession, user: User, kb_id: int, question: str) -> str:
    kb = await _get_kb(db, kb_id)
    await _require_role(db, kb, user, "viewer")
    await _ensure_builtin_synced(db, kb)
    docs = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.kb_id == kb_id,
                KnowledgeDocument.is_deleted.is_(None)
                | (KnowledgeDocument.is_deleted == False),  # noqa: E712
            )
        )
    ).scalars().all()
    citations = _retrieve(docs, question, top_k=6)
    return "\n\n".join(f"【{c['name']}】\n{c['snippet']}" for c in citations)


@router.post("/task")
async def run_task(
    req: TaskRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """任务模式：生成结构化报告（可选结合知识库）。"""
    context = ""
    if req.kb_id:
        context = await _kb_context(db, user, req.kb_id, req.prompt)

    system = (
        "你是资深监理工程师，处于任务模式。请根据用户要求"
        "（如提供了知识库资料则必须优先引用）输出结构化、可执行的 Markdown 报告，"
        "包含标题、分点论述与结论；不要编造资料中不存在的数据。"
    )
    user_content = req.prompt
    if context:
        user_content = f"知识库资料：\n{context}\n\n任务要求：{req.prompt}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    try:
        content = await ai_client.chat_completion(
            messages, temperature=0.4, max_tokens=3000, model=req.model,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"生成失败：{exc}") from exc

    title = _derive_title(content, req.prompt)
    note_id = None
    if req.save_note:
        note = Note(user_id=user.id, title=title, content=content, source_kb_id=req.kb_id)
        db.add(note)
        await db.commit()
        await db.refresh(note)
        note_id = note.id
    return {"title": title, "content": content, "note_id": note_id, "used_kb": bool(context)}


def _derive_title(content: str, fallback: str) -> str:
    for line in (content or "").splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned[:80]
    return (fallback or "任务报告")[:80]


def _save_audio(audio: bytes, content_type: str) -> str:
    directory = settings.upload_dir / "media" / "podcast"
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".wav" if "wav" in (content_type or "") else ".mp3"
    filename = f"{uuid.uuid4().hex}{suffix}"
    (directory / filename).write_bytes(audio)
    return f"/uploads/media/podcast/{filename}"


@router.post("/podcast")
async def make_podcast(
    req: PodcastRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把内容改写成口播文案并合成音频。"""
    source = (req.text or "").strip()
    if not source and req.note_id:
        note = await db.get(Note, req.note_id)
        if note and note.user_id == user.id:
            source = note.content or ""
    if not source and req.kb_id and req.question:
        source = await _kb_context(db, user, req.kb_id, req.question)
    if not source:
        raise HTTPException(status_code=400, detail="请提供文本、笔记或知识库问题作为播客素材")

    messages = [
        {
            "role": "system",
            "content": (
                "把下面的内容改写为一段适合朗读的播客口播文案：口语化、连贯、有开场和收尾，"
                "250-500 字，不要出现 Markdown 符号。只输出文案本身。"
            ),
        },
        {"role": "user", "content": source[:6000]},
    ]
    try:
        script = await ai_client.chat_completion(
            messages, temperature=0.6, max_tokens=1200, model=req.model,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"生成文案失败：{exc}") from exc

    try:
        audio, content_type, config = await model_gateway.synthesize_speech(script[:1000])
    except model_gateway.ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=f"语音合成不可用：{exc}") from exc

    return {
        "script": script,
        "audio_url": _save_audio(audio, content_type),
        "model": config.model_id,
        "provider": config.provider,
    }


async def _dashscope_image(prompt: str, size: str, api_key: str, base_url: str) -> bytes:
    """调用阿里云百炼万相 2.7 文生图（multimodal-generation 同步接口），返回图片字节。

    接口域名取自运行时模型配置 base_url 的 origin（兼容 token-plan 与公网域名）；
    旧版异步 text2image 接口在 token-plan 密钥下不可用，已弃用。
    """
    parts = urlsplit(base_url or "")
    base = f"{parts.scheme}://{parts.netloc}/api/v1"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "wan2.7-image",
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": {"size": size, "n": 1, "watermark": False},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(
            base + "/services/aigc/multimodal-generation/generation",
            headers=headers, json=payload,
        )
        if resp.status_code >= 400:
            raise model_gateway.ModelGatewayError(f"生图服务返回 {resp.status_code}: {resp.text[:200]}")
        choices = ((resp.json().get("output") or {}).get("choices")) or []
        image_url = ""
        for choice in choices:
            for item in ((choice.get("message") or {}).get("content")) or []:
                if item.get("image"):
                    image_url = item["image"]
        if not image_url:
            raise model_gateway.ModelGatewayError("生图结果为空")
        img = await client.get(image_url)
        img.raise_for_status()
        return img.content


@router.post("/image")
async def generate_image(
    req: ImageRequest,
    user: User = Depends(get_current_user),
):
    """AI 生图（需要百炼文生图模型与密钥支持）。"""
    config = await get_runtime_model_config("llm")
    if not config.api_key or not config.base_url:
        raise HTTPException(status_code=503, detail="未配置可用的模型密钥，无法生图")
    try:
        data = await _dashscope_image(req.prompt, req.size, config.api_key, config.base_url)
    except model_gateway.ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=f"生图不可用：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"生图失败：{exc}") from exc

    directory = settings.upload_dir / "media" / "ai-image"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    (directory / filename).write_bytes(data)
    return {"image_url": f"/uploads/media/ai-image/{filename}", "prompt": req.prompt}
