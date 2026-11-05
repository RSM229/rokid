"""隐患识别路由 — 代理调用 AI Center hazard_vlm_detection，含自动取证与整改闭环"""
from datetime import datetime
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pydantic import BaseModel, Field
from typing import Optional
import hashlib
import json
import time
import asyncio
from pathlib import Path

from app.config import settings
from app.db import get_db
from app.models.user import Project, User, UserRole
from app.models.hazard import Hazard, HazardStatus
from app.models.image import ImageRecord
from app.models.rectification import RectificationRecord
from app.models.document import Document, DocType, DocStatus
from app.models.security_audit import SecurityAudit
from app.models.miniprogram import MediaRecord, MediaType
from app.routers.auth import (
    get_current_user,
    require_permission,
    require_project_id,
    project_scope_id,
)
from app.services.ai_client import ai_client
from app.services import agent_hazard_candidates, agent_runtime
from app.services.media_validation import assert_valid_image
from app.services.watermark_service import apply_watermark_to_image
from app.services.form_registry import FORM_BY_CODE
from app.services.form_renderer import render_form
from app.services.form_contracts import contract_for

router = APIRouter()


class HazardResult(BaseModel):
    has_hazard: bool = False
    confidence: float = 0.0
    hazard_type: str = ""
    description: str = ""
    suggestion: str = ""
    # 兼容旧字段
    scene_related: str = ""
    risk_status: str = ""
    risk_category: str = ""
    top1_risk: str = ""
    evidence: list[str] = []
    uncertainty: str = ""
    recheck_suggestion: list[str] = []
    # 规范条款 + 整改单（识别结果深加工）
    matched_regulations: list[dict] = Field(default_factory=list)
    rectify_plan: str = ""


class RecheckRequest(BaseModel):
    passed: bool
    remark: Optional[str] = None


class RectifyMediaRequest(BaseModel):
    image_id: int


class HazardNotifyDocRequest(BaseModel):
    to_unit: Optional[str] = None      # 致（施工单位）
    requirement: Optional[str] = None  # 整改要求
    deadline: Optional[str] = None     # 整改期限 YYYY-MM-DD


class HazardReplyDocRequest(BaseModel):
    rectify_desc: Optional[str] = None  # 整改完成情况
    rechecker: Optional[str] = None     # 复查人


class HazardCandidateCommitRequest(BaseModel):
    candidates: list[dict]
    request_id: Optional[str] = None
    execution_id: Optional[str] = None


def _chain_stage(hazard: Hazard) -> str:
    """三表联动阶段：未发单 → 已发通知单 → 已发整改通知 → 已回复 → 已闭合"""
    if hazard.status == HazardStatus.CLOSED.value:
        return "已闭合"
    if hazard.reply_doc_id:
        return "已回复"
    if hazard.rectify_doc_id or hazard.rectification_doc_id:
        return "已发整改通知"
    if hazard.notify_doc_id:
        return "已发通知单"
    return "未发单"


def _save_image(image_bytes: bytes, prefix: str = "hazard", filename: str = "capture.jpg") -> str:
    """保存图片到上传目录，返回相对路径"""
    ext = Path(filename).suffix or ".jpg"
    img_name = f"{prefix}_{int(time.time())}_{hashlib.md5(image_bytes).hexdigest()[:8]}{ext}"
    img_path = settings.upload_dir / img_name
    img_path.write_bytes(image_bytes)
    return f"/uploads/{img_name}"


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _read_valid_image(image: UploadFile) -> bytes:
    ext = Path(image.filename or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="仅支持 jpg、png、webp 图片")
    image_bytes = await image.read(settings.max_image_bytes + 1)
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="图片为空")
    if len(image_bytes) > settings.max_image_bytes:
        raise HTTPException(status_code=413, detail="图片超过大小限制")
    assert_valid_image(image_bytes)
    return image_bytes


@router.post("/detect", response_model=HazardResult)
async def detect_hazard(
    image: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    """上传图片进行隐患识别（仅识别，不保存）。

    识别固定走华工 VLM；model 可选，传入时命中隐患会额外生成整改单（rectify_plan）。
    """
    image_bytes = await _read_valid_image(image)
    try:
        result = await ai_client.detect_hazard(
            image_bytes, image.filename or "capture.jpg", prompt=prompt, model=model,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI识别服务异常: {str(e)}")
    return HazardResult(**result)


async def _persist_detected_hazard(
    db: AsyncSession,
    *,
    user: User,
    project_id: int,
    image_bytes: bytes,
    filename: str,
    detection: dict,
    section: Optional[str] = None,
    location: Optional[str] = None,
    remark: Optional[str] = None,
    weather: Optional[str] = None,
    source: str = "web",
) -> dict:
    """识别结果的落库实现：水印取证 + 影像记录 +（命中时）隐患记录 + 实时推送。

    - `/hazard/detect-and-save`：现场拍照「识别+落库」一步到位；
    - `/hazard/commit-detection`：工作台对话已先调 `/hazard/detect` 拿到结论，此处只落库、
      不再重复调用视觉模型（省一次模型调用，且保证落库内容与聊天里展示的结论一致）。
    两条入口共用本函数，产出结构完全一致。
    """
    project = await db.get(Project, project_id)
    watermarked = await apply_watermark_to_image(
        image_bytes,
        project_name=project.name if project else "",
        supervisor=user.real_name,
        section=section or "",
        location=location or "",
        weather=weather or "",
        hazard_flag=detection.get("top1_risk") or detection.get("risk_status") or "",
    )
    img_url = _save_image(watermarked, "hazard", filename or "capture.jpg")
    img_disk_path = settings.upload_dir / Path(img_url).name  # 落盘真实路径，commit 失败时清理

    has_hazard = bool(detection.get("has_hazard")) or (
        detection.get("risk_status") == "存在隐患"
    )
    if not has_hazard:
        db.add(
            ImageRecord(
                project_id=project_id,
                uploader_id=user.id,
                source=source,
                original_path=img_url,
                watermarked_path=img_url,
                taken_at=datetime.now(),
                location=location,
                section=section,
                weather=weather,
                hazard_id=None,
                file_hash=_file_hash(watermarked),
                remark="AI识别：未发现隐患",
            )
        )
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            img_disk_path.unlink(missing_ok=True)
            raise
        return {
            "hazard_id": None,
            "result": detection,
            "image_url": img_url,
            "saved_as_hazard": False,
        }

    # 创建隐患记录
    matched_regulations = detection.get("matched_regulations", [])
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
        image_path=img_url,
        section=section,
        location=location,
        remark=remark,
        matched_regulations_json=json.dumps(matched_regulations, ensure_ascii=False) if matched_regulations else None,
    )
    db.add(hazard)
    await db.flush()

    # 创建影像记录（自动取证）
    image_record = ImageRecord(
        project_id=project_id,
        uploader_id=user.id,
        source=source,
        original_path=img_url,
        watermarked_path=img_url,
        taken_at=datetime.now(),
        location=location,
        section=section,
        weather=weather,
        hazard_id=hazard.id,
        file_hash=_file_hash(watermarked),
        remark=f"AI识别: {detection.get('top1_risk', '')}",
    )
    db.add(image_record)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        img_disk_path.unlink(missing_ok=True)
        raise
    await db.refresh(hazard)

    # 实时推送：隐患识别命中
    try:
        from app.services.notification import push_realtime, broadcast_to_project, push_to_wechat
        await push_realtime(
            user_id=user.id,
            event_type="hazard_detected",
            data={
                "hazard_id": hazard.id,
                "top1_risk": detection.get("top1_risk"),
                "risk_category": detection.get("risk_category"),
                "section": section,
                "location": location,
            },
        )
        await broadcast_to_project(
            project_id=project_id,
            event_type="hazard_detected",
            data={
                "hazard_id": hazard.id,
                "top1_risk": detection.get("top1_risk"),
                "reporter_id": user.id,
                "section": section,
                "location": location,
            },
        )
        # 微信服务通知：推送给项目内总监/总代
        # Step 2（设计说明 §7.8.4）：判据改为「本项目成员表的岗位集合」，
        # 兼容一人多岗（主岗+附加岗）与跨项目兼职。
        from app.services.permissions import member_user_ids_with_roles

        approver_ids = await member_user_ids_with_roles(
            db,
            project_id,
            [UserRole.CHIEF_SUPERVISOR.value, UserRole.DEPUTY_CHIEF.value],
        )
        approvers_result = await db.execute(
            select(User).where(
                User.id.in_(approver_ids or [-1]),
                User.is_active.is_(True),
            )
        )
        risk_text = (detection.get("top1_risk") or "安全隐患")[:20]
        section_text = (section or "")[:20]
        for approver in approvers_result.scalars().all():
            await push_to_wechat(
                db,
                user_id=approver.id,
                template_key="hazard_detected",
                data={
                    "thing1": {"value": risk_text},
                    "thing2": {"value": section_text},
                    "thing3": {"value": user.real_name},
                },
                page=f"pages/hazard/detail?id={hazard.id}",
            )
    except Exception:
        pass

    return {
        "hazard_id": hazard.id,
        "result": detection,
        "image_url": img_url,
        "saved_as_hazard": True,
    }


@router.post("/detect-and-save")
async def detect_and_save(
    image: UploadFile = File(...),
    section: Optional[str] = None,
    location: Optional[str] = None,
    remark: Optional[str] = None,
    weather: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """识别隐患并保存记录，同时保存影像作为取证"""
    image_bytes = await _read_valid_image(image)

    # 调用 AI 识别
    try:
        result = await ai_client.detect_hazard(image_bytes, image.filename or "capture.jpg")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI识别服务异常: {str(e)}")

    return await _persist_detected_hazard(
        db,
        user=user,
        project_id=require_project_id(user),
        image_bytes=image_bytes,
        filename=image.filename or "capture.jpg",
        detection=result,
        section=section,
        location=location,
        remark=remark,
        weather=weather,
        source="web",
    )


@router.post("/commit-detection")
async def commit_detection(
    image: UploadFile = File(...),
    detection: str = Form(...),
    section: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    weather: Optional[str] = Form(None),
    source: str = Form("miniprogram"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把 `/hazard/detect` 已拿到的识别结果落库成记录（不再重复调用视觉模型）。

    工作台对话「帮我识别隐患并且创建隐患记录」走这条：先 detect 把结论展示给用户，
    用户确认/明确要求建档时再 commit，避免出现「AI 说已生成、实际库里没有」的假承诺。
    """
    try:
        payload = json.loads(detection)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="detection 不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="detection 必须是 JSON 对象")

    image_bytes = await _read_valid_image(image)
    return await _persist_detected_hazard(
        db,
        user=user,
        project_id=require_project_id(user),
        image_bytes=image_bytes,
        filename=image.filename or "capture.jpg",
        detection=payload,
        section=section,
        location=location,
        remark=remark,
        weather=weather,
        source=source,
    )


@router.post("/commit-candidates")
async def commit_hazard_candidates(
    req: HazardCandidateCommitRequest,
    user: User = Depends(require_permission("hazard_ai")),
    db: AsyncSession = Depends(get_db),
):
    """Atomically create selected hazards with a durable idempotency record."""
    try:
        candidates = agent_hazard_candidates.normalize_candidates(req.candidates)
    except agent_hazard_candidates.HazardCandidateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    project_id = require_project_id(user)
    digest = hashlib.sha256(json.dumps(
        candidates, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    scope_id = f"hazard-batch:{digest}"
    execution_id = agent_runtime.normalize_execution_id(
        req.execution_id or req.request_id or scope_id,
    )
    request_id = agent_runtime.normalize_request_id(
        req.request_id or req.execution_id or f"hazard-request:{digest}",
    )
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user.id,
        project_id=project_id,
        conversation_id=None,
        endpoint="/api/hazard/commit-candidates",
        stage="hazard_batch",
        message=f"确认 {len(candidates)} 个隐患候选",
    )
    execution_prepared = False
    try:
        await agent_runtime.prepare_execution(
            db,
            execution_id=execution_id,
            request_id=request_id,
            flow_type="hazard_candidate_batch",
            scope_id=scope_id,
            user_id=user.id,
            project_id=project_id,
        )
        execution_prepared = True
        flow = await agent_runtime.lock_execution(db, execution_id)
        if flow is None:
            raise HTTPException(status_code=409, detail="执行实例已丢失，请重新发起")
        if flow.status == "COMMITTED":
            result = agent_runtime.execution_result(flow)
            await db.commit()
            await agent_runtime.finish_request(
                request_id=request_id,
                user_id=user.id,
                endpoint="/api/hazard/commit-candidates",
                stage="hazard_batch",
                result=result,
                created_record_refs=result.get("hazards") or [],
            )
            return result

        agent_runtime.mark_execution_running(flow)
        media_ids = [item["media_id"] for item in candidates]
        media_rows = (await db.execute(
            select(MediaRecord)
            .where(
                MediaRecord.id.in_(media_ids),
                MediaRecord.project_id == project_id,
                MediaRecord.uploader_id == user.id,
                MediaRecord.media_type == MediaType.IMAGE.value,
            )
            .with_for_update()
        )).scalars().all()
        media_by_id = {item.id: item for item in media_rows}
        if set(media_by_id) != set(media_ids):
            raise HTTPException(status_code=403, detail="部分隐患候选图片不存在或不属于当前用户/项目")
        for media in media_rows:
            if media.business_id is not None or media.business_type is not None:
                raise HTTPException(status_code=409, detail=f"图片 {media.id} 已关联其他业务记录，不能重复建档")

        now = datetime.now()
        created: list[dict] = []
        for candidate in candidates:
            media = media_by_id[candidate["media_id"]]
            detection = candidate["detection"]
            metadata = media.metadata_json if isinstance(media.metadata_json, dict) else {}
            taken_at = now
            taken_at_text = candidate.get("taken_at") or str(metadata.get("capture_time") or "")
            if taken_at_text:
                try:
                    taken_at = datetime.fromisoformat(taken_at_text.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    taken_at = now
            image_path = media.processed_path or media.original_path
            section = candidate.get("section") or media.section
            location = candidate.get("location") or media.location
            weather = candidate.get("weather") or media.weather
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
                image_path=image_path,
                section=section,
                location=location,
                remark=candidate.get("remark") or None,
            )
            db.add(hazard)
            await db.flush()

            image_record = (await db.execute(
                select(ImageRecord)
                .where(
                    ImageRecord.project_id == project_id,
                    ImageRecord.uploader_id == user.id,
                    ImageRecord.original_path == media.original_path,
                )
                .order_by(ImageRecord.id.desc())
            )).scalars().first()
            if image_record is None:
                image_record = ImageRecord(
                    project_id=project_id,
                    uploader_id=user.id,
                    source=media.source,
                    original_path=media.original_path,
                    watermarked_path=image_path,
                    taken_at=taken_at,
                    location=location,
                    section=section,
                    weather=weather,
                    hazard_id=hazard.id,
                    file_hash=media.file_hash,
                    remark=f"用户确认的隐患候选：{detection.get('top1_risk', '')}",
                )
                db.add(image_record)
            else:
                if image_record.hazard_id is not None:
                    raise HTTPException(status_code=409, detail=f"图片 {media.id} 已生成过隐患记录，不能重复建档")
                image_record.hazard_id = hazard.id
                image_record.watermarked_path = image_path
                image_record.taken_at = taken_at
                image_record.location = location
                image_record.section = section
                image_record.weather = weather
                image_record.remark = f"用户确认的隐患候选：{detection.get('top1_risk', '')}"

            media.business_type = "hazard"
            media.business_id = hazard.id
            media.section = section
            media.location = location
            media.weather = weather
            media.metadata_json = {
                **metadata,
                "hazard_candidate_confirmed": True,
                "detection": detection,
                "hazard_id": hazard.id,
            }
            created.append({
                "media_id": media.id,
                "hazard_id": hazard.id,
                "top1_risk": hazard.top1_risk,
                "section": section,
                "location": location,
            })

        result = {"created_count": len(created), "hazards": created}
        agent_runtime.mark_execution_committed(flow, result)
        # Hazards, media links and COMMITTED are one atomic transaction.
        await db.commit()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user.id,
            endpoint="/api/hazard/commit-candidates",
            stage="hazard_batch",
            result=result,
            created_record_refs=created,
        )

        try:
            from app.services.notification import push_realtime, broadcast_to_project
            for item in created:
                await push_realtime(
                    user_id=user.id,
                    event_type="hazard_detected",
                    data=item,
                )
                await broadcast_to_project(
                    project_id=project_id,
                    event_type="hazard_detected",
                    data={**item, "reporter_id": user.id},
                )
        except Exception:
            pass

        return result
    except Exception as exc:
        await db.rollback()
        if execution_prepared:
            await agent_runtime.mark_execution_failed(
                execution_id=execution_id, error=exc,
                user_id=user.id, project_id=project_id,
            )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user.id,
            endpoint="/api/hazard/commit-candidates",
            stage="hazard_batch",
            status="failed",
            error=exc,
        )
        raise


@router.get("/list")
async def list_hazards(
    status_filter: Optional[str] = None,
    risk_status: Optional[str] = None,
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询隐患列表"""
    scope = project_scope_id(user, all_projects)
    query = select(Hazard)
    if scope is not None:
        query = query.where(Hazard.project_id == scope)
    if status_filter:
        status_aliases = {
            "reported": HazardStatus.REPORTED.value,
            "notified": HazardStatus.NOTIFIED.value,
            "rectifying": HazardStatus.RECTIFYING.value,
            "recheck": HazardStatus.RECHECK_PENDING.value,
            "rechecking": HazardStatus.RECHECK_PENDING.value,
            "closed": HazardStatus.CLOSED.value,
            "rejected": HazardStatus.REJECTED.value,
        }
        if status_filter == "open":
            query = query.where(
                ~Hazard.status.in_(
                    [HazardStatus.CLOSED.value, HazardStatus.REJECTED.value]
                )
            )
        else:
            query = query.where(
                Hazard.status == status_aliases.get(status_filter, status_filter)
            )
    if risk_status:
        query = query.where(Hazard.risk_status == risk_status)
    query = query.order_by(Hazard.created_at.desc())
    result = await db.execute(query)
    hazards = result.scalars().all()
    # 「全部项目」视角下补项目名，便于列表直接展示归属；单项目视角不额外查库
    project_names: dict[int, str] = {}
    if scope is None and hazards:
        proj_rows = await db.execute(
            select(Project.id, Project.name).where(
                Project.id.in_({h.project_id for h in hazards})
            )
        )
        project_names = {pid: name for pid, name in proj_rows.all()}
    return [
        {
            "id": h.id,
            "project_id": h.project_id,
            "project_name": project_names.get(h.project_id),
            "status": h.status,
            "risk_status": h.risk_status,
            "risk_category": h.risk_category,
            "top1_risk": h.top1_risk,
            "section": h.section,
            "location": h.location,
            "image_path": h.image_path,
            "rectification_image_path": h.rectification_image_path,
            "evidence": h.evidence,
            "recheck_suggestion": h.recheck_suggestion,
            "created_at": h.created_at.isoformat(),
        }
        for h in hazards
    ]


@router.get("/{hazard_id}")
async def get_hazard(
    hazard_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取隐患详情（含整改记录、通知单与关联影像）"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Hazard).where(Hazard.id == hazard_id)
    if scope is not None:
        stmt = stmt.where(Hazard.project_id == scope)
    result = await db.execute(stmt)
    hazard = result.scalar_one_or_none()
    if not hazard:
        raise HTTPException(status_code=404, detail="隐患记录不存在")

    # 整改闭环记录
    rect_result = await db.execute(
        select(RectificationRecord).where(RectificationRecord.hazard_id == hazard_id)
    )
    rect = rect_result.scalars().first()

    # 整改通知单
    doc_title = None
    doc_status = None
    if hazard.rectification_doc_id:
        rect_doc = await db.get(Document, hazard.rectification_doc_id)
        if rect_doc:
            doc_title = rect_doc.title
            doc_status = rect_doc.status

    # 关联媒体（整改/复查影像等，uniform media）
    media_result = await db.execute(
        select(MediaRecord)
        .where(
            MediaRecord.project_id == hazard.project_id,
            MediaRecord.business_type == "hazard",
            MediaRecord.business_id == hazard_id,
        )
        .order_by(MediaRecord.created_at.desc())
    )
    media = media_result.scalars().all()

    return {
        "id": hazard.id,
        "project_id": hazard.project_id,
        "status": hazard.status,
        "risk_status": hazard.risk_status,
        "risk_category": hazard.risk_category,
        "top1_risk": hazard.top1_risk,
        "evidence": hazard.evidence,
        "uncertainty": hazard.uncertainty,
        "recheck_suggestion": hazard.recheck_suggestion,
        "scene_related": hazard.scene_related,
        "image_path": hazard.image_path,
        "rectification_image_path": hazard.rectification_image_path,
        "section": hazard.section,
        "location": hazard.location,
        "remark": hazard.remark,
        "created_at": hazard.created_at.isoformat(),
        "updated_at": hazard.updated_at.isoformat() if hazard.updated_at else None,
        "rectification_doc_id": hazard.rectification_doc_id,
        "rectification_doc_title": doc_title,
        "rectification_doc_status": doc_status,
        "notify_doc_id": hazard.notify_doc_id,
        "rectify_doc_id": hazard.rectify_doc_id or hazard.rectification_doc_id,
        "reply_doc_id": hazard.reply_doc_id,
        "chain_stage": _chain_stage(hazard),
        "deadline": rect.deadline.isoformat() if rect and rect.deadline else None,
        "rectified_at": rect.rectified_at.isoformat() if rect and rect.rectified_at else None,
        "recheck_passed": rect.recheck_passed if rect else None,
        "recheck_remark": rect.recheck_remark if rect else None,
        "recheck_at": rect.recheck_at.isoformat() if rect and rect.recheck_at else None,
        "media": [
            {
                "id": m.id,
                "media_type": m.media_type,
                "path": m.processed_path or m.original_path,
                "created_at": m.created_at.isoformat(),
            }
            for m in media
        ],
    }


@router.delete("/{hazard_id}")
async def delete_hazard(
    hazard_id: int,
    user: User = Depends(require_permission("hazard_ai")),
    db: AsyncSession = Depends(get_db),
):
    """删除隐患记录：连带整改记录与关联影像（记录行 + 磁盘文件）一并删除。

    ⚠️ 影像口径（用户 2026-10-13 确认「连照片一起删」）：隐患关联的现场取证图与整改/复查
    影像会从影像库/资料库一并消失，磁盘文件也物理删除，**不可恢复**。
    关联的监理通知单/整改通知/回复单属已发正式文书，保持独立、不随隐患删除。
    """
    project_id = require_project_id(user)
    result = await db.execute(
        select(Hazard).where(Hazard.id == hazard_id, Hazard.project_id == project_id)
    )
    hazard = result.scalar_one_or_none()
    if not hazard:
        raise HTTPException(status_code=404, detail="隐患记录不存在")

    # 1) 收集该隐患名下所有影像路径（隐患自查证图 + 整改图 + image_records 上登记的路径）
    image_rows = (
        await db.execute(select(ImageRecord).where(ImageRecord.hazard_id == hazard_id))
    ).scalars().all()
    paths: set[str] = set()
    for row in image_rows:
        for path in (row.original_path, row.watermarked_path):
            if path:
                paths.add(path)
    for path in (hazard.image_path, hazard.rectification_image_path):
        if path:
            paths.add(path)

    # 2) /media/images 上传的正文影像：同一份文件在 media_records 与 image_records 各有一行，
    #    按路径找出 media_records 行一并回收，避免只删一半留下孤儿记录
    media_rows: list[MediaRecord] = []
    if paths:
        media_rows = (
            await db.execute(
                select(MediaRecord).where(
                    MediaRecord.original_path.in_(list(paths))
                    | MediaRecord.processed_path.in_(list(paths))
                )
            )
        ).scalars().all()
        for row in media_rows:
            for path in (row.original_path, row.processed_path, row.thumbnail_path):
                if path:
                    paths.add(path)

    # 3) 🔴 必须先清子行再删父行：image_records.hazard_id → hazards.id 是**无级联**外键
    #    （image_records_ibfk_3），残留引用时 MySQL 抛 1451 → 接口 500（生产已实测）。
    await db.execute(
        delete(RectificationRecord).where(RectificationRecord.hazard_id == hazard_id)
    )
    await db.execute(delete(ImageRecord).where(ImageRecord.hazard_id == hazard_id))
    if media_rows:
        await db.execute(
            delete(MediaRecord).where(MediaRecord.id.in_([m.id for m in media_rows]))
        )
    await db.delete(hazard)
    await db.commit()

    # 4) 落盘回收：仅当两张表都不再引用该路径时才物理删文件，避免误删被共享的影像
    files_removed = 0
    for public_path in paths:
        if not public_path.startswith("/uploads/"):
            continue
        still_image = await db.execute(
            select(ImageRecord.id)
            .where(
                (ImageRecord.original_path == public_path)
                | (ImageRecord.watermarked_path == public_path)
            )
            .limit(1)
        )
        if still_image.first() is not None:
            continue
        still_media = await db.execute(
            select(MediaRecord.id)
            .where(
                (MediaRecord.original_path == public_path)
                | (MediaRecord.processed_path == public_path)
                | (MediaRecord.thumbnail_path == public_path)
            )
            .limit(1)
        )
        if still_media.first() is not None:
            continue
        try:
            (settings.upload_dir / public_path[len("/uploads/"):]).unlink(missing_ok=True)
            files_removed += 1
        except OSError:
            pass

    return {
        "detail": "隐患记录已删除（含关联影像）",
        "hazard_id": hazard_id,
        "deleted_images": len(image_rows),
        "deleted_media": len(media_rows),
        "files_removed": files_removed,
    }


@router.post("/{hazard_id}/recheck")
async def recheck_hazard(
    hazard_id: int,
    req: RecheckRequest,
    user: User = Depends(require_permission("hazard_ai")),
    db: AsyncSession = Depends(get_db),
):
    """语音复查：确认合格/驳回整改，形成闭环"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Hazard).where(Hazard.id == hazard_id, Hazard.project_id == project_id)
    )
    hazard = result.scalar_one_or_none()
    if not hazard:
        raise HTTPException(status_code=404, detail="隐患记录不存在")

    if (
        (req.passed and hazard.status == HazardStatus.CLOSED.value)
        or (not req.passed and hazard.status == HazardStatus.REJECTED.value)
    ):
        return {
            "hazard_id": hazard_id,
            "status": hazard.status,
            "passed": req.passed,
            "idempotent_replay": True,
        }
    if hazard.status != HazardStatus.RECHECK_PENDING.value:
        raise HTTPException(status_code=409, detail="仅待复查隐患可以执行复查")

    # 复查只能针对真实整改提交，不能仅凭状态或口头参数闭环。
    rect_result = await db.execute(
        select(RectificationRecord).where(RectificationRecord.hazard_id == hazard_id)
    )
    rect = rect_result.scalars().first()
    if not rect or not rect.rectified_at or not (rect.rectification_image_path or hazard.rectification_image_path):
        raise HTTPException(status_code=409, detail="尚未提交整改完成时间和整改影像，不能复查")

    if req.passed:
        hazard.status = HazardStatus.CLOSED.value
    else:
        hazard.status = HazardStatus.REJECTED.value

    # 更新整改记录
    rect.recheck_passed = req.passed
    rect.recheck_remark = req.remark
    rect.recheck_at = datetime.now()

    await db.commit()

    # 实时推送：复查结果
    try:
        from app.services.notification import push_realtime, broadcast_to_project
        reporter_result = await db.execute(
            select(User).where(User.id == hazard.reporter_id)
        )
        reporter = reporter_result.scalar_one_or_none()
        if reporter:
            await push_realtime(
                user_id=reporter.id,
                event_type="hazard_rechecked",
                data={
                    "hazard_id": hazard_id,
                    "status": hazard.status,
                    "passed": req.passed,
                },
            )
        await broadcast_to_project(
            project_id=project_id,
            event_type="hazard_status_changed",
            data={
                "hazard_id": hazard_id,
                "status": hazard.status,
                "event": "rechecked",
                "passed": req.passed,
            },
        )
    except Exception:
        pass

    return {"hazard_id": hazard_id, "status": hazard.status, "passed": req.passed}


@router.post("/{hazard_id}/rectify-image")
async def upload_rectification_image(
    hazard_id: int,
    image: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """总包上传整改后影像"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Hazard).where(Hazard.id == hazard_id, Hazard.project_id == project_id)
    )
    hazard = result.scalar_one_or_none()
    if not hazard:
        raise HTTPException(status_code=404, detail="隐患记录不存在")

    image_bytes = await _read_valid_image(image)
    project = await db.get(Project, project_id)
    watermarked = await apply_watermark_to_image(
        image_bytes,
        project_name=project.name if project else "",
        supervisor=user.real_name,
        section=hazard.section or "",
        location=hazard.location or "",
        work_content="整改后影像",
        hazard_flag=hazard.top1_risk or "",
    )
    img_url = _save_image(watermarked, "rectify", "rectify.jpg")

    hazard.rectification_image_path = img_url
    hazard.status = HazardStatus.RECHECK_PENDING.value

    # 更新整改记录
    rect_result = await db.execute(
        select(RectificationRecord).where(RectificationRecord.hazard_id == hazard_id)
    )
    rect = rect_result.scalars().first()
    if rect:
        rect.rectification_image_path = img_url
        rect.rectified_at = datetime.now()

    # 保存影像记录
    image_record = ImageRecord(
        project_id=project_id,
        uploader_id=user.id,
        source="web",
        original_path=img_url,
        watermarked_path=img_url,
        taken_at=datetime.now(),
        hazard_id=hazard_id,
        file_hash=_file_hash(watermarked),
        remark="整改后影像",
    )
    db.add(image_record)
    await db.commit()

    # 实时推送：整改状态变更（上传整改影像）
    try:
        from app.services.notification import push_realtime, broadcast_to_project
        # 通知该项目所有有隐患处理权限的人员
        reporter_result = await db.execute(
            select(User).where(User.id == hazard.reporter_id)
        )
        reporter = reporter_result.scalar_one_or_none()
        if reporter:
            await push_realtime(
                user_id=reporter.id,
                event_type="rectification_submitted",
                data={
                    "hazard_id": hazard_id,
                    "status": hazard.status,
                    "rectification_image": img_url,
                },
            )
        await broadcast_to_project(
            project_id=project_id,
            event_type="hazard_status_changed",
            data={
                "hazard_id": hazard_id,
                "status": hazard.status,
                "event": "rectify_image_uploaded",
            },
        )
    except Exception:
        pass

    return {"hazard_id": hazard_id, "rectification_image": img_url, "status": hazard.status}


@router.post("/{hazard_id}/rectify-media")
async def attach_rectification_media(
    hazard_id: int,
    req: RectifyMediaRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """将已通过统一媒体接口上传的图片作为整改后影像。"""
    project_id = require_project_id(user)
    hazard_result = await db.execute(
        select(Hazard).where(
            Hazard.id == hazard_id,
            Hazard.project_id == project_id,
        )
    )
    hazard = hazard_result.scalar_one_or_none()
    if not hazard:
        raise HTTPException(status_code=404, detail="隐患记录不存在")
    if not (hazard.rectification_doc_id or hazard.rectify_doc_id):
        raise HTTPException(status_code=409, detail="请先生成整改通知单，再提交整改影像")

    media_result = await db.execute(
        select(MediaRecord).where(
            MediaRecord.id == req.image_id,
            MediaRecord.project_id == project_id,
            MediaRecord.media_type == MediaType.IMAGE.value,
        )
    )
    media = media_result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="整改图片不存在")

    image_url = media.processed_path or media.original_path
    hazard.rectification_image_path = image_url
    hazard.status = HazardStatus.RECHECK_PENDING.value
    media.business_type = "hazard"
    media.business_id = hazard.id

    rect_result = await db.execute(
        select(RectificationRecord).where(RectificationRecord.hazard_id == hazard_id)
    )
    rect = rect_result.scalars().first()
    if rect is None:
        rect = RectificationRecord(hazard_id=hazard_id)
        db.add(rect)
    rect.rectification_image_path = image_url
    rect.rectified_at = datetime.now()
    await db.commit()

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
        "rectification_image_path": hazard.rectification_image_path,
        "created_at": hazard.created_at.isoformat() if hazard.created_at else None,
    }


@router.post("/{hazard_id}/create-rectification-doc")
@router.post("/{hazard_id}/generate-rectification")
async def create_rectification_doc(
    hazard_id: int,
    deadline: Optional[str] = None,
    user: User = Depends(require_permission("hazard_ai")),
    db: AsyncSession = Depends(get_db),
):
    """根据隐患自动生成安全隐患整改通知单"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Hazard).where(Hazard.id == hazard_id, Hazard.project_id == project_id)
    )
    hazard = result.scalar_one_or_none()
    if not hazard:
        raise HTTPException(status_code=404, detail="隐患记录不存在")
    if hazard.rectification_doc_id:
        existing_doc = await db.get(Document, hazard.rectification_doc_id)
        if existing_doc:
            return {
                "doc_id": existing_doc.id,
                "hazard_id": hazard_id,
                "doc_status": existing_doc.status,
                "hazard_status": hazard.status,
                "idempotent_replay": True,
            }
    parsed_deadline = None
    if deadline:
        try:
            parsed_deadline = datetime.fromisoformat(deadline)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="整改期限格式无效") from exc

    # 解析已匹配的规范条款，作为整改依据
    import json as _json
    regulation_refs: list[str] = []
    matched_regulations: list[dict] = []
    if hazard.matched_regulations_json:
        try:
            matched_regulations = _json.loads(hazard.matched_regulations_json)
            for reg in matched_regulations:
                title = reg.get("title", "")
                snippet = reg.get("content_snippet", "")
                if title:
                    regulation_refs.append(f"{title}：{snippet}" if snippet else title)
        except (ValueError, TypeError):
            pass

    # 自动填充真实项目资料
    project = await db.get(Project, hazard.project_id)
    form_code = "gdaq_30308_yinhuan"
    from app.services.form_contracts import contract_for

    project_name = project.name if project else ""
    construction_unit = getattr(project, "construction_unit", None) or "" if project else ""
    supervision_org = getattr(project, "supervision_org", None) or "" if project else ""
    problem_text = "；".join(x for x in [hazard.top1_risk or "", "；".join(hazard.evidence or [])] if x)
    requirement = "；".join(hazard.recheck_suggestion or []) or "请立即组织整改，并在完成后提交整改回复申请复查"
    content = {
        "project_name": project.name if project else "",
        "construction_unit": getattr(project, "construction_unit", None) or "",
        "supervision_org": getattr(project, "supervision_org", None) or "",
        "doc_number": f"GDAQ30308-{hazard_id:06d}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "hazard_id": hazard_id,
        "form_code": form_code,
        "form_contract_version": contract_for(form_code)["version"],
        "field_sources": {
            "project_name": "project", "construction_unit": "project",
            "supervision_org": "project", "date": "system_date",
            "to_unit": "project", "subject": "hazard", "part": "hazard",
            "problems": "hazard", "requirement": "hazard_recheck_suggestion",
            "deadline": "user_or_unset", "reply_requirement": "workflow_default",
            "receipt_date": "pending_actual_receipt",
        },
        "hazard_description": hazard.top1_risk or "",
        "content": hazard.top1_risk or "",
        "subject": f"安全隐患整改（{hazard.top1_risk or hazard_id}）",
        "to_unit": construction_unit,
        "cc_unit": "",
        "part": f"{hazard.section or ''} {hazard.location or ''}".strip(),
        "problems": problem_text,
        "requirement": requirement,
        "reply_requirement": "整改完成后提交整改回复，并申请监理复查",
        "penalty_basis": "",
        "receipt_date": "",
        "evidence": hazard.evidence or [],
        "recheck_suggestion": hazard.recheck_suggestion or [],
        "section": hazard.section or "",
        "location": hazard.location or "",
        "image_path": hazard.image_path or "",
        "supervisor": user.real_name,
        "supervisor_level": user.supervisor_level or "",
        "deadline": deadline or "",
        "regulation_refs": regulation_refs,  # 整改依据：自动匹配的规范条款
        "matched_regulations": matched_regulations,
    }

    doc = Document(
        doc_type=DocType.RECTIFICATION.value,
        title=f"安全隐患整改通知单_{hazard.top1_risk or hazard_id}",
        content=content,
        section=hazard.section,
        location=hazard.location,
        project_id=hazard.project_id,
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
        attached_images=[hazard.image_path] if hazard.image_path else [],
        form_code=form_code,
    )
    db.add(doc)
    await db.flush()

    # 更新隐患状态
    hazard.status = HazardStatus.NOTIFIED.value
    hazard.rectification_doc_id = doc.id
    hazard.rectify_doc_id = doc.id

    # 创建整改记录
    rect = RectificationRecord(
        hazard_id=hazard_id,
        notice_doc_id=doc.id,
        deadline=parsed_deadline,
    )
    db.add(rect)
    db.add(SecurityAudit(
        user_id=user.id,
        username=user.username,
        action="hazard_rectification_doc_create",
        detail=json.dumps({"hazard_id": hazard_id, "doc_id": doc.id, "form_code": form_code}, ensure_ascii=False),
    ))
    await db.commit()
    await db.refresh(doc)
    from app.services.project_archive import sync_document_to_project_archive
    await sync_document_to_project_archive(db, doc, user)
    await db.commit()

    return {
        "doc_id": doc.id,
        "hazard_id": hazard_id,
        "doc_status": doc.status,
        "hazard_status": hazard.status,
    }


@router.post("/{hazard_id}/notify-doc")
async def create_notify_doc(
    hazard_id: int,
    req: Optional[HazardNotifyDocRequest] = None,
    user: User = Depends(require_permission("hazard_ai")),
    db: AsyncSession = Depends(get_db),
):
    """由隐患生成监理通知单（质量类）GD-B-215（三表联动第一步）"""
    req = req or HazardNotifyDocRequest()
    project_id = require_project_id(user)
    result = await db.execute(
        select(Hazard).where(Hazard.id == hazard_id, Hazard.project_id == project_id)
    )
    hazard = result.scalar_one_or_none()
    if not hazard:
        raise HTTPException(status_code=404, detail="隐患记录不存在")
    if hazard.notify_doc_id:
        existing_doc = await db.get(Document, hazard.notify_doc_id)
        if existing_doc:
            return {
                "doc_id": existing_doc.id,
                "hazard_id": hazard_id,
                "form_code": existing_doc.form_code,
                "doc_status": existing_doc.status,
                "hazard_status": hazard.status,
                "idempotent_replay": True,
            }
    if req.deadline:
        try:
            datetime.strptime(req.deadline, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="整改期限格式无效") from exc

    project = await db.get(Project, project_id)
    evidence_text = "；".join(str(e) for e in (hazard.evidence or []))
    problem_desc = "；".join(x for x in [hazard.top1_risk or "", evidence_text] if x)
    requirement = req.requirement or "；".join(hazard.recheck_suggestion or []) or "请立即组织整改"
    to_unit = req.to_unit or (getattr(project, "construction_unit", None) or "")

    # 自动填充文书内容（含 fill_generic 兼容别名）
    content = {
        "project_name": project.name if project else "",
        "doc_number": f"GD-B-215-{hazard_id:05d}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "hazard_id": hazard_id,
        "subject": f"安全隐患整改（{hazard.top1_risk or hazard_id}）",
        "to_unit": to_unit,
        "problem_desc": problem_desc,
        "requirement": requirement,
        "deadline": req.deadline or "",
        "construction_unit": to_unit,
        "supervision_org": getattr(project, "supervision_org", None) or "",
        "supervision_unit": getattr(project, "supervision_unit", None) or "",
        "problems": problem_desc,
        "handling": requirement,
        "content": f"存在问题：{problem_desc}\n整改要求：{requirement}",
        "section": hazard.section or "",
        "location": hazard.location or "",
        "supervisor": getattr(user, "real_name", "") or "",
    }

    spec = FORM_BY_CODE["gd_b_215_tongzhi"]
    doc = Document(
        doc_type=DocType.NOTICE_QUALITY.value,
        title=f"监理通知单（质量类）_{hazard.top1_risk or hazard_id}",
        content=content,
        form_code=spec.form_code,
        section=hazard.section,
        location=hazard.location,
        project_id=hazard.project_id,
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
        attached_images=[hazard.image_path] if hazard.image_path else [],
    )
    db.add(doc)
    await db.flush()

    try:
        docx_file = await asyncio.to_thread(render_form, spec.form_code, content)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"监理通知单渲染失败：{exc}") from exc
    doc.docx_path = f"/exports/{docx_file.name}"
    doc.pdf_path = None

    hazard.notify_doc_id = doc.id
    await db.commit()
    await db.refresh(doc)
    from app.services.project_archive import sync_document_to_project_archive
    await sync_document_to_project_archive(db, doc, user)
    await db.commit()

    return {
        "doc_id": doc.id,
        "hazard_id": hazard_id,
        "form_code": spec.form_code,
        "doc_status": doc.status,
        "hazard_status": hazard.status,
        "chain_stage": _chain_stage(hazard),
        "docx_url": doc.docx_path,
    }


@router.post("/{hazard_id}/reply-doc")
async def create_reply_doc(
    hazard_id: int,
    req: Optional[HazardReplyDocRequest] = None,
    user: User = Depends(require_permission("hazard_ai")),
    db: AsyncSession = Depends(get_db),
):
    """由隐患生成安全隐患整改通知回复 GDAQ30309（三表联动第三步）"""
    req = req or HazardReplyDocRequest()
    project_id = require_project_id(user)
    result = await db.execute(
        select(Hazard).where(Hazard.id == hazard_id, Hazard.project_id == project_id)
    )
    hazard = result.scalar_one_or_none()
    if not hazard:
        raise HTTPException(status_code=404, detail="隐患记录不存在")
    if hazard.reply_doc_id:
        existing_doc = await db.get(Document, hazard.reply_doc_id)
        if existing_doc:
            return {
                "doc_id": existing_doc.id,
                "hazard_id": hazard_id,
                "form_code": existing_doc.form_code,
                "doc_status": existing_doc.status,
                "hazard_status": hazard.status,
                "idempotent_replay": True,
            }

    # 回复单代表施工单位已提交整改，必须有通知单、整改时间和真实整改影像。
    # 没有这些证据时只能继续补充，不能生成一张看似完成的回复单。
    if not (hazard.rectification_doc_id or hazard.rectify_doc_id):
        raise HTTPException(status_code=409, detail="请先生成安全隐患整改通知单")

    project = await db.get(Project, project_id)
    rect_result = await db.execute(
        select(RectificationRecord).where(RectificationRecord.hazard_id == hazard_id)
    )
    rect = rect_result.scalars().first()
    if not rect or not rect.rectified_at or not (rect.rectification_image_path or hazard.rectification_image_path):
        raise HTTPException(status_code=409, detail="请先提交整改完成时间和整改影像，再生成整改回复单")

    # 整改完成情况：优先 body 传入，其次按整改记录推导
    rectify_desc = req.rectify_desc or ""
    if not rectify_desc:
        rectify_desc = f"施工单位已于 {rect.rectified_at:%Y年%m月%d日} 完成整改并提交整改影像"
    recheck_conclusion = ""
    if rect and rect.recheck_passed is not None:
        recheck_conclusion = "合格" if rect.recheck_passed else "不合格"
    recheck_date = rect.recheck_at.strftime("%Y-%m-%d") if rect and rect.recheck_at else ""
    # 未发生复查时不得把当前操作人冒充复查人；只有明确传入且已有复查结果才写入。
    rechecker = (req.rechecker or "") if rect and rect.recheck_at else ""

    notice_doc = await db.get(Document, hazard.rectification_doc_id or hazard.rectify_doc_id)
    notice_content = notice_doc.content if notice_doc and isinstance(notice_doc.content, dict) else {}
    content_text = (
        f"隐患描述：{hazard.top1_risk or ''}\n"
        f"整改完成情况：{rectify_desc}\n"
        f"复查结论：{recheck_conclusion or '待复查'}\n"
        f"复查人：{rechecker}"
    )
    content = {
        "project_name": project.name if project else "",
        "doc_number": f"GDAQ30309-{hazard_id:05d}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "reply_date": datetime.now().strftime("%Y-%m-%d"),
        "hazard_id": hazard_id,
        "to_unit": getattr(project, "supervision_org", None) or "",
        "notice_number": notice_content.get("doc_number") or notice_content.get("document_number") or notice_content.get("notice_number") or "",
        "notice_date": notice_content.get("date") or notice_content.get("notice_date") or "",
        "part": f"{hazard.section or ''} {hazard.location or ''}".strip(),
        "hazard_desc": hazard.top1_risk or "",
        "rectify_desc": rectify_desc,
        "rectify_image": hazard.rectification_image_path or "",
        "finish_date": rect.rectified_at.strftime("%Y-%m-%d") if rect and rect.rectified_at else "",
        "construction_unit": getattr(project, "construction_unit", None) or "",
        "contractor_manager": "",
        "supervision_opinion": "",
        "supervision_org": getattr(project, "supervision_org", None) or "",
        "supervision_engineer": "",
        "review_date": "",
        "recheck_date": recheck_date,
        "recheck_conclusion": recheck_conclusion,
        "recheck_remark": rect.recheck_remark if rect else "",
        "rechecker": rechecker,
        "form_contract_version": contract_for("gdaq_30309_huifu")["version"],
        "field_sources": {
            "reply_date": "system_date", "to_unit": "project",
            "notice_number": "rectification_notice_or_unset", "notice_date": "rectification_notice_or_unset",
            "part": "hazard", "hazard_desc": "hazard",
            "rectify_desc": "rectification_record_or_user",
            "rectify_image": "rectification_record", "recheck_date": "rectification_record",
            "recheck_conclusion": "rectification_record", "recheck_remark": "rectification_record",
            "rechecker": "actual_recheck_user_or_unset",
            "contractor_manager": "user_confirmation_required",
            "supervision_opinion": "user_confirmation_required",
            "supervision_engineer": "user_confirmation_required",
            "review_date": "user_confirmation_required",
        },
        "content": content_text,
        "problems": hazard.top1_risk or "",
    }

    missing = [name for key, name in (
        ("notice_number", "整改通知编号"), ("notice_date", "整改通知日期"),
        ("construction_unit", "施工单位（项目部）"),
    ) if not content.get(key)]
    if missing:
        raise HTTPException(status_code=409, detail="生成整改回复单前还需补充：" + "、".join(missing))

    spec = FORM_BY_CODE["gdaq_30309_huifu"]
    doc = Document(
        doc_type=DocType.RECTIFY_REPLY.value,
        title=f"安全隐患整改回复单_{hazard.top1_risk or hazard_id}",
        content=content,
        form_code=spec.form_code,
        section=hazard.section,
        location=hazard.location,
        project_id=hazard.project_id,
        creator_id=user.id,
        status=DocStatus.DRAFT.value,
        attached_images=[hazard.rectification_image_path] if hazard.rectification_image_path else [],
    )
    db.add(doc)
    await db.flush()

    try:
        docx_file = await asyncio.to_thread(render_form, spec.form_code, content)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"整改回复单渲染失败：{exc}") from exc
    doc.docx_path = f"/exports/{docx_file.name}"
    doc.pdf_path = None

    hazard.reply_doc_id = doc.id
    await db.commit()
    await db.refresh(doc)
    from app.services.project_archive import sync_document_to_project_archive
    await sync_document_to_project_archive(db, doc, user)
    await db.commit()

    return {
        "doc_id": doc.id,
        "hazard_id": hazard_id,
        "form_code": spec.form_code,
        "doc_status": doc.status,
        "hazard_status": hazard.status,
        "chain_stage": _chain_stage(hazard),
        "docx_url": doc.docx_path,
    }
