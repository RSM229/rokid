"""电子签章路由 - 签章上传、管理、文书签署

作者: cloud-backend
创建日期: 2026-07-18
"""
import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.document import Document, DocStatus
from app.models.signature import Signature
from app.models.user import User
from app.routers.auth import get_current_user, require_permission, require_project_id
from app.services.passwords import pwd_context
from app.services.signature_service import embed_signature_to_pdf, _resolve_file_path

router = APIRouter()


class SignDocumentRequest(BaseModel):
    signature_id: int
    password: str
    slot: Optional[str] = None
    position: str = "bottom-right"  # bottom-right / bottom-left / top-right / top-left


class DeleteSignatureRequest(BaseModel):
    password: str


class SignatureResponse(BaseModel):
    id: int
    name: str
    role: Optional[str]
    image_path: str
    is_active: bool
    created_at: str


def _save_signature_image(image_bytes: bytes, user_id: int, filename: str) -> str:
    """保存签章图片到上传目录，返回相对 URL"""
    ext = Path(filename).suffix.lower() or ".png"
    if ext not in {".png"}:
        raise HTTPException(status_code=400, detail="签章图片仅支持 PNG 格式（透明背景）")
    img_name = f"signature_{user_id}_{int(time.time())}_{hashlib.md5(image_bytes).hexdigest()[:8]}{ext}"
    img_path = settings.upload_dir / "signatures" / img_name
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img_path.write_bytes(image_bytes)
    return f"/uploads/signatures/{img_name}"


@router.post("/signatures/upload", response_model=SignatureResponse)
async def upload_signature(
    file: UploadFile = File(...),
    name: str = Form(...),
    role: Optional[str] = Form(None),
    password: str = Form(..., min_length=6, max_length=64),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传签章图片（PNG，透明背景），需设置使用密码"""
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="签章密码 UTF-8 编码后不能超过 72 字节")

    image_bytes = await file.read(settings.max_image_bytes + 1)
    await file.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="签章图片为空")
    if len(image_bytes) > settings.max_image_bytes:
        raise HTTPException(status_code=413, detail="签章图片超过大小限制")

    # 校验为 PNG
    ext = Path(file.filename or "").suffix.lower()
    if ext != ".png":
        raise HTTPException(status_code=400, detail="签章图片仅支持 PNG 格式")
    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=400, detail="文件内容不是有效的 PNG")

    img_url = _save_signature_image(image_bytes, user.id, file.filename or "signature.png")

    signature = Signature(
        user_id=user.id,
        name=name,
        role=role,
        image_path=img_url,
        password_hash=pwd_context.hash(password),
        is_active=True,
    )
    db.add(signature)
    await db.commit()
    await db.refresh(signature)

    return SignatureResponse(
        id=signature.id,
        name=signature.name,
        role=signature.role,
        image_path=signature.image_path,
        is_active=signature.is_active,
        created_at=signature.created_at.isoformat() if signature.created_at else "",
    )


@router.get("/signatures/me", response_model=list[SignatureResponse])
async def list_my_signatures(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户的签章列表"""
    result = await db.execute(
        select(Signature).where(
            Signature.user_id == user.id,
            Signature.is_active.is_(True),
        ).order_by(Signature.created_at.desc())
    )
    items = result.scalars().all()
    return [
        SignatureResponse(
            id=s.id,
            name=s.name,
            role=s.role,
            image_path=s.image_path,
            is_active=s.is_active,
            created_at=s.created_at.isoformat() if s.created_at else "",
        )
        for s in items
    ]


@router.delete("/signatures/{signature_id}")
async def delete_signature(
    signature_id: int,
    req: Optional[DeleteSignatureRequest] = Body(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除签章（软删除，设置 is_active=False）"""
    result = await db.execute(
        select(Signature).where(
            Signature.id == signature_id,
            Signature.user_id == user.id,
        )
    )
    signature = result.scalar_one_or_none()
    if not signature:
        raise HTTPException(status_code=404, detail="签章不存在")
    if req is not None and not pwd_context.verify(req.password, signature.password_hash):
        raise HTTPException(status_code=401, detail="签章密码错误")
    signature.is_active = False
    await db.commit()
    return {"detail": "签章已删除"}


@router.get("/documents/{doc_id}/signoffs")
async def document_signoffs(doc_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.models.document_signoff import DocumentSignoff
    from app.services.document_signoffs import SLOTS, slots_for, required_slots_for
    from app.models.user import Project
    doc = await db.get(Document, doc_id)
    if not doc or doc.project_id != require_project_id(user):
        raise HTTPException(404, "文书不存在")
    history = (await db.execute(select(DocumentSignoff).where(DocumentSignoff.document_id == doc.id).order_by(DocumentSignoff.signed_at))).scalars().all()
    project = await db.get(Project, doc.project_id)
    return {
        "revision": doc.content_revision or 1,
        "slots": [{"key": key, "label": SLOTS[key][0], "required": key in required_slots_for(doc)} for key in slots_for(doc)],
        "project_seal_signature_id": getattr(project, "seal_signature_id", None),
        "history": [{"slot": s.slot, "label": SLOTS.get(s.slot, (s.slot,))[0], "signer": s.signer_name,
                     "signed_at": s.signed_at.isoformat(), "revision": s.revision,
                     "current": s.revision == (doc.content_revision or 1), "signature_hash": s.signature_hash, "content_hash": s.content_hash} for s in history],
    }


@router.post("/documents/{doc_id}/sign")
async def sign_document(doc_id: int, req: SignDocumentRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from uuid import uuid4
    from app.models.document_signoff import DocumentSignoff
    from app.models.user import Project
    from app.services.document_signoffs import SLOTS, slots_for, current_signoffs, assert_slot_authorized
    from app.services.record_fields import now_local
    from app.services.doc_generator import generate_pdf
    from app.services.signature_service import _signature_hash
    doc = (await db.execute(select(Document).where(Document.id == doc_id, Document.project_id == require_project_id(user)).with_for_update())).scalar_one_or_none()
    if not doc: raise HTTPException(404, "文书不存在")
    from app.services.record_fields import validate_document
    validate_document(doc)
    if doc.status == DocStatus.ARCHIVED.value: raise HTTPException(409, "归档文书不能追加签认")
    slot = req.slot or slots_for(doc)[0]
    await assert_slot_authorized(db, doc, user, slot)
    previous = await current_signoffs(db, doc)
    if any(s.slot == slot for s in previous): raise HTTPException(409, "该版本的此签认位置已经签署")
    if slot == "inspector" and any(s.slot == "reviewer" and s.signer_id == user.id for s in previous):
        raise HTTPException(403, "检查人与复核人不能是同一人")
    signature = await db.get(Signature, req.signature_id)
    project = await db.get(Project, doc.project_id)
    if slot == "project_seal":
        if not getattr(project, "seal_signature_id", None) or project.seal_signature_id != req.signature_id:
            raise HTTPException(403, "请使用项目信息中配置的项目章")
    elif not signature or signature.user_id != user.id:
        raise HTTPException(403, "个人签认必须使用本人签章")
    if not signature or not signature.is_active: raise HTTPException(404, "签章不存在或已停用")
    if not pwd_context.verify(req.password, signature.password_hash): raise HTTPException(403, "签章密码错误")
    signed_at = now_local()
    if slot in ("reviewer", "chief"):
        doc.reviewed_at, doc.reviewed_by = signed_at, user.id
        doc.content = {**(doc.content or {}), "review_date": signed_at.date().isoformat()}
    import json
    # 审阅日期是独立签认事件的元数据；业务正文的签认摘要须保持不变。
    signed_content = {k: v for k, v in (doc.content or {}).items() if k != "review_date"}
    content_hash = hashlib.sha256(json.dumps(signed_content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if any(old.content_hash != content_hash for old in previous):
        raise HTTPException(409, "业务内容已变化，请按修订流程生成新版本后重新签认")
    # 每次在当前完整内容上重放已有签认，既保留多签又让审阅日期进入PDF。
    filename = f"doc_{doc.id}_r{doc.content_revision or 1}_{uuid4().hex}.pdf"
    unsigned = settings.export_dir / filename
    unsigned.parent.mkdir(parents=True, exist_ok=True)
    output_files = [unsigned]
    try:
        generate_pdf(doc, str(unsigned))
        pdf_url = f"/exports/{filename}"
        for old in previous:
            old_signature = await db.get(Signature, old.signature_id)
            path = _resolve_file_path(old_signature.image_path) if old_signature else None
            if not path or not path.exists() or _signature_hash(path) != old.signature_hash:
                raise HTTPException(409, "原签章图片已变更，不能重放签认，请保留原签署版本")
            name = f"doc_{doc.id}_{old.slot}_{uuid4().hex}.pdf"
            output_files.append(settings.export_dir / name)
            pdf_url, _ = embed_signature_to_pdf(pdf_url, old_signature.image_path, name, position=SLOTS[old.slot][1], stamp_width=95, stamp_height=70)
        name = f"doc_{doc.id}_{slot}_{uuid4().hex}.pdf"
        output_files.append(settings.export_dir / name)
        pdf_url, signature_hash = embed_signature_to_pdf(pdf_url, signature.image_path, name, position=SLOTS[slot][1], stamp_width=95, stamp_height=70)
        db.add(DocumentSignoff(document_id=doc.id, revision=doc.content_revision or 1, slot=slot,
                               signer_id=user.id, signer_name=user.real_name or user.username,
                               signature_id=signature.id, signature_hash=signature_hash, content_hash=content_hash,
                               signed_pdf_path=pdf_url, signed_at=signed_at))
        doc.pdf_path = f"/exports/{filename}"
        doc.signed_pdf_path, doc.signed_at, doc.signed_by = pdf_url, signed_at, user.id
        from app.models.security_audit import SecurityAudit
        db.add(SecurityAudit(user_id=user.id, username=user.username, action="document_signoff",
                             detail=f"doc={doc.id} revision={doc.content_revision or 1} slot={slot} hash={signature_hash}"))
        await db.commit()
    except Exception:
        await db.rollback()
        for path in output_files:
            path.unlink(missing_ok=True)
        raise
    return {"doc_id": doc.id, "signed_pdf_url": pdf_url, "signed_at": signed_at.isoformat(), "signed_by": user.id,
            "slot": slot, "signature_hash": signature_hash}


@router.get("/documents/{doc_id}/signed-pdf")
async def download_signed_pdf(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载已签章 PDF"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.project_id == project_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文书不存在")
    if not doc.signed_pdf_path:
        raise HTTPException(status_code=400, detail="该文书尚未签署")

    pdf_file = _resolve_file_path(doc.signed_pdf_path)
    if not pdf_file or not pdf_file.exists():
        raise HTTPException(status_code=404, detail="已签章 PDF 文件不存在")

    return FileResponse(
        path=str(pdf_file),
        media_type="application/pdf",
        filename=f"{doc.title}_已签章.pdf",
    )
