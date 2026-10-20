"""云端巡检服务 - 管理巡检会话、采集识别、统计

作者: cloud-backend
创建日期: 2026-07-18
"""
import logging
import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hazard import Hazard, HazardStatus
from app.models.image import ImageRecord
from app.models.inspection_session import InspectionSession, InspectionStatus
from app.models.miniprogram import MediaRecord, MediaType
from app.models.user import Project, User
from app.services.ai_client import ai_client
from app.services.watermark_service import apply_watermark_to_image

logger = logging.getLogger(__name__)


async def start_session(
    db: AsyncSession,
    user: User,
    project_id: int,
    frequency_seconds: int = 5,
) -> InspectionSession:
    """启动巡检会话：创建 session 记录，状态 active

    同一用户同时只允许一个活动会话；若已存在则返回已有会话。
    """
    if frequency_seconds < 1:
        frequency_seconds = 1

    existing = await db.execute(
        select(InspectionSession).where(
            InspectionSession.user_id == user.id,
            InspectionSession.status == InspectionStatus.ACTIVE.value,
        )
    )
    active = existing.scalar_one_or_none()
    if active:
        return active

    session = InspectionSession(
        project_id=project_id,
        user_id=user.id,
        status=InspectionStatus.ACTIVE.value,
        frequency_seconds=frequency_seconds,
        started_at=datetime.now(),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def stop_session(db: AsyncSession, session_id: int, user_id: int) -> InspectionSession:
    """停止巡检会话：更新 status=stopped、stopped_at"""
    result = await db.execute(
        select(InspectionSession).where(InspectionSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise ValueError("巡检会话不存在")
    if session.user_id != user_id:
        raise PermissionError("无权操作他人巡检会话")
    if session.status == InspectionStatus.STOPPED.value:
        return session
    session.status = InspectionStatus.STOPPED.value
    session.stopped_at = datetime.now()
    await db.commit()
    await db.refresh(session)
    return session


async def get_active_session(db: AsyncSession, user_id: int) -> Optional[InspectionSession]:
    """查询当前用户的活动会话"""
    result = await db.execute(
        select(InspectionSession).where(
            InspectionSession.user_id == user_id,
            InspectionSession.status == InspectionStatus.ACTIVE.value,
        )
    )
    return result.scalar_one_or_none()


async def capture_and_detect(
    db: AsyncSession,
    session: InspectionSession,
    user: User,
    image_bytes: bytes,
    filename: str,
    section: Optional[str] = None,
    location: Optional[str] = None,
    weather: Optional[str] = None,
    save_image_fn=None,
) -> dict:
    """采集识别：调用 ai_client.detect_hazard，命中隐患时创建 Hazard + ImageRecord + 联动规范库

    Args:
        save_image_fn: 保存图片的回调函数 (bytes, prefix, filename) -> img_url

    Returns:
        {
            "result": {...},         # AI 识别结果（含 matched_regulations）
            "hazard_id": int | None, # 命中隐患时返回
            "image_url": str,        # 水印图片 URL
            "instruction": dict,     # 指令：speak_urgent + vibrate
        }
    """
    # 调用 AI 识别
    try:
        result = await ai_client.detect_hazard(image_bytes, filename)
    except Exception as exc:
        raise RuntimeError(f"AI 识别失败：{exc}") from exc

    project = await db.get(Project, session.project_id)
    project_name = project.name if project else ""

    # 添加水印
    watermarked = await apply_watermark_to_image(
        image_bytes,
        project_name=project_name,
        supervisor=user.real_name,
        section=section or "",
        location=location or "",
        weather=weather or "",
        hazard_flag=result.get("top1_risk") or result.get("risk_status") or "",
    )

    img_url = save_image_fn(watermarked, "inspection", "capture.jpg") if save_image_fn else ""

    # 更新会话统计
    session.total_captures += 1
    session.last_capture_at = datetime.now()

    hazard_id = None
    has_hazard = result.get("has_hazard", False) or "存在隐患" in (result.get("risk_status") or "")

    # 自动匹配规范库条款
    try:
        from app.services.regulation_matcher import match_regulations_for_hazard
        matched_regs = match_regulations_for_hazard(
            hazard_category=result.get("risk_category") or result.get("hazard_type") or "",
            hazard_description=result.get("top1_risk") or result.get("description") or "",
        )
        result["matched_regulations"] = matched_regs
    except Exception as exc:
        logger.warning(f"规范匹配失败：{exc}")
        result["matched_regulations"] = []

    if has_hazard:
        # 创建隐患记录
        import json as _json
        hazard = Hazard(
            project_id=session.project_id,
            reporter_id=user.id,
            status=HazardStatus.REPORTED.value,
            risk_status=result.get("risk_status"),
            risk_category=result.get("risk_category"),
            top1_risk=result.get("top1_risk"),
            evidence=result.get("evidence", []),
            uncertainty=result.get("uncertainty"),
            recheck_suggestion=result.get("recheck_suggestion", []),
            scene_related=result.get("scene_related"),
            image_path=img_url,
            section=section,
            location=location,
            matched_regulations_json=_json.dumps(result.get("matched_regulations", []), ensure_ascii=False),
        )
        db.add(hazard)
        await db.flush()
        hazard_id = hazard.id

        session.total_hazards += 1
        session.last_hazard_at = datetime.now()

    # 每次巡检采集都形成影像台账，而非仅在命中隐患时保存。
    db.add(
        ImageRecord(
            project_id=session.project_id,
            uploader_id=user.id,
            source="inspection",
            original_path=img_url,
            watermarked_path=img_url,
            taken_at=datetime.now(),
            location=location,
            section=section,
            weather=weather,
            hazard_id=hazard_id,
            file_hash=hashlib.sha256(watermarked).hexdigest(),
            remark=(
                f"巡检采集: {result.get('top1_risk', '')}"
                if has_hazard
                else "巡检采集: 未发现隐患"
            ),
        )
    )

    await db.commit()

    # 生成指令：speak_urgent + vibrate
    instruction = {
        "speak_urgent": None,
        "vibrate": False,
    }
    if has_hazard:
        speak_text = f"注意，发现隐患：{result.get('top1_risk') or result.get('risk_category') or '请立即查看'}"
        instruction["speak_urgent"] = speak_text
        instruction["vibrate"] = True

    return {
        "result": result,
        "hazard_id": hazard_id,
        "image_url": img_url,
        "instruction": instruction,
    }


async def get_session_stats(db: AsyncSession, session: InspectionSession) -> dict:
    """统计：返回总采集数、隐患数、各风险类别分布"""
    # 查询该会话期间同项目同用户的隐患
    result = await db.execute(
        select(Hazard).where(
            Hazard.project_id == session.project_id,
            Hazard.reporter_id == session.user_id,
            Hazard.created_at >= session.started_at,
        )
    )
    hazards = result.scalars().all()
    if session.stopped_at:
        hazards = [h for h in hazards if h.created_at <= session.stopped_at]

    category_dist: dict[str, int] = {}
    for h in hazards:
        cat = h.risk_category or "未分类"
        category_dist[cat] = category_dist.get(cat, 0) + 1

    capture_query = select(ImageRecord).where(
        ImageRecord.project_id == session.project_id,
        ImageRecord.uploader_id == session.user_id,
        ImageRecord.source == "inspection",
        ImageRecord.taken_at >= session.started_at,
    )
    if session.stopped_at:
        capture_query = capture_query.where(
            ImageRecord.taken_at <= session.stopped_at
        )
    capture_result = await db.execute(
        capture_query.order_by(ImageRecord.taken_at.desc())
    )
    captures = capture_result.scalars().all()
    media_result = await db.execute(
        select(MediaRecord).where(
            MediaRecord.project_id == session.project_id,
            MediaRecord.media_type == MediaType.IMAGE.value,
            MediaRecord.business_type == "inspection_session",
            MediaRecord.business_id == session.id,
        ).order_by(MediaRecord.created_at.desc())
    )
    linked_media = media_result.scalars().all()
    capture_items = [
        {
            "id": item.id,
            "image_url": item.watermarked_path or item.original_path,
            "captured_at": item.taken_at.isoformat() if item.taken_at else None,
            "section": item.section,
            "location": item.location,
            "hazard_id": item.hazard_id,
            "risk_level": "high" if item.hazard_id else "normal",
            "result": item.remark,
        }
        for item in captures
    ]
    capture_items.extend(
        {
            "id": f"media-{item.id}",
            "image_url": item.processed_path or item.original_path,
            "captured_at": item.created_at.isoformat() if item.created_at else None,
            "section": item.section,
            "location": item.location,
            "hazard_id": (item.metadata_json or {}).get("hazard_id"),
            "risk_level": (
                "high"
                if (item.metadata_json or {}).get("hazard_id")
                else "normal"
            ),
            "result": (
                ((item.metadata_json or {}).get("detection") or {}).get("top1_risk")
                or "未发现隐患"
            ),
        }
        for item in linked_media
    )
    capture_items.sort(
        key=lambda item: item.get("captured_at") or "",
        reverse=True,
    )

    return {
        "session_id": session.id,
        "status": session.status,
        "total_captures": session.total_captures,
        "total_hazards": session.total_hazards,
        "category_distribution": category_dist,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "stopped_at": session.stopped_at.isoformat() if session.stopped_at else None,
        "last_capture_at": session.last_capture_at.isoformat() if session.last_capture_at else None,
        "last_hazard_at": session.last_hazard_at.isoformat() if session.last_hazard_at else None,
        "frequency_seconds": session.frequency_seconds,
        "captures": capture_items,
    }
