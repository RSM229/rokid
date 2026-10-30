"""电子签章路由 - 签章上传、管理、文书签署

作者: cloud-backend
创建日期: 2026-07-18
"""
import hashlib
import io
import time
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.document import Document, DocStatus
from app.models.signature import Signature
from app.models.document_signoff import DocumentSignoff
from app.models.user import User
from app.routers.auth import (
    get_current_user,
    require_permission,
    require_project_id,
    project_scope_id,
    can_read_project,
)
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


class SigningModeRequest(BaseModel):
    mode: str
    slot: Optional[str] = None


class OfflineSignoffRequest(BaseModel):
    slot: str


class SignatureResponse(BaseModel):
    id: int
    name: str
    role: Optional[str]
    image_path: str
    is_active: bool
    created_at: str


def _save_signature_image(image_bytes: bytes, user_id: int, filename: str) -> str:
    """保存签章图片到上传目录，返回相对 URL"""
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            if source.width * source.height > 20_000_000:
                raise HTTPException(status_code=413, detail="签名图片尺寸过大")
            image = source.convert("RGBA")
            pixels = image.load()
            for y in range(image.height):
                for x in range(image.width):
                    r, g, b, a = pixels[x, y]
                    if not a or min(r, g, b) < 220 or max(r, g, b) - min(r, g, b) > 35:
                        continue
                    white = min(r, g, b)
                    alpha = round(a * (255 - white) / 255)
                    if alpha:
                        r = max(0, min(255, round((r - white) * 255 / (255 - white))))
                        g = max(0, min(255, round((g - white) * 255 / (255 - white))))
                        b = max(0, min(255, round((b - white) * 255 / (255 - white))))
                    else:
                        r, g, b = 0, 0, 0
                    pixels[x, y] = (r, g, b, alpha)
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            image_bytes = output.getvalue()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="签名图片无法解析") from exc
    img_name = f"signature_{user_id}_{int(time.time())}_{hashlib.md5(image_bytes).hexdigest()[:8]}.png"
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
    """上传签章图片；服务端会把近白背景转换为透明 PNG。"""
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="签章密码 UTF-8 编码后不能超过 72 字节")

    image_bytes = await file.read(settings.max_image_bytes + 1)
    await file.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="签章图片为空")
    if len(image_bytes) > settings.max_image_bytes:
        raise HTTPException(status_code=413, detail="签章图片超过大小限制")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="请上传 PNG、JPG 或 WEBP 图片")

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


@router.post("/documents/{doc_id}/signing-mode")
async def set_document_signing_mode(
    doc_id: int,
    req: SigningModeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """记录所选签署方式；线下模式只留状态，不生成电子签名。"""
    if req.mode not in {"online", "uploaded", "offline"}:
        raise HTTPException(400, "签署方式必须是 online、uploaded 或 offline")
    doc = (await db.execute(select(Document).where(
        Document.id == doc_id,
        Document.project_id == require_project_id(user),
    ).with_for_update())).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "文书不存在")
    if doc.status == DocStatus.ARCHIVED.value:
        raise HTTPException(409, "归档文书不能修改签署方式")
    from app.services.document_signoffs import slots_for
    if req.slot and req.slot not in slots_for(doc):
        raise HTTPException(400, "该文书没有此签认位置")
    if req.slot == "project_seal" and req.mode == "online":
        raise HTTPException(400, "项目用章只能使用已授权印章或选择线下签署")
    if req.slot:
        from app.services.document_signoffs import assert_slot_authorized
        await assert_slot_authorized(db, doc, user, req.slot)
    content = dict(doc.content or {})
    signing = dict(content.get("signing") or {})
    slot = req.slot or "default"
    current = await db.execute(select(DocumentSignoff.id).where(
        DocumentSignoff.document_id == doc.id,
        DocumentSignoff.revision == (doc.content_revision or 1),
    ).limit(1))
    if current.first():
        raise HTTPException(409, "该版本已有签认，不能更换签署方式")
    if any(
        isinstance(row, dict)
        and row.get("slot") == slot
        and row.get("revision") == (doc.content_revision or 1)
        and row.get("status") == "completed"
        for row in content.get("offline_signoffs") or []
    ):
        raise HTTPException(409, "该版本已有线下签署确认，不能更换签署方式")
    signing[slot] = {
        "mode": req.mode,
        "selected_at": datetime.now().isoformat(),
        "selected_by": user.id,
    }
    content["signing"] = signing
    # Signing method is workflow metadata, not a business-body revision. Keep
    # the current DOCX/PDF and existing signatures intact when the user picks it.
    doc.content = content
    await db.commit()
    return {"doc_id": doc.id, "slot": slot, "mode": req.mode, "revision": doc.content_revision or 1}


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
async def document_signoffs(doc_id: int, all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.models.document_signoff import DocumentSignoff
    from app.services.document_signoffs import SLOTS, slots_for, required_slots_for, current_offline_signoffs
    from app.models.user import Project
    doc = await db.get(Document, doc_id)
    if not doc or not can_read_project(doc.project_id, user, all_projects):
        raise HTTPException(404, "文书不存在")
    history = (await db.execute(select(DocumentSignoff).where(DocumentSignoff.document_id == doc.id).order_by(DocumentSignoff.signed_at))).scalars().all()
    project = await db.get(Project, doc.project_id)
    signing = dict((doc.content or {}).get("signing") or {})
    slot_modes = {key: value.get("mode") for key, value in signing.items() if isinstance(value, dict)}
    current_electronic = {
        row.slot for row in history if row.revision == (doc.content_revision or 1)
    }
    current_offline = current_offline_signoffs(doc)
    role_labels = {
        ("supervisor", "gdaq_30306_weida"): "监理检查人员",
        ("chief", "gdaq_30306_weida"): "总监理工程师",
        ("supervisor", "gdaq_30305_qizhong"): "监理检查人员",
        ("chief", "gdaq_30305_qizhong"): "总监理工程师",
        ("supervisor", "巡视记录"): "巡视监理人员",
        ("inspector", "见证取样记录"): "见证取样人",
        ("inspector", "gd_b_220_rizhi"): "当日值班监理人员",
        ("supervisor", "gd_b_220_rizhi"): "专业监理工程师",
        ("project_seal", "gd_b_221_yuebao"): "监理单位（盖章）",
    }
    history_items = [
        {
            "slot": signoff.slot,
            "label": role_labels.get(
                (signoff.slot, doc.form_code),
                role_labels.get(
                    (signoff.slot, doc.doc_type),
                    SLOTS.get(signoff.slot, (signoff.slot,))[0],
                ),
            ),
            "signer": signoff.signer_name,
            "signed_at": signoff.signed_at.isoformat(),
            "revision": signoff.revision,
            "current": signoff.revision == (doc.content_revision or 1),
            "type": "electronic",
            "signature_hash": signoff.signature_hash,
            "content_hash": signoff.content_hash,
        }
        for signoff in history
    ]
    for row in (doc.content or {}).get("offline_signoffs") or []:
        if not isinstance(row, dict):
            continue
        slot = str(row.get("slot") or "")
        history_items.append({
            "slot": slot,
            "label": role_labels.get(
                (slot, doc.form_code),
                role_labels.get((slot, doc.doc_type), SLOTS.get(slot, (slot,))[0]),
            ),
            "confirmed_by": row.get("confirmed_by_name", ""),
            "signed_at": row.get("confirmed_at", ""),
            "revision": row.get("revision"),
            "current": row.get("revision") == (doc.content_revision or 1),
            "type": "offline",
            "status": "线下签署已确认" if row.get("status") == "completed" else "等待线下签署",
        })
    return {
        "revision": doc.content_revision or 1,
        "slots": [{
            "key": key,
            "label": role_labels.get((key, doc.form_code), role_labels.get((key, doc.doc_type), SLOTS[key][0])),
            "required": key in required_slots_for(doc),
            "mode": slot_modes.get(key),
            "completed": key in current_electronic or key in current_offline,
            "completion_type": "electronic" if key in current_electronic else "offline" if key in current_offline else None,
        } for key in slots_for(doc)],
        "project_seal_signature_id": getattr(project, "seal_signature_id", None),
        "history": sorted(history_items, key=lambda row: str(row.get("signed_at") or "")),
    }


@router.post("/documents/{doc_id}/offline-signoff")
async def confirm_offline_signoff(
    doc_id: int,
    req: OfflineSignoffRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record the user's confirmation that a printed copy was signed by hand."""
    from app.models.security_audit import SecurityAudit
    from app.services.document_signoffs import (
        slots_for, assert_slot_authorized, current_signoffs, current_offline_signoffs,
    )
    from app.services.record_fields import now_local, validate_document

    doc = (await db.execute(select(Document).where(
        Document.id == doc_id,
        Document.project_id == require_project_id(user),
    ).with_for_update())).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "文书不存在")
    if doc.status == DocStatus.ARCHIVED.value:
        raise HTTPException(409, "已归档文书不能补充线下签署确认")
    if req.slot not in slots_for(doc):
        raise HTTPException(400, "该文书没有此签认位置")

    content = dict(doc.content or {})
    signing = content.get("signing") if isinstance(content.get("signing"), dict) else {}
    mode = signing.get(req.slot) if isinstance(signing.get(req.slot), dict) else {}
    if mode.get("mode") != "offline":
        raise HTTPException(409, "请先将该签认位置设置为线下打印手签")
    await assert_slot_authorized(db, doc, user, req.slot)
    if any(row.slot == req.slot for row in await current_signoffs(db, doc)):
        raise HTTPException(409, "该位置已有电子签认，不能再登记线下签署")
    if req.slot in current_offline_signoffs(doc):
        raise HTTPException(409, "该版本的线下签署已经确认")
    validate_document(doc)

    signed_at = now_local()
    row = {
        "slot": req.slot,
        "mode": "offline",
        "status": "completed",
        "revision": doc.content_revision or 1,
        "confirmed_by_id": user.id,
        "confirmed_by_name": user.real_name or user.username,
        "confirmed_at": signed_at.isoformat(),
    }
    history = list(content.get("offline_signoffs") or [])
    history.append(row)
    content["offline_signoffs"] = history
    doc.content = content
    db.add(SecurityAudit(
        user_id=user.id,
        username=user.username,
        action="document_offline_signoff_confirmed",
        detail=f"doc={doc.id} revision={doc.content_revision or 1} slot={req.slot}",
    ))
    await db.commit()
    return {
        "doc_id": doc.id,
        "revision": doc.content_revision or 1,
        "slot": req.slot,
        "status": "completed",
        "confirmed_by": row["confirmed_by_name"],
        "confirmed_at": row["confirmed_at"],
        "electronic_signature_created": False,
    }


@router.post("/documents/{doc_id}/sign")
async def sign_document(doc_id: int, req: SignDocumentRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from uuid import uuid4
    from app.models.document_signoff import DocumentSignoff
    from app.models.user import Project
    from app.services.document_signoffs import SLOTS, slots_for, current_signoffs, assert_slot_authorized
    from app.services.record_fields import now_local
    from app.services.signature_service import _signature_hash
    doc = (await db.execute(select(Document).where(Document.id == doc_id, Document.project_id == require_project_id(user)).with_for_update())).scalar_one_or_none()
    if not doc: raise HTTPException(404, "文书不存在")
    from app.services.record_fields import validate_document
    validate_document(doc)
    if doc.status == DocStatus.ARCHIVED.value: raise HTTPException(409, "归档文书不能追加签认")
    slot = req.slot or slots_for(doc)[0]
    selected_mode = ((doc.content or {}).get("signing") or {}).get(slot, {})
    selected_mode = selected_mode.get("mode") if isinstance(selected_mode, dict) else None
    if selected_mode == "offline":
        raise HTTPException(409, "当前签署方式为线下打印手签，不能提交电子签认")
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
    signed_content = {k: v for k, v in (doc.content or {}).items() if k not in {"review_date", "signing"}}
    content_hash = hashlib.sha256(json.dumps(signed_content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if any(old.content_hash != content_hash for old in previous):
        raise HTTPException(409, "业务内容已变化，请按修订流程生成新版本后重新签认")
    # 每次在当前完整内容上重放已有签认，既保留多签又让审阅日期进入PDF。
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    output_files = []
    try:
        signature_path = _resolve_file_path(signature.image_path)
        if not signature_path or not signature_path.exists():
            raise HTTPException(409, "签名图片文件不存在，请重新上传")
        signature_hash = _signature_hash(signature_path)
        pdf_url = ""
        if doc.docx_path:
            source_docx = _resolve_file_path(doc.docx_path)
            if not source_docx or not source_docx.exists():
                raise HTTPException(404, "Word 模板文件不存在，无法在签名栏定位签名")
            from app.services.form_renderer import docx_to_pdf, embed_signoff_image
            stamped_name = f"doc_{doc.id}_r{doc.content_revision or 1}_{uuid4().hex}.docx"
            stamped_docx = settings.export_dir / stamped_name
            import shutil
            shutil.copy2(source_docx, stamped_docx)
            output_files.append(stamped_docx)
            if not embed_signoff_image(stamped_docx, slot, signature.image_path, doc.form_code):
                raise HTTPException(422, "未能在该表式定位对应签认栏，请勿使用错误位置代签")
            signed_pdf = await asyncio.to_thread(docx_to_pdf, stamped_docx)
            if signed_pdf is None:
                raise HTTPException(501, "当前服务未安装 Word/LibreOffice，无法从已签 Word 生成同版式 PDF")
            output_files.append(signed_pdf)
            doc.docx_path = f"/exports/{stamped_docx.name}"
            doc.pdf_path = f"/exports/{signed_pdf.name}"
            pdf_url = doc.pdf_path
        else:
            from app.services.doc_generator import generate_pdf
            filename = f"doc_{doc.id}_r{doc.content_revision or 1}_{uuid4().hex}.pdf"
            unsigned = settings.export_dir / filename
            output_files.append(unsigned)
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
            pdf_url, _ = embed_signature_to_pdf(pdf_url, signature.image_path, name, position=SLOTS[slot][1], stamp_width=95, stamp_height=70)
        db.add(DocumentSignoff(document_id=doc.id, revision=doc.content_revision or 1, slot=slot,
                               signer_id=user.id, signer_name=user.real_name or user.username,
                               signature_id=signature.id, signature_hash=signature_hash, content_hash=content_hash,
                               signed_pdf_path=pdf_url, signed_at=signed_at))
        doc.signed_pdf_path, doc.signed_at, doc.signed_by = pdf_url, signed_at, user.id
        from app.models.work_record import WorkRecord
        linked_record = (await db.execute(select(WorkRecord).where(
            WorkRecord.generated_doc_id == doc.id,
        ).with_for_update())).scalar_one_or_none()
        if linked_record:
            record_content = dict(linked_record.content_json or {})
            history = list(record_content.get("document_signoffs") or [])
            history.append({
                "document_id": doc.id,
                "revision": doc.content_revision or 1,
                "slot": slot,
                "signer_id": user.id,
                "signer_name": user.real_name or user.username,
                "signature_id": signature.id,
                "signature_hash": signature_hash,
                "signed_at": signed_at.isoformat(),
                "signed_docx_path": doc.docx_path,
                "signed_pdf_path": pdf_url,
            })
            record_content["document_signoffs"] = history
            linked_record.content_json = record_content
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
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载已签章 PDF"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Document).where(Document.id == doc_id)
    if scope is not None:
        stmt = stmt.where(Document.project_id == scope)
    result = await db.execute(stmt)
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
