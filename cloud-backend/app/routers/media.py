"""四端统一媒体上传、取证、业务关联与录音转写。"""
import asyncio
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import shutil
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.hazard import Hazard, HazardStatus
from app.models.image import ImageRecord
from app.models.miniprogram import MediaRecord, MediaType
from app.models.parallel import ParallelInspection
from app.models.rectification import RectificationRecord
from app.models.task_assignment import TaskAssignment
from app.models.user import Project, User
from app.models.work_record import WorkRecord
from app.routers.auth import (
    get_current_user,
    require_project_id,
    project_scope_id,
    can_read_project,
)
from app.services.ai_client import ai_client
from app.services import model_gateway
from app.services.watermark_service import apply_watermark_to_image
from app.services.watermark_ocr import recognize_watermark
from app.services.media_validation import assert_valid_audio, assert_valid_image

try:
    from PIL import Image as PILImage
except Exception:  # pragma: no cover - image validation remains available without EXIF support
    PILImage = None

router = APIRouter()

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".webm"}
DOCUMENT_EXTS = {".pdf", ".docx", ".txt", ".md"}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 文档解析大小限制：10MB


class MediaResponse(BaseModel):
    id: int
    media_type: str
    url: str
    original_url: str
    file_hash: str
    size_bytes: int
    source: str
    business_type: Optional[str] = None
    business_id: Optional[int] = None
    created_at: str


class DetectMediaRequest(BaseModel):
    save: bool = False
    section: Optional[str] = None
    location: Optional[str] = None
    remark: Optional[str] = None


class LinkMediaRequest(BaseModel):
    media_ids: list[int]
    business_type: str
    business_id: int


def _media_dir(media_type: str) -> Path:
    day = datetime.now().strftime("%Y/%m/%d")
    path = settings.upload_dir / "media" / media_type / day
    path.mkdir(parents=True, exist_ok=True)
    return path


def _public_path(path: Path) -> str:
    relative = path.resolve().relative_to(settings.upload_dir.resolve())
    return f"/uploads/{relative.as_posix()}"


def _safe_ext(filename: str | None, allowed: set[str]) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{ext or '未知'}")
    return ext


def _extract_exif_capture_time(data: bytes) -> datetime | None:
    """Read the original capture timestamp without treating it as an authoritative business date."""
    if PILImage is None:
        return None
    try:
        with PILImage.open(io.BytesIO(data)) as image:
            exif = image.getexif()
            raw = exif.get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
    except Exception:
        return None
    if not raw:
        return None
    text = str(raw).strip()
    for candidate in (text, text.replace(':', '-', 2)):
        try:
            return datetime.fromisoformat(candidate.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _assert_image(data: bytes) -> None:
    assert_valid_image(data)


def _assert_video(data: bytes) -> None:
    is_iso_media = len(data) > 12 and data[4:8] == b"ftyp"
    is_webm = data.startswith(b"\x1a\x45\xdf\xa3")
    if not (is_iso_media or is_webm):
        raise HTTPException(status_code=400, detail="视频文件头无效")


def _assert_audio(data: bytes, ext: str) -> None:
    assert_valid_audio(data, ext)


async def _probe_video_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise HTTPException(status_code=503, detail="服务器未安装 ffprobe，无法安全校验视频时长")
    process = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    try:
        duration = float(stdout.decode("utf-8").strip())
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="无法读取视频时长") from exc
    if process.returncode != 0 or duration <= 0:
        raise HTTPException(status_code=400, detail="视频文件无法解析")
    return duration


async def _validate_business_link(
    db: AsyncSession,
    project_id: int,
    business_type: str | None,
    business_id: int | None,
) -> None:
    if not business_type and business_id is None:
        return
    if not business_type or business_id is None:
        raise HTTPException(status_code=400, detail="business_type 与 business_id 必须同时提供")
    model_map = {
        "work_record": WorkRecord,
        "hazard": Hazard,
        "task": TaskAssignment,
        "parallel_inspection": ParallelInspection,
    }
    model = model_map.get(business_type)
    if model is None:
        raise HTTPException(status_code=400, detail="不支持的业务关联类型")
    result = await db.execute(
        select(model).where(model.id == business_id, model.project_id == project_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="关联业务不存在或不属于当前项目")


async def _existing_by_key(
    db: AsyncSession,
    user_id: int,
    idempotency_key: str,
) -> MediaRecord | None:
    result = await db.execute(
        select(MediaRecord).where(
            MediaRecord.uploader_id == user_id,
            MediaRecord.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


def _serialize(record: MediaRecord) -> dict:
    return MediaResponse(
        id=record.id,
        media_type=record.media_type,
        url=record.processed_path or record.original_path,
        original_url=record.original_path,
        file_hash=record.file_hash,
        size_bytes=record.size_bytes,
        source=record.source,
        business_type=record.business_type,
        business_id=record.business_id,
        created_at=record.created_at.isoformat(),
    ).model_dump()


@router.post("/images")
async def upload_image(
    file: UploadFile = File(...),
    source: str = Form("miniprogram"),
    business_type: Optional[str] = Form(None),
    business_id: Optional[int] = Form(None),
    section: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    weather: Optional[str] = Form(None),
    unit_project: Optional[str] = Form(None),
    work_content: Optional[str] = Form(None),
    trigger_detection: bool = Form(False),
    idempotency_key_form: Optional[str] = Form(None, alias="idempotency_key"),
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    key = idempotency_key_header or idempotency_key_form or str(uuid.uuid4())
    existing = await _existing_by_key(db, user.id, key)
    if existing:
        return {**_serialize(existing), "idempotent_replay": True}
    await _validate_business_link(db, project_id, business_type, business_id)
    if business_type == "hazard" and trigger_detection:
        raise HTTPException(
            status_code=400,
            detail="整改/复查影像不能同时触发新隐患识别",
        )

    ext = _safe_ext(file.filename, IMAGE_EXTS)
    data = await file.read()
    if not data or len(data) > settings.max_image_bytes:
        raise HTTPException(status_code=413, detail="图片为空或超过大小限制")
    _assert_image(data)

    project = await db.get(Project, project_id)

    # 先识别照片上已有的水印（第三方水印相机/工程相机等），请求未提供的字段用识别值回填。
    # 识别失败不阻塞上传（recognize_watermark 内部已兜底）。
    watermark_fields: dict = {}
    try:
        watermark_result = await recognize_watermark(data)
    except Exception as exc:  # 双保险：识别异常不阻断上传
        import logging as _logging
        _logging.getLogger(__name__).warning(f"水印识别异常：{exc}")
        watermark_result = {"available": False, "fields": {}}
    if watermark_result.get("available"):
        watermark_fields = watermark_result.get("fields") or {}
        location = location or watermark_fields.get("location") or None
        weather = weather or watermark_fields.get("weather") or None
        section = section or watermark_fields.get("section") or None
    exif_taken_at = _extract_exif_capture_time(data)
    watermark_taken_at = None
    if watermark_fields.get("taken_at"):
        try:
            watermark_taken_at = datetime.fromisoformat(str(watermark_fields["taken_at"]).replace("Z", "+00:00"))
        except ValueError:
            pass
    observed_taken_at = watermark_taken_at or exif_taken_at
    taken_at_source = "watermark" if watermark_taken_at else "exif" if exif_taken_at else "server_upload_time"

    directory = _media_dir(MediaType.IMAGE.value)
    file_id = uuid.uuid4().hex
    original = directory / f"{file_id}_original{ext}"
    processed = directory / f"{file_id}_watermarked.jpg"
    original.write_bytes(data)
    watermarked = await apply_watermark_to_image(
        data,
        project_name=project.name if project else "",
        supervisor=user.real_name,
        unit_project=unit_project or "",
        section=section or "",
        location=location or "",
        work_content=work_content or "",
        weather=weather or "",
    )
    processed.write_bytes(watermarked)
    digest = hashlib.sha256(watermarked).hexdigest()

    record = MediaRecord(
        project_id=project_id,
        uploader_id=user.id,
        media_type=MediaType.IMAGE.value,
        source=source,
        original_path=_public_path(original),
        processed_path=_public_path(processed),
        mime_type="image/jpeg",
        size_bytes=len(watermarked),
        file_hash=digest,
        idempotency_key=key,
        business_type=business_type,
        business_id=business_id,
        section=section,
        location=location,
        weather=weather,
        metadata_json={
            **({"watermark": watermark_fields} if watermark_fields else {}),
            "capture_time": observed_taken_at.isoformat() if observed_taken_at else None,
            "capture_time_source": taken_at_source,
        },
    )
    db.add(record)
    await db.flush()

    hazard_payload = None
    hazard_id = business_id if business_type == "hazard" else None
    if trigger_detection:
        try:
            detection = await ai_client.detect_hazard(data, file.filename or "capture.jpg")
        except Exception as exc:
            await db.rollback()
            original.unlink(missing_ok=True)
            processed.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"AI识别服务异常：{exc}") from exc
        hazard_payload = detection
        has_hazard = bool(detection.get("has_hazard")) or (
            detection.get("risk_status") == "存在隐患"
        )
        if has_hazard:
            hazard = Hazard(
                project_id=project_id,
                reporter_id=user.id,
                status=HazardStatus.REPORTED.value,
                risk_status=detection.get("risk_status"),
                risk_category=detection.get("risk_category"),
                top1_risk=detection.get("top1_risk"),
                evidence=detection.get("evidence", []),
                uncertainty=detection.get("uncertainty"),
                recheck_suggestion=detection.get("recheck_suggestion", []),
                scene_related=detection.get("scene_related"),
                image_path=record.processed_path,
                section=section,
                location=location,
            )
            db.add(hazard)
            await db.flush()
            hazard_id = hazard.id
        record.metadata_json = {
            **(record.metadata_json or {}),
            "detection": detection,
            "hazard_id": hazard_id,
        }

    if business_type == "hazard" and business_id is not None:
        hazard = await db.get(Hazard, business_id)
        if hazard is None or hazard.project_id != project_id:
            raise HTTPException(status_code=404, detail="关联隐患不存在")
        hazard.rectification_image_path = record.processed_path
        hazard.status = HazardStatus.RECHECK_PENDING.value
        rect_result = await db.execute(
            select(RectificationRecord).where(
                RectificationRecord.hazard_id == business_id
            )
        )
        rectification = rect_result.scalars().first()
        if rectification is None:
            rectification = RectificationRecord(hazard_id=business_id)
            db.add(rectification)
        rectification.rectification_image_path = record.processed_path
        rectification.rectified_at = datetime.now()

    if business_type == "work_record" and business_id is not None:
        # 与 /media/link 保持一致：直传挂到工作记录时同步取证照片字段，
        # 否则详情页（inspection_photos/witness_photos）与生成文书的 attached_images 都看不到本次照片。
        work_record = await db.get(WorkRecord, business_id)
        if work_record is None or work_record.project_id != project_id:
            raise HTTPException(status_code=404, detail="关联工作记录不存在")
        photo_url = record.processed_path or record.original_path
        if work_record.record_type == "见证":
            work_record.witness_photos = list(
                dict.fromkeys((work_record.witness_photos or []) + [photo_url])
            )
        else:
            work_record.inspection_photos = list(
                dict.fromkeys((work_record.inspection_photos or []) + [photo_url])
            )
        metadata = dict(work_record.content_json or {})
        metadata["media_ids"] = list(
            dict.fromkeys((metadata.get("media_ids") or []) + [record.id])
        )
        work_record.content_json = metadata

    db.add(ImageRecord(
        project_id=project_id,
        uploader_id=user.id,
        source=source,
        original_path=record.original_path,
        watermarked_path=record.processed_path,
        taken_at=observed_taken_at or datetime.now(),
        location=location,
        section=section,
        weather=weather,
        hazard_id=hazard_id,
        file_hash=digest,
        remark=(
            "整改/复查影像"
            if business_type == "hazard"
            else "微信小程序/统一媒体上传"
        ),
    ))
    await db.commit()
    await db.refresh(record)
    return {
        **_serialize(record),
        "idempotent_replay": False,
        "hazard_id": hazard_id,
        "detection": hazard_payload,
        "watermark": watermark_fields or None,
        "capture_time": observed_taken_at.isoformat() if observed_taken_at else None,
        "capture_time_source": taken_at_source,
    }


@router.post("/videos")
async def upload_video(
    file: UploadFile = File(...),
    source: str = Form("miniprogram"),
    business_type: Optional[str] = Form(None),
    business_id: Optional[int] = Form(None),
    duration_seconds: Optional[int] = Form(None),
    section: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    weather: Optional[str] = Form(None),
    idempotency_key_form: Optional[str] = Form(None, alias="idempotency_key"),
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    key = idempotency_key_header or idempotency_key_form or str(uuid.uuid4())
    existing = await _existing_by_key(db, user.id, key)
    if existing:
        return {**_serialize(existing), "idempotent_replay": True}
    await _validate_business_link(db, project_id, business_type, business_id)

    ext = _safe_ext(file.filename, VIDEO_EXTS)
    data = await file.read()
    if not data or len(data) > settings.max_video_bytes:
        raise HTTPException(status_code=413, detail="视频为空或超过大小限制")
    _assert_video(data)
    path = _media_dir(MediaType.VIDEO.value) / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    try:
        probed_duration = await _probe_video_duration(path)
        if probed_duration > settings.max_video_duration_seconds:
            raise HTTPException(
                status_code=413,
                detail=f"视频时长超过 {settings.max_video_duration_seconds} 秒限制",
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    record = MediaRecord(
        project_id=project_id,
        uploader_id=user.id,
        media_type=MediaType.VIDEO.value,
        source=source,
        original_path=_public_path(path),
        mime_type=file.content_type or "video/mp4",
        size_bytes=len(data),
        duration_seconds=round(probed_duration),
        file_hash=hashlib.sha256(data).hexdigest(),
        idempotency_key=key,
        business_type=business_type,
        business_id=business_id,
        section=section,
        location=location,
    )
    db.add(record)
    await db.flush()

    # 自动调用视频水印服务（异步，不阻塞上传响应）
    watermarked_url: Optional[str] = None
    try:
        from app.services.watermark_service import apply_video_watermark_async
        project = await db.get(Project, project_id)
        watermarked_path = _media_dir(MediaType.VIDEO.value) / f"{path.stem}_watermarked.mp4"
        ok = await apply_video_watermark_async(
            input_path=path,
            output_path=watermarked_path,
            project_name=project.name if project else "",
            supervisor=user.real_name,
            location=location or "",
            section=section or "",
            weather=weather or "",
        )
        if ok and watermarked_path.exists():
            watermarked_url = _public_path(watermarked_path)
            record.processed_path = watermarked_url
            await db.flush()
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"视频水印失败：{exc}")

    # 创建视频记录（VideoRecord），便于业务查询
    try:
        from app.models.video import VideoRecord
        video_record = VideoRecord(
            project_id=project_id,
            user_id=user.id,
            original_path=_public_path(path),
            watermarked_path=watermarked_url,
            duration=probed_duration,
            location=location,
            weather=weather,
            captured_at=datetime.now(),
            source=source,
            file_hash=hashlib.sha256(data).hexdigest(),
            business_type=business_type,
            business_id=business_id,
        )
        db.add(video_record)
    except Exception:
        pass

    await db.commit()
    await db.refresh(record)
    return {**_serialize(record), "idempotent_replay": False}


@router.post("/audio/transcribe")
async def upload_audio_and_transcribe(
    file: UploadFile = File(...),
    language: str = Form("zh"),
    source: str = Form("miniprogram"),
    idempotency_key_form: Optional[str] = Form(None, alias="idempotency_key"),
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project_id = require_project_id(user)
    key = idempotency_key_header or idempotency_key_form or str(uuid.uuid4())
    existing = await _existing_by_key(db, user.id, key)
    if existing and existing.metadata_json and existing.metadata_json.get("transcription"):
        return {
            **_serialize(existing),
            **existing.metadata_json["transcription"],
            "idempotent_replay": True,
        }

    ext = _safe_ext(file.filename, AUDIO_EXTS)
    data = await file.read()
    if not data or len(data) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail="录音为空或超过大小限制")
    _assert_audio(data, ext)
    path = _media_dir(MediaType.AUDIO.value) / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    try:
        transcription = await model_gateway.transcribe_audio(
            data,
            filename=file.filename or path.name,
            content_type=file.content_type or "application/octet-stream",
            language=language,
        )
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"语音识别不可用：{exc}") from exc

    record = MediaRecord(
        project_id=project_id,
        uploader_id=user.id,
        media_type=MediaType.AUDIO.value,
        source=source,
        original_path=_public_path(path),
        mime_type=file.content_type or "audio/mpeg",
        size_bytes=len(data),
        duration_seconds=int(transcription.get("duration", 0)),
        file_hash=hashlib.sha256(data).hexdigest(),
        idempotency_key=key,
        metadata_json={"transcription": transcription},
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {
        **_serialize(record),
        **transcription,
        "idempotent_replay": False,
    }


def _extract_pdf_text(data: bytes) -> str:
    """按页提取 PDF 纯文本并拼接。pypdf 失败或提取过少时回退 PyMuPDF
    （对 CJK 编码、复杂字体的文本型 PDF 兼容性更好）。"""
    text = ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        text = ""
    if len(text.strip()) >= 20:
        return text
    try:
        import fitz  # PyMuPDF

        with fitz.open(stream=data, filetype="pdf") as doc:
            alt = "\n".join(page.get_text() for page in doc)
            if len(alt.strip()) > len(text.strip()):
                return alt
    except ImportError:
        pass
    except Exception:
        pass
    return text


def _extract_docx_text(data: bytes) -> str:
    """提取 docx 段落与表格文本。"""
    from docx import Document

    doc = Document(io.BytesIO(data))
    lines = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _extract_plain_text(data: bytes) -> str:
    """解码 txt/md 文本，utf-8 失败则回退 gbk。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


@router.post("/document/parse")
async def parse_document(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """上传文档并提取纯文本，供微信小程序智能体页导入聊天/本地文件。"""
    ext = _safe_ext(file.filename, DOCUMENT_EXTS)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文档文件为空")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=400, detail="文档超过 10MB 大小限制")
    try:
        if ext == ".pdf":
            text = _extract_pdf_text(data)
        elif ext == ".docx":
            text = _extract_docx_text(data)
        else:
            text = _extract_plain_text(data)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"文档解析失败：{file.filename}：{exc}")
        raise HTTPException(status_code=400, detail=f"文档解析失败：{exc}") from exc
    if not text.strip():
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "文档解析无文本：%s（%d B, ext=%s）——多为扫描件/图片型 PDF",
            file.filename, len(data), ext,
        )
        raise HTTPException(
            status_code=400,
            detail="未能从文档提取到文字：该文件可能是扫描件或图片型 PDF，请把页面拍照后用图片发送识别",
        )
    import logging as _logging
    _logging.getLogger(__name__).info(
        "文档解析成功：%s（%d 字符）", file.filename, len(text.strip())
    )
    return {"filename": file.filename, "text": text}


@router.post("/watermark/recognize")
async def recognize_image_watermark(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """识别图片上的水印信息（不落库），供工作台“先识别一下”预览。

    返回 {"available", "fields", "lines"}：fields 含
    taken_at / location / weather / person / project / section / lat / lon（能识别到的才有）。
    """
    _safe_ext(file.filename, IMAGE_EXTS)
    data = await file.read()
    if not data or len(data) > settings.max_image_bytes:
        raise HTTPException(status_code=413, detail="图片为空或超过大小限制")
    _assert_image(data)
    return await recognize_watermark(data)


@router.get("")
async def list_media(
    media_type: Optional[str] = None,
    business_type: Optional[str] = None,
    business_id: Optional[int] = None,
    limit: int = 50,
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = project_scope_id(user, all_projects)
    query = select(MediaRecord)
    if scope is not None:
        query = query.where(MediaRecord.project_id == scope)
    if media_type:
        query = query.where(MediaRecord.media_type == media_type)
    if business_type:
        query = query.where(MediaRecord.business_type == business_type)
    if business_id is not None:
        query = query.where(MediaRecord.business_id == business_id)
    result = await db.execute(
        query.order_by(MediaRecord.created_at.desc()).limit(min(max(limit, 1), 100))
    )
    items = result.scalars().all()
    return {"items": [_serialize(item) for item in items], "total": len(items)}


@router.post("/link")
async def link_media(
    req: LinkMediaRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """将“先上传、后创建业务”的媒体批量关联到业务记录。

    眼镜端在工作记录保存前就可能已经完成拍照上传，因此不能在上传请求里
    携带尚不存在的 business_id。本接口在业务记录创建成功后完成关联，并
    同步维护工作记录的取证图片字段。
    """
    project_id = require_project_id(user)
    media_ids = list(dict.fromkeys(req.media_ids))
    if not media_ids:
        raise HTTPException(status_code=400, detail="media_ids 不能为空")
    if len(media_ids) > 50:
        raise HTTPException(status_code=400, detail="单次最多关联 50 个媒体")
    await _validate_business_link(
        db,
        project_id,
        req.business_type,
        req.business_id,
    )
    result = await db.execute(
        select(MediaRecord).where(
            MediaRecord.id.in_(media_ids),
            MediaRecord.project_id == project_id,
            MediaRecord.uploader_id == user.id,
        )
    )
    records = result.scalars().all()
    found_ids = {record.id for record in records}
    missing_ids = [item for item in media_ids if item not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"媒体不存在或无权关联：{missing_ids}",
        )
    for record in records:
        if record.business_id is not None and (
            record.business_type != req.business_type
            or record.business_id != req.business_id
        ):
            raise HTTPException(
                status_code=409,
                detail=f"媒体 {record.id} 已关联其他业务",
            )
        record.business_type = req.business_type
        record.business_id = req.business_id

    if req.business_type == "work_record":
        work_record = await db.get(WorkRecord, req.business_id)
        if work_record is None or work_record.project_id != project_id:
            raise HTTPException(status_code=404, detail="工作记录不存在")
        image_urls = [
            record.processed_path or record.original_path
            for record in records
            if record.media_type == MediaType.IMAGE.value
        ]
        if work_record.record_type == "见证":
            work_record.witness_photos = list(
                dict.fromkeys((work_record.witness_photos or []) + image_urls)
            )
        else:
            work_record.inspection_photos = list(
                dict.fromkeys((work_record.inspection_photos or []) + image_urls)
            )
        metadata = dict(work_record.content_json or {})
        metadata["media_ids"] = list(
            dict.fromkeys((metadata.get("media_ids") or []) + media_ids)
        )
        work_record.content_json = metadata

    await db.commit()
    return {
        "detail": f"已关联 {len(records)} 个媒体",
        "media_ids": media_ids,
        "business_type": req.business_type,
        "business_id": req.business_id,
    }


def _stored_media_path(record: MediaRecord) -> Path:
    """将媒体公开 URL 安全解析为 uploads 目录下的本地路径。"""
    public_path = record.original_path or record.processed_path
    if not public_path:
        raise HTTPException(status_code=404, detail="媒体文件路径为空")
    relative = public_path.removeprefix("/uploads/").lstrip("/")
    path = (settings.upload_dir / relative).resolve()
    upload_root = settings.upload_dir.resolve()
    if not path.is_relative_to(upload_root) or not path.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    return path


async def _media_detection(
    db: AsyncSession,
    record: MediaRecord,
) -> tuple[dict, int | None]:
    """返回媒体识别结果；优先复用上传时已完成的识别。"""
    metadata = record.metadata_json or {}
    cached = metadata.get("detection")
    hazard_id = metadata.get("hazard_id")
    if isinstance(cached, dict):
        return cached, int(hazard_id) if hazard_id else None

    path = _stored_media_path(record)
    try:
        detection = await ai_client.detect_hazard(path.read_bytes(), path.name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI识别服务异常：{exc}") from exc
    record.metadata_json = {**metadata, "detection": detection}
    await db.commit()
    return detection, None


def _serialize_hazard(hazard: Hazard) -> dict:
    return {
        "id": hazard.id,
        "status": hazard.status,
        "risk_status": hazard.risk_status,
        "risk_category": hazard.risk_category,
        "top1_risk": hazard.top1_risk,
        "evidence": hazard.evidence or [],
        "section": hazard.section,
        "location": hazard.location,
        "image_path": hazard.image_path,
        "created_at": hazard.created_at.isoformat() if hazard.created_at else None,
    }


@router.post("/{media_id}/detect")
async def detect_media(
    media_id: int,
    req: DetectMediaRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """使用已上传媒体执行识别，供眼镜端弱网分步上传流程调用。"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(MediaRecord).where(
            MediaRecord.id == media_id,
            MediaRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record or record.media_type != MediaType.IMAGE.value:
        raise HTTPException(status_code=404, detail="图片媒体不存在")
    detection, _ = await _media_detection(db, record)
    return detection


@router.post("/{media_id}/detect-and-save")
async def detect_media_and_save(
    media_id: int,
    req: DetectMediaRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """识别已上传图片并确保形成一条隐患记录。"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(MediaRecord).where(
            MediaRecord.id == media_id,
            MediaRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record or record.media_type != MediaType.IMAGE.value:
        raise HTTPException(status_code=404, detail="图片媒体不存在")

    detection, hazard_id = await _media_detection(db, record)
    if hazard_id:
        existing = await db.get(Hazard, hazard_id)
        if existing and existing.project_id == project_id:
            return _serialize_hazard(existing)

    has_hazard = bool(detection.get("has_hazard")) or (
        detection.get("risk_status") == "存在隐患"
    )
    if not has_hazard:
        raise HTTPException(status_code=409, detail="识别结果未发现隐患，无需保存")

    hazard = Hazard(
        project_id=project_id,
        reporter_id=user.id,
        status=HazardStatus.REPORTED.value,
        risk_status=detection.get("risk_status"),
        risk_category=detection.get("risk_category"),
        top1_risk=detection.get("top1_risk"),
        evidence=detection.get("evidence", []),
        uncertainty=detection.get("uncertainty"),
        recheck_suggestion=detection.get("recheck_suggestion", []),
        scene_related=detection.get("scene_related"),
        image_path=record.processed_path or record.original_path,
        section=req.section or record.section,
        location=req.location or record.location,
        remark=req.remark,
    )
    db.add(hazard)
    await db.flush()
    record.metadata_json = {
        **(record.metadata_json or {}),
        "detection": detection,
        "hazard_id": hazard.id,
    }
    await db.commit()
    await db.refresh(hazard)
    return _serialize_hazard(hazard)


@router.get("/{media_id}")
async def get_media(
    media_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = project_scope_id(user, all_projects)
    stmt = select(MediaRecord).where(MediaRecord.id == media_id)
    if scope is not None:
        stmt = stmt.where(MediaRecord.project_id == scope)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="媒体不存在")
    return {
        **_serialize(record),
        "duration_seconds": record.duration_seconds,
        "section": record.section,
        "location": record.location,
        "weather": record.weather,
        "metadata": record.metadata_json,
    }
