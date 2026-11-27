"""Agent 读取工具：看图片像素、读资料库正文、检索资料库（2026-11-27）。

回答前由 agent 自己决定要不要看：服务端只负责取文件、鉴权、截断。
* 图片：走通用多模态模型 `observe_image`，可以带一个具体问题（不是只拿预先
  算好的通用描述）。
* 文档：按知识库成员权限校验后抽正文，可按问题挑相关段落。
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import model_gateway

MAX_DOC_CHARS = 6000
MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass
class EvidenceImage:
    index: int
    url: str

    @property
    def local_path(self) -> Path | None:
        path = str(self.url or "").split("?", 1)[0]
        marker = "/uploads/"
        if marker not in path:
            return None
        rel = path.split(marker, 1)[1]
        candidate = Path(settings.upload_dir) / rel
        return candidate if candidate.exists() else None


def image_evidence(images: Sequence[str]) -> list[EvidenceImage]:
    out: list[EvidenceImage] = []
    for index, url in enumerate(images or [], start=1):
        text = str(url or "").strip()
        if text:
            out.append(EvidenceImage(index=index, url=text))
    return out


async def view_image(image: EvidenceImage, question: str) -> dict[str, Any]:
    """看一张图片的像素，按 agent 的问题回答。"""
    path = image.local_path
    if path is None:
        return {"ok": False, "error": "这张图不是本服务上传的图片，读不到像素"}
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        return {"ok": False, "error": f"读取图片失败：{type(exc).__name__}"}
    if not data or len(data) > MAX_IMAGE_BYTES:
        return {"ok": False, "error": "图片为空或过大"}
    prompt = (
        "你是一名工程监理，正在看一张现场照片。只回答被问到的内容，"
        "描述可见事实（构件、工序、人员、机械、防护、材料、明显缺陷），"
        "不要重复水印文字，不要编造看不见的东西。\n"
        f"问题：{question or '这张照片里在做什么？有哪些与质量安全相关的可见事实？'}"
    )
    try:
        text = await model_gateway.observe_image(
            data, content_type="image/jpeg", prompt=prompt,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"图片理解不可用：{type(exc).__name__}"}
    return {"ok": True, "observation": (text or "").strip()[:1500]}


def _paragraph_score(paragraph: str, question: str) -> int:
    if not question:
        return 0
    score = 0
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", question):
        if token in paragraph:
            score += 1
    return score


def _excerpt(text: str, question: str) -> str:
    """按问题挑相关段落；没有问题就按原顺序截断。"""
    body = (text or "").strip()
    if not body:
        return ""
    if not question:
        return body[:MAX_DOC_CHARS]
    paragraphs = [p.strip() for p in re.split(r"\n{1,}", body) if p.strip()]
    ranked = sorted(
        enumerate(paragraphs),
        key=lambda pair: (-_paragraph_score(pair[1], question), pair[0]),
    )
    picked: list[tuple[int, str]] = []
    used = 0
    for index, paragraph in ranked:
        if used >= MAX_DOC_CHARS:
            break
        if _paragraph_score(paragraph, question) == 0 and picked:
            continue
        picked.append((index, paragraph))
        used += len(paragraph)
    if not picked:
        return body[:MAX_DOC_CHARS]
    picked.sort(key=lambda pair: pair[0])
    return "\n".join(paragraph for _, paragraph in picked)[:MAX_DOC_CHARS]


async def read_document(
    db: AsyncSession, user, doc_id: int, question: str = ""
) -> dict[str, Any]:
    """按权限读一份资料正文。"""
    from app.models.knowledge_base import KnowledgeDocument
    from app.routers.knowledge_base import _extract_text
    from app.services import kb_search

    doc = await db.get(KnowledgeDocument, int(doc_id))
    if doc is None or getattr(doc, "is_deleted", False):
        return {"ok": False, "error": f"资料 {doc_id} 不存在"}
    try:
        allowed = await kb_search.accessible_kb_ids(db, int(user.id))
    except Exception:  # noqa: BLE001
        allowed = set()
    if int(doc.kb_id) not in {int(item) for item in allowed}:
        return {"ok": False, "error": "没有这份资料的查看权限"}
    path = Path(str(doc.path or ""))
    if not path.exists():
        return {"ok": False, "error": "资料文件不存在或尚未同步"}
    try:
        text = await asyncio.to_thread(_extract_text, path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"解析失败：{type(exc).__name__}"}
    if not text.strip():
        return {"ok": False, "error": "这份资料没有可提取的文字（可能是扫描件）"}
    return {
        "ok": True,
        "name": doc.name,
        "chars": len(text),
        "text": _excerpt(text, question),
    }


async def search_kb(
    db: AsyncSession, user, project_id: int | None, query: str, limit: int = 6
) -> dict[str, Any]:
    from app.services import kb_search

    result = await kb_search.search(db, int(user.id), query, limit=limit, project_id=project_id)
    docs = [
        {
            "id": int(item.get("id") or 0),
            "name": item.get("name") or "",
            "kb": item.get("kb_name") or "",
            "folder": item.get("folder_path") or "",
        }
        for item in (result.get("documents") or [])[:limit]
        if item.get("id")
    ]
    folders = [
        {"name": item.get("name") or "", "path": item.get("path") or "", "count": item.get("count") or 0}
        for item in (result.get("folders") or [])[:4]
    ]
    return {"ok": True, "documents": docs, "folders": folders}
