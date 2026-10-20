"""云端巡检路由 - AI 巡检会话管理与实时采集识别

作者: cloud-backend
创建日期: 2026-07-18
"""
import hashlib
import time
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.inspection_session import InspectionSession, InspectionStatus
from app.models.miniprogram import MediaRecord, MediaType
from app.models.user import User
from app.routers.auth import get_current_user, require_permission, require_project_id
from app.services import inspection_service
from app.services.media_validation import assert_valid_image

router = APIRouter()


class StartSessionRequest(BaseModel):
    project_id: Optional[int] = None
    frequency_seconds: int = 5


class CaptureMediaRequest(BaseModel):
    image_id: int


def _save_image(image_bytes: bytes, prefix: str = "inspection", filename: str = "capture.jpg") -> str:
    """保存图片到上传目录，返回相对路径"""
    ext = Path(filename).suffix or ".jpg"
    img_name = f"{prefix}_{int(time.time())}_{hashlib.md5(image_bytes).hexdigest()[:8]}{ext}"
    img_path = settings.upload_dir / img_name
    img_path.write_bytes(image_bytes)
    return f"/uploads/{img_name}"


def _assert_image(data: bytes) -> None:
    assert_valid_image(data)


@router.post("/start")
async def start_inspection(
    req: StartSessionRequest,
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """启动巡检会话（参数：project_id, frequency_seconds 默认 5）"""
    project_id = require_project_id(user)
    if req.project_id is not None and req.project_id != project_id:
        raise HTTPException(status_code=403, detail="只能在当前项目内启动巡检")
    try:
        session = await inspection_service.start_session(
            db, user, project_id, req.frequency_seconds
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # 实时推送：巡检启动
    try:
        from app.services.notification import push_realtime
        await push_realtime(
            user_id=user.id,
            event_type="inspection_started",
            data={"session_id": session.id, "project_id": project_id},
        )
    except Exception:
        pass

    return {
        "session_id": session.id,
        "status": session.status,
        "frequency_seconds": session.frequency_seconds,
        "started_at": session.started_at.isoformat() if session.started_at else None,
    }


@router.post("/{session_id}/stop")
async def stop_inspection(
    session_id: int,
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """停止巡检"""
    try:
        session = await inspection_service.stop_session(db, session_id, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # 实时推送：巡检停止
    try:
        from app.services.notification import push_realtime
        await push_realtime(
            user_id=user.id,
            event_type="inspection_stopped",
            data={"session_id": session.id, "total_captures": session.total_captures},
        )
    except Exception:
        pass

    return {
        "session_id": session.id,
        "status": session.status,
        "stopped_at": session.stopped_at.isoformat() if session.stopped_at else None,
        "total_captures": session.total_captures,
        "total_hazards": session.total_hazards,
    }


@router.get("/active")
async def get_active_session(
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户的活动会话"""
    session = await inspection_service.get_active_session(db, user.id)
    if not session:
        return {"session": None}
    return {
        "session": {
            "id": session.id,
            "project_id": session.project_id,
            "status": session.status,
            "frequency_seconds": session.frequency_seconds,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "total_captures": session.total_captures,
            "total_hazards": session.total_hazards,
            "last_capture_at": session.last_capture_at.isoformat() if session.last_capture_at else None,
        }
    }


@router.get("/stats")
async def get_project_stats(
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """PC 管理端巡检看板汇总统计。"""
    project_id = require_project_id(user)
    today_start = datetime.combine(date.today(), datetime_time.min)
    tomorrow_start = today_start + timedelta(days=1)

    async def _count(model, *conditions) -> int:
        result = await db.execute(select(func.count(model.id)).where(*conditions))
        return int(result.scalar() or 0)

    total_inspections = await _count(
        InspectionSession, InspectionSession.project_id == project_id
    )
    today_inspections = await _count(
        InspectionSession,
        InspectionSession.project_id == project_id,
        InspectionSession.started_at >= today_start,
        InspectionSession.started_at < tomorrow_start,
    )

    from app.models.hazard import Hazard

    total_hazards = await _count(Hazard, Hazard.project_id == project_id)
    today_hazards = await _count(
        Hazard,
        Hazard.project_id == project_id,
        Hazard.created_at >= today_start,
        Hazard.created_at < tomorrow_start,
    )
    active_sessions = await _count(
        InspectionSession,
        InspectionSession.project_id == project_id,
        InspectionSession.status == InspectionStatus.ACTIVE.value,
    )
    return {
        "today_inspection_count": today_inspections,
        "today_hazard_count": today_hazards,
        "total_inspection_count": total_inspections,
        "total_hazard_count": total_hazards,
        "active_session_count": active_sessions,
    }


@router.get("/trend")
async def get_project_trend(
    days: int = 7,
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """按天返回巡检次数与隐患数趋势。"""
    project_id = require_project_id(user)
    days = min(max(days, 1), 90)
    start_day = date.today() - timedelta(days=days - 1)
    start_at = datetime.combine(start_day, datetime_time.min)

    inspection_result = await db.execute(
        select(
            func.date(InspectionSession.started_at),
            func.count(InspectionSession.id),
        )
        .where(
            InspectionSession.project_id == project_id,
            InspectionSession.started_at >= start_at,
        )
        .group_by(func.date(InspectionSession.started_at))
    )
    inspection_counts = {
        str(day): int(count or 0) for day, count in inspection_result.all()
    }

    from app.models.hazard import Hazard

    hazard_result = await db.execute(
        select(func.date(Hazard.created_at), func.count(Hazard.id))
        .where(Hazard.project_id == project_id, Hazard.created_at >= start_at)
        .group_by(func.date(Hazard.created_at))
    )
    hazard_counts = {str(day): int(count or 0) for day, count in hazard_result.all()}

    return [
        {
            "date": (start_day + timedelta(days=offset)).isoformat(),
            "inspection_count": inspection_counts.get(
                (start_day + timedelta(days=offset)).isoformat(), 0
            ),
            "hazard_count": hazard_counts.get(
                (start_day + timedelta(days=offset)).isoformat(), 0
            ),
        }
        for offset in range(days)
    ]


@router.get("/category-distribution")
async def get_category_distribution(
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """返回当前项目隐患类别分布。"""
    project_id = require_project_id(user)
    from app.models.hazard import Hazard

    result = await db.execute(
        select(Hazard.risk_category, func.count(Hazard.id))
        .where(Hazard.project_id == project_id)
        .group_by(Hazard.risk_category)
        .order_by(func.count(Hazard.id).desc())
    )
    return [
        {"category": category or "未分类", "name": category or "未分类", "count": int(count)}
        for category, count in result.all()
    ]


@router.get("/recent")
async def get_recent_inspections(
    limit: int = 20,
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """返回巡检看板最近发现的风险记录。"""
    project_id = require_project_id(user)
    from app.models.hazard import Hazard, HazardStatus

    result = await db.execute(
        select(Hazard)
        .where(Hazard.project_id == project_id)
        .order_by(Hazard.created_at.desc())
        .limit(min(max(limit, 1), 100))
    )
    records = result.scalars().all()

    def _handle_status(status: str) -> str:
        if status == HazardStatus.CLOSED.value:
            return "已处理"
        if status in {
            HazardStatus.NOTIFIED.value,
            HazardStatus.RECTIFYING.value,
            HazardStatus.RECHECK_PENDING.value,
        }:
            return "处理中"
        return "未处理"

    return [
        {
            "id": record.id,
            "inspected_at": record.created_at.isoformat() if record.created_at else None,
            "location": record.location or record.section or "-",
            "risk_status": record.risk_status,
            "risk_category": record.risk_category,
            "handle_status": _handle_status(record.status),
            "hazard_status": record.status,
            "image_url": record.image_path,
        }
        for record in records
    ]


@router.get("/{session_id}/stats")
async def get_session_stats(
    session_id: int,
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """获取巡检统计"""
    result = await db.execute(
        select(InspectionSession).where(InspectionSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="巡检会话不存在")
    if session.user_id != user.id and not user.has_permission("task_assign"):
        raise HTTPException(status_code=403, detail="无权查看他人巡检统计")
    return await inspection_service.get_session_stats(db, session)


@router.get("/history")
async def list_history(
    limit: int = 50,
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """历史会话列表"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(InspectionSession).where(
            InspectionSession.project_id == project_id,
        ).order_by(InspectionSession.created_at.desc()).limit(min(max(limit, 1), 200))
    )
    sessions = result.scalars().all()
    return {
        "total": len(sessions),
        "items": [
            {
                "id": s.id,
                "user_id": s.user_id,
                "status": s.status,
                "frequency_seconds": s.frequency_seconds,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "stopped_at": s.stopped_at.isoformat() if s.stopped_at else None,
                "total_captures": s.total_captures,
                "total_hazards": s.total_hazards,
            }
            for s in sessions
        ],
    }


@router.post("/{session_id}/capture-media")
async def capture_uploaded_media(
    session_id: int,
    req: CaptureMediaRequest,
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """将统一媒体中的已上传图片计入巡检会话。

    眼镜端先通过可离线补传的媒体接口上传，再用媒体 ID 关联巡检；识别结果
    优先复用媒体上传阶段的结果，避免对同一图片重复调用 VLM。
    """
    session = await db.get(InspectionSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="巡检会话不存在")
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作他人巡检会话")
    if session.status != InspectionStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="巡检会话已停止")

    result = await db.execute(
        select(MediaRecord).where(
            MediaRecord.id == req.image_id,
            MediaRecord.project_id == session.project_id,
            MediaRecord.media_type == MediaType.IMAGE.value,
        )
    )
    media = result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="巡检图片不存在")

    if media.business_type == "inspection_session" and media.business_id == session.id:
        return {
            "id": session.id,
            "session_id": session.id,
            "status": session.status,
            "total_captures": session.total_captures,
            "total_hazards": session.total_hazards,
            "started_at": session.started_at.isoformat() if session.started_at else None,
        }

    from app.routers.media import _media_detection

    detection, hazard_id = await _media_detection(db, media)
    now = datetime.now()
    session.total_captures += 1
    session.last_capture_at = now
    if hazard_id or detection.get("has_hazard") or detection.get("risk_status") == "存在隐患":
        session.total_hazards += 1
        session.last_hazard_at = now
    media.business_type = "inspection_session"
    media.business_id = session.id
    await db.commit()

    return {
        "id": session.id,
        "session_id": session.id,
        "project_id": session.project_id,
        "status": session.status,
        "frequency_seconds": session.frequency_seconds,
        "total_captures": session.total_captures,
        "total_hazards": session.total_hazards,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "detection": detection,
        "hazard_id": hazard_id,
    }


@router.post("/{session_id}/capture")
async def capture(
    session_id: int,
    image: UploadFile = File(...),
    section: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    weather: Optional[str] = Form(None),
    user: User = Depends(require_permission("inspection")),
    db: AsyncSession = Depends(get_db),
):
    """上传巡检采集的图片，自动调用 VLM 识别，结果入库并返回指令（speak_urgent + vibrate）"""
    # 校验会话
    result = await db.execute(
        select(InspectionSession).where(InspectionSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="巡检会话不存在")
    if session.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作他人巡检会话")
    if session.status != InspectionStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="巡检会话已停止")

    # 校验图片
    ext = Path(image.filename or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="仅支持 jpg、png、webp 图片")
    image_bytes = await image.read(settings.max_image_bytes + 1)
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="图片为空")
    if len(image_bytes) > settings.max_image_bytes:
        raise HTTPException(status_code=413, detail="图片超过大小限制")
    _assert_image(image_bytes)

    try:
        outcome = await inspection_service.capture_and_detect(
            db,
            session,
            user,
            image_bytes,
            image.filename or "capture.jpg",
            section=section,
            location=location,
            weather=weather,
            save_image_fn=_save_image,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 实时推送：巡检采集结果（含命中隐患时通知项目相关人）
    try:
        from app.services.notification import push_realtime, broadcast_to_project, push_to_wechat
        await push_realtime(
            user_id=user.id,
            event_type="inspection_capture",
            data={
                "session_id": session.id,
                "hazard_id": outcome.get("hazard_id"),
                "has_hazard": bool(outcome.get("hazard_id")),
            },
        )
        if outcome.get("hazard_id"):
            await broadcast_to_project(
                project_id=session.project_id,
                event_type="hazard_detected",
                data={
                    "hazard_id": outcome["hazard_id"],
                    "session_id": session.id,
                    "top1_risk": outcome["result"].get("top1_risk"),
                    "section": section,
                    "location": location,
                },
            )
            # 微信服务通知：推送给项目内总监/总代
            # Step 2（设计说明 §7.8.4）：判据改为「本项目成员表的岗位集合」，
            # 兼容一人多岗（主岗+附加岗）与跨项目兼职。
            from app.models.user import User, UserRole
            from app.services.permissions import member_user_ids_with_roles

            approver_ids = await member_user_ids_with_roles(
                db,
                session.project_id,
                [UserRole.CHIEF_SUPERVISOR.value, UserRole.DEPUTY_CHIEF.value],
            )
            approvers_result = await db.execute(
                select(User).where(
                    User.id.in_(approver_ids or [-1]),
                    User.is_active.is_(True),
                )
            )
            risk_text = (outcome["result"].get("top1_risk") or "安全隐患")[:20]
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
                    page=f"pages/hazard/detail?id={outcome['hazard_id']}",
                )
    except Exception:
        pass

    return {
        "session_id": session.id,
        "total_captures": session.total_captures,
        "total_hazards": session.total_hazards,
        "result": outcome["result"],
        "hazard_id": outcome["hazard_id"],
        "image_url": outcome["image_url"],
        "instruction": outcome["instruction"],
    }
