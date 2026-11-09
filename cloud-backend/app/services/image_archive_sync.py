"""归档文档 → Document → PDF → 项目资料库 KB 的编排层。

职责边界：
- ``upsert_documents`` 只负责「按 (项目, 类型, 标题) 复用/新建 Document、
  递增 content_revision、生成 PDF」，**不 commit**（由路由统一提交）；
- KB 同步复用既有 ``sync_document_to_project_archive``，在路由 commit 之后执行。

幂等性（截图「改口径后四处联动」的关键）：同一天同一项目重复归档不会新建一堆
文档，而是复用同一条 Document 并出 R2/R3 新版本；KB 里按 source_document_id
更新为最新 revision 的副本。PDF 生成失败只标记该文档，不阻断照片归档主流程。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import DocStatus, Document
from app.models.user import User
from app.services.doc_generator import generate_pdf


async def upsert_documents(db: AsyncSession, specs: list[dict], *,
                           project_id: int, user: User) -> list[dict]:
    """为每份文档规格复用/新建 Document 并生成 PDF。

    返回项含 ``document``（ORM 对象）、``status``（generated/failed）、
    ``pdf_url``、``revision``；仅 generated 的项应交给 KB 同步。
    """
    results: list[dict] = []
    export_dir = Path(settings.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        doc = (await db.execute(
            select(Document)
            .where(Document.project_id == project_id,
                   Document.doc_type == spec["doc_type"],
                   Document.title == spec["title"])
            .order_by(Document.id.desc())
        )).scalars().first()
        if doc is None:
            doc = Document(
                doc_type=spec["doc_type"],
                title=spec["title"],
                content={},  # 拿到真实 revision 后由 render 重算
                project_id=project_id,
                creator_id=user.id,
                status=DocStatus.ARCHIVED.value,
                attached_images=[],
            )
            db.add(doc)
            await db.flush()
            revision = doc.content_revision or 1
            doc.content_revision = revision  # 显式落定，避免依赖服务端 default
        else:
            revision = (doc.content_revision or 1) + 1
            doc.content_revision = revision
            doc.status = DocStatus.ARCHIVED.value
            await db.flush()

        # render(revision) 让正文里的「第 N 版」与 Document.content_revision 一致；
        # 预览阶段用预估版本渲染，落盘时用真实版本重算，不产生编号漂移。
        render = spec.get("render")
        payload = render(revision) if callable(render) else spec
        doc.content = payload["content"]
        await db.flush()

        day = str(spec.get("day") or "00000000")
        day = "".join(ch for ch in day if ch.isdigit())[:8] or "00000000"
        filename = f"image_archive_{project_id}_{day}_{doc.id}_r{revision}.pdf"
        output = export_dir / filename
        try:
            generate_pdf(doc, str(output))
        except Exception as exc:  # 字体/排版异常不应让已完成的照片归档回滚
            output.unlink(missing_ok=True)
            doc.archive_sync_status = "failed"
            doc.archive_sync_error = f"PDF 生成失败：{str(exc)[:500]}"
            await db.flush()
            results.append({
                "title": spec["title"], "doc_type": spec["doc_type"],
                "doc_id": doc.id, "revision": revision,
                "status": "failed", "error": str(exc)[:500],
                "document": doc,
            })
            continue

        doc.pdf_path = f"/exports/{filename}"
        doc.archive_sync_status = "pending"
        doc.archive_sync_error = None
        await db.flush()
        results.append({
            "title": spec["title"], "doc_type": spec["doc_type"],
            "doc_id": doc.id, "revision": revision,
            "status": "generated", "pdf_url": doc.pdf_path,
            "document": doc,
        })
    return results


async def sync_to_archive_kb(db: AsyncSession, results: list[dict], user: User) -> list[dict]:
    """把已生成 PDF 的文档同步进项目资料库 KB（失败不抛，逐条记录状态）。"""
    from app.services.project_archive import sync_document_to_project_archive

    output: list[dict] = []
    for item in results:
        if item["status"] != "generated":
            output.append({k: v for k, v in item.items() if k != "document"})
            continue
        try:
            archived = await sync_document_to_project_archive(db, item["document"], user)
            doc = item["document"]
            output.append({
                **{k: v for k, v in item.items() if k != "document"},
                "kb_sync_status": getattr(doc, "archive_sync_status", None),
                "kb_sync_error": getattr(doc, "archive_sync_error", None),
                "kb_document_id": getattr(archived, "id", None),
            })
        except Exception as exc:
            output.append({
                **{k: v for k, v in item.items() if k != "document"},
                "kb_sync_status": "failed",
                "kb_sync_error": str(exc)[:500],
            })
    return output
