"""工作记录路由 — 巡视/验收/见证/旁站"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, update, or_, exists
from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime, date

from app.db import get_db
from app.models.project_member import ProjectMember
from app.models.user import Project, User, UserRole
from app.models.work_record import WorkRecord, WorkRecordType, WorkRecordStatus
from app.models.document import Document, DocType, DocStatus
from app.models.image import ImageRecord
from app.models.miniprogram import MediaRecord, MediaType
from app.routers.auth import (
    get_current_user,
    project_scope_id,
    require_permission,
    require_project_id,
)
from app.services.watermark_service import apply_watermark_to_image
from app.services.watermark_ocr import recognize_watermark
from app.services.media_validation import assert_valid_image
from app.config import settings
import os
import json
import logging
import uuid
from pathlib import Path
from app.services.record_fields import business_date, now_local, validate_times, validate_record, invalidate_document, ACCEPTANCE_RESULTS

router = APIRouter()
logger = logging.getLogger(__name__)

# 工作记录类型 -> 文书类型（「待定」不在其中：类型未定，套不了统表）
RECORD_DOC_TYPES = {
    WorkRecordType.INSPECTION.value: DocType.INSPECTION.value,
    WorkRecordType.ACCEPTANCE.value: DocType.ACCEPTANCE.value,
    WorkRecordType.WITNESS.value: DocType.WITNESS_SAMPLING.value,
    WorkRecordType.STATION.value: DocType.SIDE_STATION.value,
}


async def auto_create_record_document(
    db: AsyncSession,
    user: User,
    record: WorkRecord,
    project: Project | None = None,
    auto_generate: bool = True,
) -> Document | None:
    """工作记录创建/提交后自动生成对应的省统表文书。

    规则：
    - 幂等：已有 generated_doc_id 直接返回，绝不重复生成；
    - 「待定」记录不生成（类型未定）；旁站未结束（无结束时间）也不生成，
      待 `/station/{id}` 结束时补；
    - 优先走 `form_doc.generate_form_from_record`（渲染省统表 docx 模板并落库）；
      表式必填项不足时**不生成残缺文书**，交由 Agent 继续补录；
      技术性渲染失败才降级为普通文书；
    - 任何异常都只记日志，**绝不让工作记录的创建/提交失败**。
    """
    # 工作记录与正式表单解耦：业务入口默认只创建记录。保留该函数供
    # 历史管理脚本/测试和明确的兼容调用使用，产品入口必须显式选择表式。
    if not auto_generate:
        return None
    if record.record_type not in RECORD_DOC_TYPES:
        return None
    if record.generated_doc_id:
        return await db.get(Document, record.generated_doc_id)
    if record.record_type == WorkRecordType.STATION.value and not record.station_end_time:
        return None

    if project is None and record.project_id:
        project = await db.get(Project, record.project_id)

    from app.routers.form_doc import _record_attach_photos
    photos = await _record_attach_photos(db, record)
    # 用 SAVEPOINT 包住：文书生成失败只回滚这一次尝试，
    # 不会把调用方尚未提交的工作记录一起回滚掉。
    try:
        async with db.begin_nested():
            from app.routers.form_doc import generate_form_from_record

            doc, _spec, _content = await generate_form_from_record(
                db, user, project, record
            )
            doc.attached_images = photos
        return doc
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, HTTPException) and exc.status_code == 422:
            logger.info("工作记录尚未满足表式必填项，暂不生成文书：%s", exc.detail)
            return None
        logger.warning("省统表文书生成失败，降级为普通文书：%s", exc)

    # 降级：不套模板，仍生成一份可归档文书
    try:
        from app.routers.form_doc import _content_from_record

        content = _content_from_record(record, user, project)
    except Exception:  # noqa: BLE001
        content = {"section": record.section or "", "location": record.location or ""}
    from app.services.record_fields import project_fields
    from app.services.form_registry import FORM_BY_CODE
    from app.services.form_routing import effective_record_form_code
    code = effective_record_form_code(None, str(record.record_type or ""), project=project, record=record)
    spec = FORM_BY_CODE.get(code) if code else None
    for key, value in project_fields(project).items():
        if not str(content.get(key) or "").strip():
            content[key] = value
    content["doc_number"] = f"{spec.form_number if spec else 'JL'}-{record.id:05d}"
    content.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
    content.setdefault("supervisor", user.real_name)
    try:
        async with db.begin_nested():
            doc = Document(
                doc_type=RECORD_DOC_TYPES[record.record_type],
                title=f"工作记录_{business_date(record).strftime('%Y-%m-%d')}",
                content=content,
                section=record.section,
                location=record.location,
                project_id=record.project_id,
                creator_id=record.creator_id,
                status=DocStatus.DRAFT.value,
                attached_images=photos,
            )
            db.add(doc)
            await db.flush()
            record.generated_doc_id = doc.id
        return doc
    except Exception:  # noqa: BLE001
        logger.exception("降级生成文书同样失败，跳过（不影响工作记录）")
        return None


async def _validate_record_ready(db, record):
    validate_record(record, await db.get(Project, record.project_id))
    if record.record_type in ("验收", "旁站", "巡视"):
        doc = await db.get(Document, record.generated_doc_id) if record.generated_doc_id else None
        if not doc:
            raise HTTPException(400, "请先生成记录文书并完成必签项")
        from app.services.document_signoffs import assert_required_signoffs
        await assert_required_signoffs(db, doc)


async def _read_valid_photo(photo: UploadFile) -> bytes:
    ext = os.path.splitext(photo.filename or "")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "仅支持 jpg、png、webp 图片")
    data = await photo.read(settings.max_image_bytes + 1)
    await photo.close()
    if not data:
        raise HTTPException(400, "图片为空")
    if len(data) > settings.max_image_bytes:
        raise HTTPException(413, "图片超过大小限制")
    assert_valid_image(data)
    return data


# ---- 请求模型 ----

class CreateInspectionRequest(BaseModel):
    """巡视记录请求"""
    record_date: Optional[date] = None
    worker_count: Optional[int] = Field(default=None, ge=0)
    inspection_start_time: Optional[datetime] = None
    inspection_end_time: Optional[datetime] = None
    section: Optional[str] = None
    location: Optional[str] = None
    work_content: Optional[str] = None
    weather: Optional[str] = None
    inspection_remark: Optional[str] = None
    problems: Optional[str] = None
    treatment: Optional[str] = None


class CreateAcceptanceRequest(BaseModel):
    """验收记录请求"""
    record_date: Optional[date] = None
    acceptance_participants: Optional[str] = None
    section: Optional[str] = None
    location: Optional[str] = None
    acceptance_type: Optional[str] = None  # 验收类型
    acceptance_content: Optional[str] = None  # 验收内容
    acceptance_basis: Optional[str] = None  # 检验依据（GD-B-213）
    treatment: Optional[str] = None  # 处理意见
    acceptance_guide_ref: Optional[str] = None  # 验收指引关键词（用于从知识图谱获取流程）
    guide_ref: Optional[int] = None  # 知识图谱实体ID
    acceptance_result: Optional[str] = None


class CreateWitnessRequest(BaseModel):
    """见证记录请求"""
    section: Optional[str] = None
    location: Optional[str] = None
    witness_material_name: Optional[str] = None
    witness_material_spec: Optional[str] = None
    witness_quantity: Optional[str] = None  # 取样数量（GD-B-219）
    witness_unit: Optional[str] = None  # 计量单位（GD-B-219）
    witness_cert_no: Optional[str] = None  # 合格证/检测报告编号（GD-B-219）
    witness_accept_result: Optional[str] = None  # 进场验收结论（GD-B-219）
    manufacturer: Optional[str] = None
    batch_no: Optional[str] = None
    remark: Optional[str] = None


class CreateStationRequest(BaseModel):
    """旁站记录请求"""
    record_date: Optional[date] = None
    station_process: Optional[str] = None
    section: Optional[str] = None
    location: Optional[str] = None
    station_project_name: Optional[str] = None
    station_location: Optional[str] = None
    station_content: Optional[str] = None
    station_supervision_status: Optional[str] = None  # 旁站监理情况（GD-B-212）
    station_conclusion: Optional[str] = None
    problems: Optional[str] = None
    treatment: Optional[str] = None
    weather: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class CreateOtherRequest(BaseModel):
    """待定工作记录请求（眼镜端智能体未能确定类型时创建，后续人工贴标签）"""
    section: Optional[str] = None
    location: Optional[str] = None
    other_content: Optional[str] = None
    work_content: Optional[str] = None
    voice_text: Optional[str] = None  # 现场语音说明原文，供后续自动分类参考
    source: Optional[str] = None  # 来源标识（miniprogram/glasses 等），缺省按 miniprogram 记录


class CreateQualityRequest(BaseModel):
    """质量问题记录请求（眼镜端兼容路由，统一归为待定，原类型存 content_json）"""
    section: Optional[str] = None
    location: Optional[str] = None
    quality_content: Optional[str] = None
    work_content: Optional[str] = None


class UpdateRecordTypeRequest(BaseModel):
    """修改工作记录类型标签（待定 → 旁站/巡视/验收/见证 等）"""
    record_type: str


class UpdateWorkRecordRequest(BaseModel):
    station_start_time: Optional[datetime] = None
    station_end_time: Optional[datetime] = None
    status: Optional[str] = None
    remark: Optional[str] = None
    content_json: Optional[dict] = None
    record_date: Optional[date] = None
    worker_count: Optional[int] = Field(default=None, ge=0)
    inspection_start_time: Optional[datetime] = None
    inspection_end_time: Optional[datetime] = None
    station_process: Optional[str] = None
    acceptance_participants: Optional[str] = None
    section: Optional[str] = None
    location: Optional[str] = None
    weather: Optional[str] = None
    work_content: Optional[str] = None
    inspection_remark: Optional[str] = None
    problems: Optional[str] = None
    treatment: Optional[str] = None
    acceptance_type: Optional[str] = None
    acceptance_content: Optional[str] = None
    acceptance_basis: Optional[str] = None
    acceptance_guide_ref: Optional[str] = None
    acceptance_result: Optional[str] = None
    witness_material_name: Optional[str] = None
    witness_material_spec: Optional[str] = None
    witness_quantity: Optional[str] = None
    witness_unit: Optional[str] = None
    witness_cert_no: Optional[str] = None
    witness_accept_result: Optional[str] = None
    station_project_name: Optional[str] = None
    station_location: Optional[str] = None
    station_content: Optional[str] = None
    station_supervision_status: Optional[str] = None
    station_conclusion: Optional[str] = None


def _record_field_edit_snapshot(record: WorkRecord, changed_fields: set[str]) -> dict[str, dict[str, str]]:
    """Map edited record columns to the canonical form fields they represent."""
    edits: dict[str, dict[str, str]] = {}

    def text(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="minutes")
        if isinstance(value, date):
            return value.isoformat()
        return "" if value is None else str(value).strip()

    def put(canonical: str, value: Any, *aliases: str) -> None:
        row = {"value": text(value), "source": "user_edit", "status": "user_confirmed"}
        for key in (canonical, *aliases):
            edits[key] = row

    if "record_date" in changed_fields:
        put("date", record.record_date, "record_date")
        if record.record_type == WorkRecordType.WITNESS.value:
            put("sample_date", record.record_date, "acceptance_date")

    if "section" in changed_fields or "station_location" in changed_fields:
        if record.record_type != WorkRecordType.STATION.value:
            if "section" in changed_fields:
                put("section", record.section, "work_area", "acceptance_section")
        elif "station_location" in changed_fields or not record.station_location:
            put("section", record.station_location or record.section, "work_area", "station_part", "station_location")

    if "location" in changed_fields:
        put("location", record.location, "address")
    if "weather" in changed_fields:
        put("weather", record.weather)
    if "worker_count" in changed_fields:
        put("worker_count", record.worker_count)

    if record.record_type == WorkRecordType.INSPECTION.value and "work_content" in changed_fields:
        put("content", record.work_content, "work_content", "inspection_content", "activity")
    elif record.record_type == WorkRecordType.STATION.value:
        if "station_content" in changed_fields:
            put("content", record.station_content or record.work_content, "work_content", "station_content", "activity")
        elif "work_content" in changed_fields:
            put("content", record.work_content, "work_content", "station_content", "activity")
    elif record.record_type == WorkRecordType.ACCEPTANCE.value and (
        "acceptance_content" in changed_fields or "work_content" in changed_fields
    ):
        if "acceptance_content" in changed_fields:
            value = (record.content_json or {}).get("acceptance_content")
        else:
            value = (record.content_json or {}).get("acceptance_content") or record.work_content
        put("acceptance_content", value, "content", "work_content")

    if record.record_type == WorkRecordType.INSPECTION.value:
        start_field, end_field = "inspection_start_time", "inspection_end_time"
    else:
        start_field, end_field = "station_start_time", "station_end_time"
    if start_field in changed_fields:
        put("start_time", getattr(record, start_field), start_field)
        if record.record_type == WorkRecordType.INSPECTION.value:
            put("time", getattr(record, start_field) or record.record_date)
    if end_field in changed_fields:
        put("end_time", getattr(record, end_field), end_field)
    if record.record_type == WorkRecordType.INSPECTION.value and "record_date" in changed_fields and not record.inspection_start_time:
        put("time", record.record_date)

    if changed_fields.intersection({"problems", "inspection_remark"}):
        if record.record_type == WorkRecordType.INSPECTION.value:
            value = record.problems if "problems" in changed_fields else (record.problems or record.inspection_remark)
        else:
            from app.services.record_fields import problem_text
            value = problem_text(record)
        put("problems", value, "problem", "issue", "inspection_remark")
    if "treatment" in changed_fields:
        put("treatment", record.treatment, "handling")
    if "remark" in changed_fields:
        put("remark", record.remark)

    if record.record_type == WorkRecordType.STATION.value:
        if "station_process" in changed_fields:
            put("station_process", record.station_process)
        if "station_supervision_status" in changed_fields:
            put("supervision_status", record.station_supervision_status, "station_supervision_status")
        if "station_conclusion" in changed_fields:
            put("conclusion", record.station_conclusion, "station_conclusion")

    if record.record_type == WorkRecordType.ACCEPTANCE.value:
        if "acceptance_type" in changed_fields:
            put("acceptance_type", (record.content_json or {}).get("acceptance_type"))
        if "acceptance_participants" in changed_fields:
            put("acceptance_participants", record.acceptance_participants)
        if "acceptance_result" in changed_fields:
            put("acceptance_result", record.acceptance_result)
        if "acceptance_basis" in changed_fields:
            put("basis", record.acceptance_basis)

    if record.record_type == WorkRecordType.WITNESS.value:
        for field, canonical, aliases in (
            ("witness_material_name", "material_name", ()),
            ("witness_material_spec", "material_spec", ("spec",)),
            ("witness_quantity", "quantity", ()),
            ("witness_unit", "unit", ()),
            ("witness_cert_no", "cert_no", ("report_no",)),
            ("witness_accept_result", "acceptance_result", ("accept_result",)),
        ):
            if field in changed_fields:
                put(canonical, getattr(record, field), *aliases)

    return edits


@router.post("/create-with-photos")
async def create_work_record_with_photos(
    record_type: str = Form(...),
    payload_json: str = Form("{}"),
    photos: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """原子创建任一类工作记录并保存现场照片。

    该入口用于移动端弱网队列：记录表单和本地 Blob 在同一请求中重放，
    避免先创建记录后因断网无法把照片关联回新 ID。
    """
    type_map = {
        "inspection": WorkRecordType.INSPECTION.value,
        "acceptance": WorkRecordType.ACCEPTANCE.value,
        "witness": WorkRecordType.WITNESS.value,
        "station": WorkRecordType.STATION.value,
        "other": "待定",
        "待定": "待定",
    }
    permission_map = {
        "inspection": "inspection",
        "acceptance": "acceptance",
        "witness": "witness",
        "station": "station",
        "other": None,
        "待定": None,
    }
    if record_type not in type_map:
        raise HTTPException(400, "不支持的工作记录类型")
    required_permission = permission_map[record_type]
    if required_permission and not user.has_permission(required_permission):
        raise HTTPException(403, "当前角色无权创建该类工作记录")
    if len(photos) > 9:
        raise HTTPException(400, "现场照片最多上传 9 张")
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "payload_json 不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload_json 必须是对象")
    add_system_watermark = payload.get("camera_capture") is True

    project_id = require_project_id(user)
    if record_type == "inspection":
        if not payload.get("construction_area") or not payload.get("construction_content"):
            raise HTTPException(400, "巡视记录需填写施工区域和施工内容")
        record = WorkRecord(
            record_type=type_map[record_type],
            status=WorkRecordStatus.DRAFT.value,
            creator_id=user.id,
            project_id=project_id,
            section=payload.get("construction_area"),
            location=payload.get("location"),
            work_content=payload.get("construction_content"),
            weather=payload.get("weather"),
            inspection_remark=payload.get("remark"),
        )
    elif record_type == "acceptance":
        if not payload.get("acceptance_type") or not payload.get("construction_area"):
            raise HTTPException(400, "验收记录需填写验收类型和施工区域")
        result_map = {"pass": "合格", "fail": "不合格", "pending": "待整改"}
        record = WorkRecord(
            record_type=type_map[record_type],
            status=WorkRecordStatus.DRAFT.value,
            creator_id=user.id,
            project_id=project_id,
            section=payload.get("construction_area"),
            location=payload.get("location"),
            acceptance_result=result_map.get(
                payload.get("result"),
                payload.get("result"),
            ),
            content_json={
                "acceptance_type": payload.get("acceptance_type"),
                "acceptance_content": payload.get("acceptance_content"),
            },
            remark=payload.get("remark"),
        )
    elif record_type == "witness":
        if not payload.get("material_name"):
            raise HTTPException(400, "见证记录需填写材料名称")
        record = WorkRecord(
            record_type=type_map[record_type],
            status=WorkRecordStatus.DRAFT.value,
            creator_id=user.id,
            project_id=project_id,
            section=payload.get("construction_area"),
            location=payload.get("location"),
            witness_material_name=payload.get("material_name"),
            witness_material_spec=payload.get("specification"),
            remark=payload.get("remark"),
        )
    elif record_type in ("other", "待定"):
        # 待定记录：不强制字段，语音说明/内容尽量保留，供后续贴标签
        record = WorkRecord(
            record_type="待定",
            status=WorkRecordStatus.DRAFT.value,
            creator_id=user.id,
            project_id=project_id,
            section=payload.get("section") or payload.get("construction_area"),
            location=payload.get("location"),
            work_content=payload.get("other_content") or payload.get("work_content"),
            content_json={
                "voice_text": payload.get("voice_text"),
                "source": payload.get("source"),
                "evidence_watermarks": [],
            },
        )
    else:
        if not payload.get("project_name") or not payload.get("start_time"):
            raise HTTPException(400, "旁站记录需填写旁站工程和开始时间")
        try:
            start_time = datetime.fromisoformat(payload["start_time"])
            end_time = (
                datetime.fromisoformat(payload["end_time"])
                if payload.get("end_time")
                else None
            )
        except ValueError as exc:
            raise HTTPException(400, "旁站时间格式无效") from exc
        record = WorkRecord(
            record_type=type_map[record_type],
            status=WorkRecordStatus.DRAFT.value,
            creator_id=user.id,
            project_id=project_id,
            section=payload.get("position"),
            location=payload.get("position"),
            station_project_name=payload.get("project_name"),
            station_location=payload.get("position"),
            station_start_time=start_time,
            station_end_time=end_time,
            station_content=payload.get("description"),
            station_conclusion=payload.get("conclusion"),
        )

    for key in ("worker_count", "inspection_start_time", "inspection_end_time", "station_process", "acceptance_participants", "record_date", "problems", "treatment"):
        if key in payload:
            value = payload[key]
            try:
                if key.endswith("_time") and value:
                    value = datetime.fromisoformat(value)
                elif key == "record_date" and value:
                    value = date.fromisoformat(value)
                elif key == "worker_count" and value is not None:
                    if isinstance(value, bool) or int(value) != float(value):
                        raise ValueError()
                    value = int(value)
                    if value < 0:
                        raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(400, "现场日期、时间或施工人数格式无效")
            setattr(record, key, value)
    validate_times(record.station_start_time, record.station_end_time)
    validate_times(record.inspection_start_time, record.inspection_end_time)
    if record.acceptance_result and record.acceptance_result not in ACCEPTANCE_RESULTS:
        raise HTTPException(400, "验收结论须为合格、不合格或待整改")
    record.record_date = record.record_date or business_date(record)
    db.add(record)
    await db.flush()
    project = await db.get(Project, project_id)
    saved_files = []
    photo_urls: list[str] = []
    try:
        for index, photo in enumerate(photos):
            raw = await _read_valid_photo(photo)
            watermarked = raw
            watermark_result = {"available": False, "recognized": False, "fields": {}, "lines": []}
            if add_system_watermark:
                watermarked = await apply_watermark_to_image(
                    raw,
                    project_name=project.name if project else "",
                    supervisor=user.real_name,
                    section=record.section or "",
                    location=record.location or "",
                    work_content=(
                        record.work_content
                        or record.witness_material_name
                        or record.station_content
                        or record.record_type
                    ),
                    weather=record.weather or "",
                )
            try:
                watermark_result = await recognize_watermark(watermarked)
            except Exception as exc:
                logger.warning("工作记录图片水印 OCR 失败：%s", type(exc).__name__)
            watermark_fields = watermark_result.get("fields") or {}
            if not isinstance(record.content_json, dict):
                record.content_json = {}
            content_json = dict(record.content_json or {})
            content_json.setdefault("evidence_watermarks", []).append({
                "labels": watermark_fields.get("labeled_items", []),
                "fields": {key: value for key, value in watermark_fields.items() if key != "labeled_items"},
                "lines": watermark_result.get("lines", []),
                "status": "recognized" if watermark_result.get("recognized") else "empty" if watermark_result.get("available") else "unavailable",
            })
            record.content_json = content_json
            if watermark_result.get("available"):
                record.section = record.section or watermark_fields.get("section") or watermark_fields.get("work_area") or None
                record.location = record.location or watermark_fields.get("location") or None
                record.weather = record.weather or watermark_fields.get("weather") or None
                if not record.record_date and watermark_fields.get("taken_at"):
                    try:
                        record.record_date = datetime.fromisoformat(str(watermark_fields["taken_at"])).date()
                    except ValueError:
                        pass
            directory = settings.upload_dir / "work-record" / str(record.id)
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
            suffix = ".jpg" if add_system_watermark else (os.path.splitext(photo.filename or "")[1].lower() or ".jpg")
            original_filename = f"{record_type}_{index}_{stamp}_original{os.path.splitext(photo.filename or '')[1].lower() or '.jpg'}"
            original_path = directory / original_filename
            original_path.write_bytes(raw)
            saved_files.append(original_path)
            filename = f"{record_type}_{index}_{stamp}_processed{suffix}"
            path = directory / filename
            path.write_bytes(watermarked)
            saved_files.append(path)
            original_url = f"/uploads/work-record/{record.id}/{original_filename}"
            url = f"/uploads/work-record/{record.id}/{filename}"
            photo_urls.append(url)
            try:
                image_taken_at = datetime.fromisoformat(str(watermark_fields["taken_at"])) if watermark_fields.get("taken_at") else datetime.now()
            except ValueError:
                image_taken_at = datetime.now()
            image_record = ImageRecord(
                    project_id=project_id,
                    uploader_id=user.id,
                    source="mobile_h5",
                    original_path=original_url,
                    watermarked_path=url,
                    taken_at=image_taken_at,
                    location=record.location,
                    section=record.section,
                    weather=record.weather,
                    remark=f"{record.record_type}现场取证",
                )
            db.add(image_record)
        if record_type == "witness":
            record.witness_photos = photo_urls
        else:
            record.inspection_photos = photo_urls

        await db.commit()
    except Exception:
        await db.rollback()
        for path in saved_files:
            path.unlink(missing_ok=True)
        raise

    # 自动生成对应的省统表文书（待定记录跳过；失败不影响记录本身）
    try:
        await auto_create_record_document(db, user, record, project, auto_generate=False)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动生成文书失败（记录已保存）：%s", exc)
        await db.rollback()

    return {
        "detail": f"{record.record_type}记录及 {len(photo_urls)} 张照片已保存",
        "record_id": record.id,
        "status": record.status,
        "photo_urls": photo_urls,
        "generated_doc_id": record.generated_doc_id,
    }


# ---- 待定工作记录（无照片入口）----


@router.post("/other")
async def create_other_work_record(
    req: CreateOtherRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建待定工作记录（不附带照片，用于视频等无法随记录上传照片的场景）"""
    record = WorkRecord(
        record_type="待定",
        status=WorkRecordStatus.DRAFT.value,
        creator_id=user.id,
        project_id=require_project_id(user),
        section=req.section,
        location=req.location,
        work_content=req.other_content or req.work_content,
        content_json={"voice_text": req.voice_text, "source": req.source or "miniprogram"},
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {
        "detail": "待定工作记录已创建",
        "record_id": record.id,
        "status": record.status,
        "photo_urls": [],
    }


# ---- 巡视记录 ----


@router.post("/inspection-with-photo")
async def create_inspection_with_photo(
    photo: UploadFile = File(...),
    section: str = Form(""),
    location: str = Form(""),
    work_content: str = Form(""),
    weather: str = Form(""),
    remark: str = Form(""),
    project_name: str = Form(""),
    unit_project: str = Form(""),
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """原子创建巡视记录并保存一张水印照片。

    移动端离线重放时无法在两个请求之间传递新生成的 record_id，因此提供
    单请求入口，保证记录和取证照片要么同时保存、要么同时失败。
    """
    raw_bytes = await _read_valid_photo(photo)
    project_id = require_project_id(user)
    project = await db.get(Project, project_id)
    record = WorkRecord(
        record_type=WorkRecordType.INSPECTION.value,
        status=WorkRecordStatus.DRAFT.value,
        creator_id=user.id,
        project_id=project_id,
        section=section or None,
        location=location or None,
        work_content=work_content or None,
        weather=weather or None,
        inspection_remark=remark or None,
    )
    db.add(record)
    await db.flush()

    watermarked = await apply_watermark_to_image(
        image_data=raw_bytes,
        project_name=project_name or (project.name if project else ""),
        supervisor=user.real_name,
        unit_project=unit_project,
        section=section,
        location=location,
        work_content=work_content,
        weather=weather,
    )
    filename = (
        f"inspection_{record.id}_0_"
        f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}.jpg"
    )
    save_path = settings.upload_dir / "inspection" / str(record.id)
    save_path.mkdir(parents=True, exist_ok=True)
    file_path = save_path / filename
    file_path.write_bytes(watermarked)
    public_path = f"/uploads/inspection/{record.id}/{filename}"
    record.inspection_photos = [public_path]

    db.add(
        ImageRecord(
            project_id=project_id,
            uploader_id=user.id,
            source="mobile_h5",
            original_path=public_path,
            watermarked_path=public_path,
            taken_at=datetime.now(),
            location=location or None,
            section=section or None,
            weather=weather or None,
            remark=remark or "移动端巡视拍照取证",
        )
    )
    try:
        await auto_create_record_document(db, user, record, project, auto_generate=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动生成文书失败（记录已保存）：%s", exc)
    await db.commit()
    await db.refresh(record)
    return {
        "detail": "巡视记录及取证照片已保存",
        "record_id": record.id,
        "image_url": public_path,
        "status": record.status,
        "generated_doc_id": record.generated_doc_id,
    }


@router.post("/inspection")
async def create_inspection(
    req: CreateInspectionRequest,
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """创建巡视记录（草稿）"""
    validate_times(req.inspection_start_time, req.inspection_end_time)
    record = WorkRecord(
        record_type=WorkRecordType.INSPECTION.value,
        status=WorkRecordStatus.DRAFT.value,
        creator_id=user.id,
        project_id=require_project_id(user),
        section=req.section,
        location=req.location,
        work_content=req.work_content,
        weather=req.weather,
        inspection_remark=req.inspection_remark,
        problems=req.problems or "",
        treatment=req.treatment or "",
        record_date=req.record_date or now_local().date(),
        worker_count=req.worker_count,
        inspection_start_time=req.inspection_start_time,
        inspection_end_time=req.inspection_end_time,
    )
    db.add(record)
    await db.flush()
    # 自动生成对应的省统表文书
    try:
        await auto_create_record_document(db, user, record, auto_generate=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动生成文书失败（记录已保存）：%s", exc)
    await db.commit()
    await db.refresh(record)
    return {"detail": "巡视记录已创建", "record_id": record.id}


@router.post("/inspection/{record_id}/photos")
async def upload_inspection_photos(
    record_id: int,
    project_name: str = Form(""),
    unit_project: str = Form(""),
    section: str = Form(""),
    work_content: str = Form(""),
    weather: str = Form(""),
    photos: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传巡视照片并添加水印（工程名称、单位工程、施工区域、施工内容、拍摄时间、天气）"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id,
            WorkRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record or record.creator_id != user.id:
        raise HTTPException(404, "记录不存在或无权操作")

    project = await db.get(Project, project_id)
    saved_files = []  # 已落盘的本地路径，失败时统一清理
    saved_paths = []
    try:
        for i, photo in enumerate(photos):
            # 读取原始图片
            raw_bytes = await _read_valid_photo(photo)
            # 添加水印
            watermarked = await apply_watermark_to_image(
                image_data=raw_bytes,
                project_name=project_name or (project.name if project else ""),
                supervisor=user.real_name,
                unit_project=unit_project,
                section=section,
                work_content=work_content,
                weather=weather,
            )
            # 保存文件
            filename = f"inspection_{record_id}_{i}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
            save_path = settings.upload_dir / "inspection" / str(record_id)
            os.makedirs(save_path, exist_ok=True)
            file_path = save_path / filename
            with open(file_path, "wb") as f:
                f.write(watermarked)
            saved_files.append(file_path)
            saved_paths.append(f"/uploads/inspection/{record_id}/{filename}")

        # 更新记录
        existing_photos = record.inspection_photos or []
        existing_photos.extend(saved_paths)
        record.inspection_photos = existing_photos
        await db.commit()
    except Exception:
        # 任一张校验/水印失败或 commit 失败：回滚并删除已写盘的照片，防孤儿文件
        await db.rollback()
        for fp in saved_files:
            Path(fp).unlink(missing_ok=True)
        raise

    return {"detail": f"已上传 {len(saved_paths)} 张照片", "paths": saved_paths}


@router.post("/inspection/{record_id}/submit")
async def submit_inspection(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """兼容旧巡视提交地址，所有提交使用同一校验与文书链路。"""
    return await submit_work_record(record_id, user, db)


# ---- 通用审批流端点（P1-3）----
# 注意：以下路由必须在 /{record_id} 之前定义，否则 "submit"/"approve" 等会被当作 record_id 解析


class RejectRequest(BaseModel):
    reason: str


async def _get_project_record(
    db: AsyncSession, project_id: int, record_id: int
) -> WorkRecord:
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id,
            WorkRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "工作记录不存在")
    return record


@router.post("/{record_id}/submit")
async def submit_work_record(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """提交审批（适用所有四类记录：巡视/验收/旁站/见证）

    状态从 草稿/进行中 -> 已提交
    """
    project_id = require_project_id(user)
    record = await _get_project_record(db, project_id, record_id)
    if record.creator_id != user.id and not user.has_permission("task_manage"):
        raise HTTPException(403, "仅创建人可提交审批")
    if record.status not in {
        WorkRecordStatus.DRAFT.value,
        WorkRecordStatus.IN_PROGRESS.value,
        WorkRecordStatus.REJECTED.value,
    }:
        raise HTTPException(400, f"当前状态「{record.status}」不可提交")
    await _validate_record_ready(db, record)
    record.status = WorkRecordStatus.SUBMITTED.value
    record.reject_reason = None
    await db.flush()
    # 提交审批 -> 补齐省统表文书（创建时已生成则跳过，老记录在此补上）
    doc = None
    try:
        doc = await auto_create_record_document(db, user, record, auto_generate=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动生成文书失败（记录已提交）：%s", exc)
    await db.commit()
    if doc and doc.docx_path:
        from app.services.project_archive import sync_document_to_project_archive

        await sync_document_to_project_archive(db, doc, user)
        await db.commit()

    # Durable responsibility first; reminders are best-effort and never replace
    # the collaboration item as the source of truth for the red badge.
    from app.services.permissions import project_members_with_permission
    from app.services.collaboration import upsert_action_items

    approver_rows = await project_members_with_permission(
        db, project_id, "document_approve"
    )
    await upsert_action_items(
        db,
        project_id=project_id,
        sender_id=user.id,
        recipient_ids=[member_user.id for _, member_user in approver_rows],
        object_type="work_record",
        object_id=record.id,
        action_type="approve",
        title=f"待审批{record.record_type}记录",
        description=record.section or record.location or "",
        source_prefix="workflow",
        retire_missing=True,
    )
    await db.commit()
    try:
        from app.services.notification import push_realtime

        for _, approver in approver_rows:
            await push_realtime(
                user_id=approver.id,
                event_type="work_record_submitted",
                data={
                    "record_id": record.id,
                    "record_type": record.record_type,
                    "submitted_by": user.real_name,
                    "section": record.section,
                },
            )
    except Exception:
        logger.exception("工作记录审批提醒发送失败（待办责任项已落库）")

    return {
        "detail": "工作记录已提交审批",
        "record_id": record_id,
        "status": record.status,
        "generated_doc_id": record.generated_doc_id,
        "doc_id": doc.id if doc else None,
    }


@router.post("/{record_id}/approve")
async def approve_work_record(
    record_id: int,
    user: User = Depends(require_permission("document_approve")),
    db: AsyncSession = Depends(get_db),
):
    """审批通过（需 document_approve 权限）

    状态 -> 已审批
    """
    project_id = require_project_id(user)
    record = await _get_project_record(db, project_id, record_id)
    if record.status != WorkRecordStatus.SUBMITTED.value:
        raise HTTPException(400, f"仅已提交状态可审批，当前状态「{record.status}」")
    await _validate_record_ready(db, record)
    record.status = WorkRecordStatus.APPROVED.value
    record.approved_by = user.id
    record.approved_at = datetime.now()
    record.reject_reason = None
    from app.services.collaboration import complete_action_items
    await complete_action_items(
        db, object_type="work_record", object_id=record.id,
        action_type="approve", actor_id=user.id, detail="审批通过",
    )
    await db.commit()

    # 推送给提交人
    try:
        from app.services.notification import push_realtime
        await push_realtime(
            user_id=record.creator_id,
            event_type="work_record_approved",
            data={
                "record_id": record.id,
                "record_type": record.record_type,
                "approved_by": user.real_name,
            },
        )
    except Exception:
        pass

    return {"detail": "工作记录已审批通过", "record_id": record_id, "status": record.status}


@router.post("/{record_id}/reject")
async def reject_work_record(
    record_id: int,
    req: RejectRequest,
    user: User = Depends(require_permission("document_approve")),
    db: AsyncSession = Depends(get_db),
):
    """驳回（需 document_approve 权限，需填 reason）

    状态 -> 已驳回
    """
    if not req.reason or not req.reason.strip():
        raise HTTPException(400, "驳回必须填写原因")
    project_id = require_project_id(user)
    record = await _get_project_record(db, project_id, record_id)
    if record.status != WorkRecordStatus.SUBMITTED.value:
        raise HTTPException(400, f"仅已提交状态可驳回，当前状态「{record.status}」")
    record.status = WorkRecordStatus.REJECTED.value
    record.reject_reason = req.reason.strip()
    record.approved_by = user.id
    record.approved_at = datetime.now()
    from app.services.collaboration import complete_action_items
    await complete_action_items(
        db, object_type="work_record", object_id=record.id,
        action_type="approve", actor_id=user.id, detail="审批驳回",
    )
    await db.commit()

    # 推送给提交人
    try:
        from app.services.notification import push_realtime
        await push_realtime(
            user_id=record.creator_id,
            event_type="work_record_rejected",
            data={
                "record_id": record.id,
                "record_type": record.record_type,
                "rejected_by": user.real_name,
                "reason": req.reason.strip(),
            },
        )
    except Exception:
        pass

    return {"detail": "工作记录已驳回", "record_id": record_id, "status": record.status}


@router.post("/{record_id}/archive")
async def archive_work_record(
    record_id: int,
    user: User = Depends(require_permission("document_approve")),
    db: AsyncSession = Depends(get_db),
):
    """归档（需 document_approve 权限）

    状态 -> 已归档
    """
    project_id = require_project_id(user)
    record = await _get_project_record(db, project_id, record_id)
    if record.status not in {WorkRecordStatus.APPROVED.value, WorkRecordStatus.SUBMITTED.value}:
        raise HTTPException(400, f"当前状态「{record.status}」不可归档")
    await _validate_record_ready(db, record)
    record.status = WorkRecordStatus.ARCHIVED.value
    if not record.approved_by:
        record.approved_by = user.id
        record.approved_at = datetime.now()
    from app.services.collaboration import complete_action_items
    await complete_action_items(
        db, object_type="work_record", object_id=record.id,
        action_type="approve", actor_id=user.id, detail="归档完成",
    )
    await db.commit()
    return {"detail": "工作记录已归档", "record_id": record_id, "status": record.status}


# ---- 验收记录 ----

@router.post("/acceptance")
async def create_acceptance(
    req: CreateAcceptanceRequest,
    user: User = Depends(require_permission("acceptance")),
    db: AsyncSession = Depends(get_db),
):
    """创建验收记录（基于知识图谱验收指引生成省统表）"""
    if req.acceptance_result and req.acceptance_result not in ACCEPTANCE_RESULTS:
        raise HTTPException(400, "验收结论须为合格、不合格或待整改")
    # 如果传了 guide_ref（知识图谱实体ID），获取实体名称作为 guide_ref 文本
    guide_ref_text = req.acceptance_guide_ref
    if req.guide_ref and not guide_ref_text:
        from app.models.knowledge_graph import KGEntity
        ent_result = await db.execute(select(KGEntity).where(KGEntity.id == req.guide_ref))
        ent = ent_result.scalar_one_or_none()
        if ent:
            guide_ref_text = ent.name

    record = WorkRecord(
        record_type=WorkRecordType.ACCEPTANCE.value,
        status=WorkRecordStatus.DRAFT.value,
        creator_id=user.id,
        project_id=require_project_id(user),
        section=req.section,
        location=req.location,
        acceptance_guide_ref=guide_ref_text,
        acceptance_result=req.acceptance_result,
        record_date=req.record_date or now_local().date(),
        acceptance_participants=req.acceptance_participants,
        acceptance_basis=req.acceptance_basis or "",
        treatment=req.treatment or "",
        content_json={
            "acceptance_type": req.acceptance_type,
            "acceptance_content": req.acceptance_content,
        },
    )
    db.add(record)
    await db.flush()

    project = await db.get(Project, record.project_id)
    if not record.acceptance_participants:
        record.acceptance_participants = getattr(project, "acceptance_participants", None)
    doc = await auto_create_record_document(db, user, record, project, auto_generate=False)

    await db.commit()
    await db.refresh(record)

    return {
        "detail": "验收记录已创建" + ("并生成省统表" if doc else ""),
        "record_id": record.id,
        "doc_id": doc.id if doc else None,
    }


# ---- 见证记录 ----

@router.post("/witness")
async def create_witness(
    req: CreateWitnessRequest,
    user: User = Depends(require_permission("witness")),
    db: AsyncSession = Depends(get_db),
):
    """创建见证记录"""
    record = WorkRecord(
        record_type=WorkRecordType.WITNESS.value,
        status=WorkRecordStatus.DRAFT.value,
        creator_id=user.id,
        project_id=require_project_id(user),
        section=req.section,
        location=req.location,
        witness_material_name=req.witness_material_name,
        witness_material_spec=req.witness_material_spec,
        witness_quantity=req.witness_quantity or "",
        witness_unit=req.witness_unit or "",
        witness_cert_no=req.witness_cert_no or "",
        witness_accept_result=req.witness_accept_result or "",
        content_json={
            "manufacturer": req.manufacturer,
            "batch_no": req.batch_no,
        },
        remark=req.remark,
    )
    db.add(record)
    await db.flush()
    # 自动生成对应的省统表文书（见证取样记录 / GD-B-219）
    try:
        await auto_create_record_document(db, user, record, auto_generate=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动生成文书失败（记录已保存）：%s", exc)
    await db.commit()
    await db.refresh(record)
    return {
        "detail": "见证记录已创建",
        "record_id": record.id,
        "generated_doc_id": record.generated_doc_id,
    }


@router.post("/witness/{record_id}/photos")
async def upload_witness_photos(
    record_id: int,
    photos: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传见证拍照材料"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id,
            WorkRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record or record.creator_id != user.id:
        raise HTTPException(404, "记录不存在或无权操作")

    project = await db.get(Project, project_id)
    saved_files = []  # 已落盘的本地路径，失败时统一清理
    saved_paths = []
    try:
        for i, photo in enumerate(photos):
            raw_bytes = await _read_valid_photo(photo)
            watermarked = await apply_watermark_to_image(
                raw_bytes,
                project_name=project.name if project else "",
                supervisor=user.real_name,
                section=record.section or "",
                location=record.location or "",
                work_content=record.witness_material_name or "见证取样",
            )
            filename = f"witness_{record_id}_{i}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
            save_path = settings.upload_dir / "witness" / str(record_id)
            os.makedirs(save_path, exist_ok=True)
            file_path = save_path / filename
            with open(file_path, "wb") as f:
                f.write(watermarked)
            saved_files.append(file_path)
            saved_paths.append(f"/uploads/witness/{record_id}/{filename}")

        existing = record.witness_photos or []
        existing.extend(saved_paths)
        record.witness_photos = existing
        await db.commit()
    except Exception:
        # 任一张失败或 commit 失败：回滚并删除已写盘照片，防孤儿文件
        await db.rollback()
        for fp in saved_files:
            Path(fp).unlink(missing_ok=True)
        raise

    return {"detail": f"已上传 {len(saved_paths)} 张见证照片", "paths": saved_paths}


@router.post("/witness/{record_id}/report")
async def generate_witness_report(
    record_id: int,
    user: User = Depends(require_permission("witness")),
    db: AsyncSession = Depends(get_db),
):
    """根据见证记录和现场照片生成可归档 PDF 报告。"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id,
            WorkRecord.project_id == project_id,
            WorkRecord.record_type == WorkRecordType.WITNESS.value,
        )
    )
    record = result.scalar_one_or_none()
    if not record or (
        record.creator_id != user.id
        and not user.has_permission("task_manage")
    ):
        raise HTTPException(404, "见证记录不存在或无权操作")

    doc = await db.get(Document, record.generated_doc_id) if record.generated_doc_id else None
    content_json = record.content_json or {}
    content = {
        "doc_number": f"GD-W-{record.id:06d}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "supervisor": user.real_name,
        "section": record.section or "",
        "location": record.location or "",
        "material_name": record.witness_material_name or "",
        "material_spec": record.witness_material_spec or "",
        "manufacturer": content_json.get("manufacturer") or "",
        "batch_no": content_json.get("batch_no") or "",
        "remark": record.remark or "",
    }
    if doc is None:
        doc = Document(
            doc_type=DocType.WITNESS_SAMPLING.value,
            title=f"工作记录_{business_date(record).strftime('%Y-%m-%d')}",
            content=content,
            section=record.section,
            location=record.location,
            project_id=project_id,
            creator_id=record.creator_id,
            status=DocStatus.ARCHIVED.value,
            attached_images=record.witness_photos or [],
        )
        db.add(doc)
        await db.flush()
        record.generated_doc_id = doc.id
    else:
        doc.content = content
        doc.attached_images = record.witness_photos or []
        doc.status = DocStatus.ARCHIVED.value

    from app.services.doc_generator import generate_pdf
    filename = f"witness_{record.id}_{doc.id}.pdf"
    output_path = settings.export_dir / filename
    try:
        generate_pdf(doc, str(output_path))
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"见证报告生成失败：{exc}") from exc
    doc.pdf_path = f"/exports/{filename}"
    record.witness_report_path = doc.pdf_path
    await _validate_record_ready(db, record)
    record.status = WorkRecordStatus.ARCHIVED.value
    from app.services.collaboration import complete_action_items
    await complete_action_items(
        db, object_type="work_record", object_id=record.id,
        action_type="approve", actor_id=user.id, detail="见证报告生成并归档",
    )
    await db.commit()
    from app.services.project_archive import sync_document_to_project_archive

    await sync_document_to_project_archive(db, doc, user)
    await db.commit()
    return {
        "detail": "见证报告已生成并归档",
        "record_id": record.id,
        "doc_id": doc.id,
        "pdf_url": doc.pdf_path,
    }


# ---- 旁站记录 ----

@router.post("/station")
async def create_station(
    req: CreateStationRequest,
    user: User = Depends(require_permission("station")),
    db: AsyncSession = Depends(get_db),
):
    """创建旁站记录"""
    validate_times(req.start_time, req.end_time)
    record = WorkRecord(
        record_type=WorkRecordType.STATION.value,
        status=WorkRecordStatus.DRAFT.value,
        creator_id=user.id,
        project_id=require_project_id(user),
        section=req.section,
        location=req.location,
        weather=req.weather,
        station_project_name=req.station_project_name,
        station_location=req.station_location,
        station_content=req.station_content,
        station_supervision_status=req.station_supervision_status or "",
        station_conclusion=req.station_conclusion,
        problems=req.problems or "",
        treatment=req.treatment or "",
        station_start_time=req.start_time or now_local(),
        station_end_time=req.end_time,
        station_process=req.station_process,
        record_date=req.record_date or (req.start_time or now_local()).date(),
    )
    db.add(record)
    await db.flush()
    # 自动生成对应的省统表文书（旁站未结束时跳过，结束时由 /station/{id} 补）
    try:
        await auto_create_record_document(db, user, record, auto_generate=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动生成文书失败（记录已保存）：%s", exc)
    await db.commit()
    await db.refresh(record)
    return {
        "detail": "旁站记录已创建",
        "record_id": record.id,
        "generated_doc_id": record.generated_doc_id,
    }


@router.put("/station/{record_id}")
async def update_station_end_time(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """结束旁站记录"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id,
            WorkRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record or record.creator_id != user.id:
        raise HTTPException(404, "记录不存在或无权操作")
    if record.record_type != WorkRecordType.STATION.value or record.status != WorkRecordStatus.DRAFT.value:
        raise HTTPException(409, "仅可结束草稿旁站记录")
    record.station_end_time = now_local()
    validate_record(record, await db.get(Project, record.project_id))
    record.status = WorkRecordStatus.DRAFT.value
    await db.flush()
    # 旁站结束 -> 补生成省统表文书
    try:
        await auto_create_record_document(db, user, record, auto_generate=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动生成文书失败（记录已保存）：%s", exc)
    await db.commit()
    return {
        "detail": "旁站记录已结束，请完成签认后提交",
        "record_id": record.id,
        "generated_doc_id": record.generated_doc_id,
    }


# ---- 通用操作 ----

@router.get("/list")
async def list_work_records(
    record_type: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询工作记录列表（按项目返回，同一项目内所有用户可见，支持眼镜端记录同步到小程序）

    支持按创建日期范围过滤（start_date/end_date，含端点），与小程序端时间范围筛选对应。
    公司级账号带 ``all_projects=1`` 时不限项目（顶部「全部项目」视角）。
    """
    scope = project_scope_id(user, all_projects)
    query = select(WorkRecord)
    if scope is not None:
        query = query.where(WorkRecord.project_id == scope)
    if record_type:
        query = query.where(WorkRecord.record_type == record_type)
    if status:
        query = query.where(WorkRecord.status == status)
    if start_date:
        query = query.where(WorkRecord.created_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.where(WorkRecord.created_at <= datetime.combine(end_date, datetime.max.time()))
    query = query.order_by(WorkRecord.created_at.desc())
    result = await db.execute(query)
    records = result.scalars().all()
    project_ids = {item.project_id for item in records if item.project_id is not None}
    project_rows = (await db.execute(
        select(Project).where(Project.id.in_(project_ids))
    )).scalars().all() if project_ids else []
    projects_by_id = {item.id: item for item in project_rows}
    from app.services.record_fields import business_record_fields

    items = []
    for r in records:
        business = business_record_fields(r, projects_by_id.get(r.project_id))
        items.append({
            "id": r.id,
            "record_type": r.record_type,
            "status": r.status,
            "project_id": r.project_id,
            "project_name": business.get("business_project_name") or "",
            "system_project_name": business.get("system_project_name") or "",
            "project_mismatch": bool(business.get("project_mismatch")),
            "construction_unit": business.get("construction_unit") or "",
            "section": r.section,
            "location": r.location,
            "work_content": r.work_content,
            "weather": r.weather,
            "inspection_remark": r.inspection_remark,
            "acceptance_guide_ref": r.acceptance_guide_ref,
            "acceptance_result": r.acceptance_result,
            "generated_doc_id": r.generated_doc_id,
            "content_json": r.content_json,
            "witness_material_name": r.witness_material_name,
            "witness_material_spec": r.witness_material_spec,
            "witness_report_path": r.witness_report_path,
            "station_project_name": business.get("business_project_name") or r.station_project_name,
            "station_process": r.station_process,
            "station_start_time": r.station_start_time.isoformat() if r.station_start_time else None,
            "station_end_time": r.station_end_time.isoformat() if r.station_end_time else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {
        "total": len(records),
        "records": items,
    }


# ---- 统计：未查看/未处理数量 ----

@router.get("/stats")
async def work_record_stats(
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取工作记录和隐患的未查看/未处理统计

    工作记录：未查看(viewed=false) 或 未处理(status=草稿/已提交)
    安全隐患：未处理(status != 已闭环 且 status != 驳回整改)
    """
    scope = project_scope_id(user, all_projects)
    # scope 为 None = 「全部项目」视角：where 里不加项目过滤
    wr_scope = WorkRecord.project_id == scope if scope is not None else True
    from app.models.hazard import Hazard, HazardStatus
    hz_scope = Hazard.project_id == scope if scope is not None else True

    # 工作记录 - 未查看
    unviewed_by_type = {}
    for rtype in ["巡视", "验收", "旁站", "见证"]:
        result = await db.execute(
            select(func.count(WorkRecord.id)).where(
                and_(
                    wr_scope,
                    WorkRecord.record_type == rtype,
                    WorkRecord.viewed == False,
                )
            )
        )
        unviewed_by_type[rtype] = result.scalar() or 0

    # 工作记录 - 未处理（草稿+已提交）
    pending_by_type = {}
    for rtype in ["巡视", "验收", "旁站", "见证"]:
        result = await db.execute(
            select(func.count(WorkRecord.id)).where(
                and_(
                    wr_scope,
                    WorkRecord.record_type == rtype,
                    WorkRecord.status.in_([
                        WorkRecordStatus.DRAFT.value,
                        WorkRecordStatus.IN_PROGRESS.value,
                        WorkRecordStatus.SUBMITTED.value,
                    ]),
                )
            )
        )
        pending_by_type[rtype] = result.scalar() or 0

    # 其他工作记录（非四种类型的）
    other_unviewed_result = await db.execute(
        select(func.count(WorkRecord.id)).where(
            and_(
                wr_scope,
                ~WorkRecord.record_type.in_(["巡视", "验收", "旁站", "见证"]),
                WorkRecord.viewed == False,
            )
        )
    )
    other_unviewed = other_unviewed_result.scalar() or 0

    other_pending_result = await db.execute(
        select(func.count(WorkRecord.id)).where(
            and_(
                wr_scope,
                ~WorkRecord.record_type.in_(["巡视", "验收", "旁站", "见证"]),
                WorkRecord.status.in_([
                    WorkRecordStatus.DRAFT.value,
                    WorkRecordStatus.IN_PROGRESS.value,
                    WorkRecordStatus.SUBMITTED.value,
                ]),
            )
        )
    )
    other_pending = other_pending_result.scalar() or 0

    # 安全隐患 - 未处理
    from app.models.hazard import Hazard, HazardStatus
    hazard_pending_result = await db.execute(
        select(func.count(Hazard.id)).where(
            and_(
                hz_scope,
                ~Hazard.status.in_([HazardStatus.CLOSED.value, HazardStatus.REJECTED.value]),
            )
        )
    )
    hazard_pending = hazard_pending_result.scalar() or 0

    day_start = datetime.combine(date.today(), datetime.min.time())
    today_inspection_result = await db.execute(
        select(func.count(WorkRecord.id)).where(
            wr_scope,
            WorkRecord.record_type == WorkRecordType.INSPECTION.value,
            WorkRecord.created_at >= day_start,
        )
    )
    today_hazard_result = await db.execute(
        select(func.count(Hazard.id)).where(
            hz_scope,
            Hazard.created_at >= day_start,
        )
    )

    return {
        "work_records": {
            "巡视": {"unviewed": unviewed_by_type["巡视"], "pending": pending_by_type["巡视"]},
            "验收": {"unviewed": unviewed_by_type["验收"], "pending": pending_by_type["验收"]},
            "旁站": {"unviewed": unviewed_by_type["旁站"], "pending": pending_by_type["旁站"]},
            "见证": {"unviewed": unviewed_by_type["见证"], "pending": pending_by_type["见证"]},
            "其他": {"unviewed": other_unviewed, "pending": other_pending},
        },
        "hazards": {"pending": hazard_pending},
        "today_inspection": today_inspection_result.scalar() or 0,
        "today_hazard": today_hazard_result.scalar() or 0,
    }


@router.put("/{record_id}/viewed")
async def mark_record_viewed(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """标记工作记录为已查看"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id,
            WorkRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "记录不存在")
    record.viewed = True
    await db.commit()
    return {"detail": "已标记为已查看", "record_id": record_id}


# ---- 巡检指引（从知识图谱拉取验收流程）----
# 注意：此路由必须在 /{record_id} 之前定义，否则 "guides" 会被当作 record_id 解析

@router.get("/guides")
async def list_guides(
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取知识图谱中的验收/巡检流程指引列表"""
    from app.models.knowledge_graph import KGEntity
    query = select(KGEntity).where(KGEntity.entity_type.in_(["验收流程", "监理要点", "施工工序"]))
    if category:
        query = query.where(KGEntity.entity_type == category)
    if keyword:
        query = query.where(KGEntity.name.like(f"%{keyword}%"))
    query = query.order_by(KGEntity.created_at.desc()).limit(50)
    result = await db.execute(query)
    entities = result.scalars().all()
    return {
        "total": len(entities),
        "guides": [
            {
                "id": e.id,
                "name": e.name,
                "title": e.name,
                "type": e.entity_type,
                "description": e.description,
                "source_doc": e.source_doc,
            }
            for e in entities
        ],
    }


@router.get("/{record_id}")
async def get_work_record(
    record_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取工作记录详情"""
    scope = project_scope_id(user, all_projects)
    stmt = select(WorkRecord).where(WorkRecord.id == record_id)
    if scope is not None:
        stmt = stmt.where(WorkRecord.project_id == scope)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    collaborator_access = False
    if record and record.creator_id != user.id and not user.has_permission("task_manage"):
        from app.models.collaboration import CollaborationFollower, CollaborationItem

        collaborator_access = bool(await db.scalar(
            select(CollaborationItem.id).where(
                CollaborationItem.project_id == record.project_id,
                CollaborationItem.object_type == "work_record",
                CollaborationItem.object_id == record.id,
                or_(
                    CollaborationItem.sender_id == user.id,
                    CollaborationItem.recipient_id == user.id,
                    exists(select(CollaborationFollower.id).where(
                        CollaborationFollower.item_id == CollaborationItem.id,
                        CollaborationFollower.user_id == user.id,
                    )),
                ),
            ).limit(1)
        ))
    if not record or (record.creator_id != user.id and not user.has_permission("task_manage") and not collaborator_access):
        raise HTTPException(404, "记录不存在或无权查看")
    # 监理人员 = 记录创建人（GD-B-214 等省统表「监理人员」栏）
    creator_name = (await db.execute(
        select(User.real_name).where(User.id == record.creator_id)
    )).scalar_one_or_none()
    # 系统栏（工程项目名称/施工单位）：按统表表头口径回填，详情页与导出文书一致
    from app.services.record_fields import business_record_fields, project_fields
    project = (await db.execute(
        select(Project).where(Project.id == record.project_id)
    )).scalar_one_or_none()
    pj = project_fields(project)
    business = business_record_fields(record, project)
    confirmed = (record.content_json or {}).get("confirmed_fields") or (record.content_json or {}).get("field_resolutions") or {}
    prov = (record.content_json or {}).get("prov_fields") or {}
    def confirmed_value(key: str, fallback: Any = None) -> Any:
        value = confirmed.get(key) if isinstance(confirmed, dict) else None
        if isinstance(value, dict):
            value = value.get("value")
        if value in (None, "") and isinstance(prov, dict):
            value = prov.get(key)
        return fallback if value in (None, "") else value
    # 兜底：从统一媒体表取关联到本记录的图片，防止旧数据或同步遗漏导致详情页看不到照片。
    media_result = await db.execute(
        select(MediaRecord).where(
            MediaRecord.business_type == "work_record",
            MediaRecord.business_id == record.id,
            MediaRecord.media_type == MediaType.IMAGE.value,
        )
    )
    media_rows = list(media_result.scalars().all())
    media_urls = [r.processed_path or r.original_path for r in media_rows]
    media_evidence = [
        {
            "id": r.id,
            "url": r.processed_path or r.original_path,
            "section": r.section,
            "location": r.location,
            "weather": r.weather,
            "capture_time": (r.metadata_json or {}).get("capture_time"),
            "capture_time_source": (r.metadata_json or {}).get("capture_time_source"),
            "watermark": (r.metadata_json or {}).get("watermark") or {},
        }
        for r in media_rows
    ]
    if record.record_type == "见证":
        witness_photos = list(dict.fromkeys((record.witness_photos or []) + media_urls))
        inspection_photos = record.inspection_photos
    else:
        inspection_photos = list(dict.fromkeys((record.inspection_photos or []) + media_urls))
        witness_photos = record.witness_photos
    return {
        "id": record.id,
        "record_type": record.record_type,
        "status": record.status,
        "supervisor": creator_name or None,
        "project_name": confirmed_value("project_name", business.get("business_project_name") or pj.get("project_name")) or None,
        "business_project_name": business.get("business_project_name") or None,
        "system_project_name": business.get("system_project_name") or pj.get("project_name") or None,
        "watermark_project_name": business.get("watermark_project_name") or None,
        "project_mismatch": bool(business.get("project_mismatch")),
        "business_field_conflicts": business.get("field_conflicts") or {},
        "business_field_candidates": business.get("field_candidates") or {},
        "construction_unit": confirmed_value("construction_unit", business.get("construction_unit") or pj.get("construction_unit")) or None,
        "owner_unit": business.get("owner_unit") or None,
        "supervision_unit": business.get("supervision_unit") or pj.get("supervision_unit") or None,
        "supervision_org": business.get("supervision_org") or pj.get("supervision_org") or None,
        # The record creator is an audit actor, not the inspector identified by
        # the watermark or confirmed in the Agent flow.
        "inspector": business.get("inspector") or None,
        "section": record.section,
        "location": record.location,
        "weather": record.weather,
        "work_content": record.work_content,
        "generated_doc_id": record.generated_doc_id,
        "inspection_photos": inspection_photos,
        "inspection_items": record.inspection_items,
        "inspection_remark": record.inspection_remark,
        "problems": record.problems,
        "treatment": record.treatment,
        "acceptance_guide_ref": record.acceptance_guide_ref,
        "acceptance_result": record.acceptance_result,
        "acceptance_basis": record.acceptance_basis,
        "generated_doc_id": record.generated_doc_id,
        "witness_material_name": record.witness_material_name,
        "witness_material_spec": record.witness_material_spec,
        "witness_quantity": record.witness_quantity,
        "witness_unit": record.witness_unit,
        "witness_cert_no": record.witness_cert_no,
        "witness_accept_result": record.witness_accept_result,
        "witness_photos": witness_photos,
        "witness_report_path": record.witness_report_path,
        "station_project_name": business.get("business_project_name") or record.station_project_name,
        "prov_fields": prov,
        "form_fields": ((record.content_json or {}).get("form_requirements") or {}).get("fields") or [],
        "record_date": business_date(record).isoformat(),
        "worker_count": record.worker_count,
        "inspection_start_time": record.inspection_start_time.isoformat() if record.inspection_start_time else None,
        "inspection_end_time": record.inspection_end_time.isoformat() if record.inspection_end_time else None,
        "station_process": record.station_process,
        "acceptance_participants": record.acceptance_participants,
        "acceptance_type": (record.content_json or {}).get("acceptance_type", ""),
        "acceptance_content": (record.content_json or {}).get("acceptance_content", ""),
        "station_location": record.station_location,
        "station_start_time": record.station_start_time.isoformat() if record.station_start_time else None,
        "station_end_time": record.station_end_time.isoformat() if record.station_end_time else None,
        "station_content": record.station_content,
        "station_supervision_status": record.station_supervision_status,
        "station_conclusion": record.station_conclusion,
        "content_json": record.content_json,
        "media_evidence": media_evidence,
        "remark": record.remark,
        "reject_reason": record.reject_reason,
        "approved_by": record.approved_by,
        "approved_at": record.approved_at.isoformat() if record.approved_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.put("/{record_id}")
async def update_work_record(
    record_id: int,
    req: UpdateWorkRecordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新工作记录"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id,
            WorkRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record or (record.creator_id != user.id and not user.has_permission("task_manage")):
        raise HTTPException(404, "记录不存在或无权操作")

    update_data = req.model_dump(exclude_unset=True)
    if 'status' in update_data and update_data['status'] != record.status:
        raise HTTPException(400, "状态变更请使用提交、审批、驳回或归档操作")
    validate_times(update_data.get("station_start_time", record.station_start_time), update_data.get("station_end_time", record.station_end_time))
    validate_times(update_data.get("inspection_start_time", record.inspection_start_time), update_data.get("inspection_end_time", record.inspection_end_time))
    if update_data.get("acceptance_result") and update_data["acceptance_result"] not in ACCEPTANCE_RESULTS:
        raise HTTPException(400, "验收结论须为合格、不合格或待整改")
    linked_doc = await db.get(Document, record.generated_doc_id) if record.generated_doc_id else None
    if linked_doc and linked_doc.status not in (DocStatus.DRAFT.value, DocStatus.REJECTED.value):
        raise HTTPException(409, "已提交或归档的文书请先驳回再修改记录")
    if "status" in update_data:
        valid_statuses = {status.value for status in WorkRecordStatus}
        if update_data["status"] not in valid_statuses:
            raise HTTPException(400, "工作记录状态非法")
        if (
            record.creator_id == user.id
            and not user.has_permission("task_manage")
            and update_data["status"] not in {
                WorkRecordStatus.DRAFT.value,
                WorkRecordStatus.IN_PROGRESS.value,
                WorkRecordStatus.SUBMITTED.value,
            }
        ):
            raise HTTPException(403, "当前角色无权审批或归档工作记录")
    if (
        record.status == WorkRecordStatus.ARCHIVED.value
        and not user.has_permission("task_manage")
    ):
        raise HTTPException(409, "已归档工作记录不能修改")

    direct_fields = [
        'status', 'remark', 'content_json',
        'section', 'location', 'weather', 'work_content', 'inspection_remark',
        'problems', 'treatment',
        'acceptance_guide_ref', 'acceptance_result', 'acceptance_basis',
        'witness_material_name', 'witness_material_spec',
        'witness_quantity', 'witness_unit', 'witness_cert_no', 'witness_accept_result',
        'station_project_name', 'station_location', 'station_content',
        'station_supervision_status', 'station_conclusion',
        'station_start_time', 'station_end_time', 'record_date', 'station_process',
        'inspection_start_time', 'inspection_end_time', 'worker_count', 'acceptance_participants',
    ]
    for field in direct_fields:
        if field in update_data:
            setattr(record, field, update_data[field])

    if (
        update_data.get("status") == WorkRecordStatus.SUBMITTED.value
        and record.generated_doc_id
    ):
        generated_doc = await db.get(Document, record.generated_doc_id)
        if generated_doc and generated_doc.doc_type not in {
            DocType.RECTIFICATION.value,
            DocType.WEEKLY.value,
            DocType.MONTHLY.value,
        }:
            generated_doc.status = DocStatus.ARCHIVED.value

    if 'acceptance_type' in update_data or 'acceptance_content' in update_data:
        cj = record.content_json or {}
        if 'acceptance_type' in update_data:
            cj['acceptance_type'] = update_data['acceptance_type']
        if 'acceptance_content' in update_data:
            cj['acceptance_content'] = update_data['acceptance_content']
        record.content_json = cj

    field_edits = _record_field_edit_snapshot(record, set(update_data))
    if field_edits:
        content_json = dict(record.content_json or {})
        previous_edits = content_json.get("record_field_edits")
        if not isinstance(previous_edits, dict):
            previous_edits = {}
        content_json["record_field_edits"] = {**previous_edits, **field_edits}
        record.content_json = content_json

    if linked_doc:
        from app.routers.form_doc import _content_from_record, _record_attach_photos
        from app.services.record_fields import project_fields
        project = await db.get(Project, record.project_id)
        content = _content_from_record(
            record, await db.get(User, record.creator_id), project
        )
        for key, value in project_fields(project).items():
            if not str(content.get(key) or "").strip():
                content[key] = value
        content["doc_number"] = (linked_doc.content or {}).get("doc_number", "")
        for key in ("signing", "offline_signoffs"):
            if (linked_doc.content or {}).get(key) is not None:
                content[key] = (linked_doc.content or {})[key]
        linked_doc.content = content
        linked_doc.section = record.section
        linked_doc.location = record.location
        linked_doc.attached_images = await _record_attach_photos(db, record)
        linked_doc.status = DocStatus.DRAFT.value
        invalidate_document(linked_doc)
    await db.commit()
    if linked_doc:
        from app.services.project_archive import sync_document_to_project_archive

        # Keep the project archive copy in step with record edits. The archive
        # helper re-renders when invalidation removed the previous file.
        await sync_document_to_project_archive(db, linked_doc, user)
        await db.commit()
    return {"detail": "记录已更新"}


@router.delete("/{record_id}")
async def delete_work_record(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除工作记录（创建人或具备 task_manage 权限者）。"""
    project_id = require_project_id(user)
    record = await _get_project_record(db, project_id, record_id)
    if record.creator_id != user.id and not user.has_permission("task_manage"):
        raise HTTPException(403, "仅创建人或管理人员可删除工作记录")

    from app.services.collaboration import cancel_action_items

    await cancel_action_items(
        db,
        object_type="work_record",
        object_id=record.id,
        actor_id=user.id,
        detail="工作记录已删除",
    )
    photo_paths = list(record.inspection_photos or []) + list(record.witness_photos or [])
    # 🔴 task_assignments.work_record_id → work_records.id 是无级联外键：
    # 记录被派发过待办任务时直接删会抛 1451 → 500。先解绑（任务保留，只是不再指向已删记录）。
    from app.models.task_assignment import TaskAssignment

    await db.execute(
        update(TaskAssignment)
        .where(TaskAssignment.work_record_id == record_id)
        .values(work_record_id=None)
    )
    await db.delete(record)
    await db.commit()

    for public_path in photo_paths:
        if isinstance(public_path, str) and public_path.startswith("/uploads/"):
            try:
                (settings.upload_dir / public_path[len("/uploads/"):]).unlink(missing_ok=True)
            except OSError:
                pass
    return {"detail": "工作记录已删除", "record_id": record_id}


# ---- 待定/质量问题记录（眼镜端智能体未能确定类型时兜底） ----

@router.post("/other")
async def create_other_record(
    req: CreateOtherRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建待定工作记录。

    现场工作流：语音唤醒/开始记录 → 拍照/视频+语音 → 智能体自动匹配类型 →
    未能确定类型的记录统一归为"待定"，后续由操作者在小程序/Web后台贴标签。
    眼镜端 WorkRecordService 将"待定/其他工作"映射为 other 上传至此。
    """
    content = req.other_content or req.work_content
    record = WorkRecord(
        record_type="待定",
        status=WorkRecordStatus.DRAFT.value,
        creator_id=user.id,
        project_id=require_project_id(user),
        section=req.section,
        location=req.location,
        work_content=content,
        content_json={
            "voice_text": req.voice_text,
            "source": "glasses",
        },
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {"detail": "待定工作记录已创建，请稍后贴标签", "record_id": record.id}


@router.post("/quality")
async def create_quality_record(
    req: CreateQualityRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """质量问题记录（眼镜端兼容路由）：统一归为待定，原始类型保留在 content_json。"""
    record = WorkRecord(
        record_type="待定",
        status=WorkRecordStatus.DRAFT.value,
        creator_id=user.id,
        project_id=require_project_id(user),
        section=req.section,
        location=req.location,
        work_content=req.quality_content or req.work_content,
        content_json={
            "original_type": "质量",
            "source": "glasses",
        },
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {"detail": "质量问题已记录（待定），请稍后贴标签", "record_id": record.id}


# ---- 类型标签修改（待定 → 旁站/巡视/验收/见证） ----

# 说明：微信小程序 wx.request 不支持 PATCH 方法，故用 POST /set-type
@router.post("/{record_id}/set-type")
async def update_record_type(
    record_id: int,
    req: UpdateRecordTypeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改工作记录类型标签。

    对应现场机制：智能体未能确定的"待定"记录，操作者在小程序/Web后台
    人工选择或更改为 旁站/巡视/验收/见证取样；已确定类型也可重新标注。
    """
    valid_types = {t.value for t in WorkRecordType}
    if req.record_type not in valid_types:
        raise HTTPException(400, f"不支持的工作记录类型，可选：{'、'.join(valid_types)}")

    project_id = require_project_id(user)
    result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id,
            WorkRecord.project_id == project_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record or (record.creator_id != user.id and not user.has_permission("task_manage")):
        raise HTTPException(404, "记录不存在或无权操作")
    if record.status == WorkRecordStatus.ARCHIVED.value and not user.has_permission("task_manage"):
        raise HTTPException(409, "已归档工作记录不能修改类型")

    old_type = record.record_type
    record.record_type = req.record_type
    cj = record.content_json or {}
    cj["type_history"] = (cj.get("type_history") or []) + [
        {"from": old_type, "to": req.record_type, "by": user.real_name or user.username,
         "at": datetime.now().isoformat(timespec="seconds")}
    ]
    record.content_json = cj

    # 待定记录被确定为具体类型后 -> 生成对应的省统表文书
    doc = None
    try:
        doc = await auto_create_record_document(db, user, record, auto_generate=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自动生成文书失败（类型已修改）：%s", exc)
    await db.commit()
    return {
        "detail": f"类型已从「{old_type}」改为「{req.record_type}」",
        "record_type": record.record_type,
        "generated_doc_id": record.generated_doc_id,
        "doc_id": doc.id if doc else None,
    }


# ---- 记录分享（上传/转发） ----

class ShareWorkRecordRequest(BaseModel):
    target: str                       # role | user
    group: Optional[str] = None       # target=role：监理部门 | 总监
    user_id: Optional[int] = None     # target=user：接收人 id
    form_code: Optional[str] = None   # 不传则按记录类型取默认表式


# 监理部门 = 项目内除总监外的监理人员；总监 = 总监理工程师
# Step 2（S2 路线）：组内岗位改用英文 key，与 project_members.role 同源。
# 对外仍是中文组名（前端下拉的取值），只在查库时映射成 key。
SHARE_ROLE_GROUPS: dict[str, list[str]] = {
    "监理部门": [
        UserRole.SPECIAL_SUPERVISOR.value,
        UserRole.INSPECTOR.value,
        UserRole.DEPUTY_CHIEF.value,
    ],
    "总监": [UserRole.CHIEF_SUPERVISOR.value],
}


@router.post("/{record_id}/share")
async def share_work_record(
    record_id: int,
    req: ShareWorkRecordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """生成省统表文书并送达指定角色/成员（上传/转发）。

    - target=role：按 SHARE_ROLE_GROUPS 推送所有匹配成员
    - target=user：推送给指定 project 内成员（转发生成的文档）
    """
    project_id = require_project_id(user)
    record_result = await db.execute(
        select(WorkRecord).where(
            WorkRecord.id == record_id, WorkRecord.project_id == project_id
        )
    )
    record = record_result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "工作记录不存在")
    if record.creator_id != user.id and not user.has_permission("task_manage"):
        raise HTTPException(403, "无权分享该记录")

    # 确定接收人
    if req.target == "role":
        roles = SHARE_ROLE_GROUPS.get(req.group or "", [])
        if not roles:
            raise HTTPException(400, "上传目标仅支持：监理部门 / 总监")
        # Step 2（设计说明 §7.8.4）：改按本项目成员表的岗位集合（主岗 ∪ 附加岗）
        from app.services.permissions import member_user_ids_with_roles

        target_ids = await member_user_ids_with_roles(db, project_id, roles)
        members_result = await db.execute(
            select(User).where(
                User.id.in_(target_ids or [-1]),
                User.is_active.is_(True),
            )
        )
    elif req.target == "user":
        if not req.user_id:
            raise HTTPException(400, "请选择接收人")
        if req.user_id == user.id:
            raise HTTPException(400, "不能转发给自己")
        # 接收人必须是本项目**有效成员**（归属判定，非上下文判定）
        member_row = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == req.user_id,
                ProjectMember.left_at.is_(None),
            )
        )
        if member_row.first() is None:
            raise HTTPException(404, "接收人不是本项目成员")
        members_result = await db.execute(
            select(User).where(
                User.id == req.user_id,
                User.is_active.is_(True),
            )
        )
    else:
        raise HTTPException(400, "target 仅支持 role / user")

    recipients = members_result.scalars().all()
    if not recipients:
        raise HTTPException(400, "当前项目没有匹配的接收人")

    # 生成文书并落库
    from app.routers.form_doc import generate_form_from_record

    project = await db.get(Project, project_id)
    doc, spec, content = await generate_form_from_record(
        db, user, project, record, req.form_code
    )
    await db.commit()
    from app.services.project_archive import sync_document_to_project_archive
    await sync_document_to_project_archive(db, doc, user)
    await db.commit()

    # 推送通知
    from app.services.notification import push_to_glasses

    section = record.section or ""
    location = record.location or ""
    body = f"{spec.name} 已入库"
    if section:
        body += f"（{section}" + (f"/{location}" if location else "") + "）"
    for recipient in recipients:
        await push_to_glasses(
            recipient.id,
            f"{user.real_name} 发来{record.record_type}记录",
            body,
            {
                "type": "work_record_share",
                "record_id": record.id,
                "record_type": record.record_type,
                "doc_id": doc.id,
                "doc_title": doc.title,
                "docx_url": doc.docx_path,
                "sender": user.real_name,
                "group": req.group if req.target == "role" else None,
                "share_mode": req.target,
            },
        )

    return {
        "detail": f"已送达 {len(recipients)} 人",
        "recipients": len(recipients),
        "doc_id": doc.id,
        "docx_url": doc.docx_path,
    }
