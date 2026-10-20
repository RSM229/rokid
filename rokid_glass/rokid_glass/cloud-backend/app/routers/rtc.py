"""Authenticated HTTP signaling: polling works across backend workers."""
import time
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.user import User
from app.models.rtc import CallSession, CallParticipant, CallAudit
from app.routers.auth import get_current_user
from app.services import rtc_service as rtc

router = APIRouter()


class CreateCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callee_id: int = Field(gt=0)
    media_type: Literal["audio", "video"] = "video"


@router.get("/status")
async def status(user: User = Depends(get_current_user)):
    rtc.require_member(user)
    return rtc.provider_status()


@router.get("/users")
async def users(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
                user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rtc.require_member(user)
    contacts = (await db.scalars(select(User).where(
        User.project_id == user.project_id, User.id != user.id,
        User.is_active.is_(True), User.role.in_(rtc.ROLES)).order_by(User.id).offset(offset).limit(limit))).all()
    return [{"id": item.id, "real_name": item.real_name, "role": item.role} for item in contacts]


@router.post("/calls", status_code=201)
async def create_call(body: CreateCall, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await rtc.initiate(db, user, body.callee_id, body.media_type)


@router.get("/calls")
async def calls(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rtc.require_member(user)
    await rtc.expire_calls(db)
    items = (await db.scalars(select(CallSession).where(
        CallSession.project_id == user.project_id,
        or_(CallSession.caller_id == user.id, CallSession.callee_id == user.id)
    ).order_by(CallSession.created_at.desc()).limit(50))).all()
    return [rtc.snapshot(item) for item in items]


@router.get("/calls/{call_id}")
async def detail(call_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    call = await rtc.get_call(db, user, call_id)
    result = rtc.snapshot(call)
    participants = (await db.scalars(select(CallParticipant).where(CallParticipant.call_id == call_id))).all()
    result["participants"] = [{"user_id": p.user_id, "joined_at": p.joined_at, "left_at": p.left_at} for p in participants]
    return result


@router.post("/calls/{call_id}/credentials")
async def credentials(call_id: str, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    call = await rtc.get_call(db, user, call_id)
    if call.status != "accepted":
        raise HTTPException(409, "Credentials require an accepted call")
    await rtc.validate_peers(db, call)
    result = rtc.credentials(call, user)
    rtc.audit(db, call_id, user.id, "credentials_issued", time.time())
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/calls/{call_id}/heartbeat")
async def heartbeat(call_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    call = await rtc.get_call(db, user, call_id)
    if call.status == "accepted":
        await rtc.validate_peers(db, call)
        await db.execute(update(CallParticipant).where(
            CallParticipant.call_id == call_id, CallParticipant.user_id == user.id,
            CallParticipant.active_user_id == user.id).values(last_seen=time.time()))
        await db.commit()
    return rtc.snapshot(call)


@router.post("/calls/{call_id}/joined")
async def joined(call_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    call = await rtc.get_call(db, user, call_id)
    if call.status != "accepted":
        raise HTTPException(409, "Call is not accepted")
    await rtc.validate_peers(db, call)
    now = time.time()
    changed = await db.execute(update(CallParticipant).where(
        CallParticipant.call_id == call_id, CallParticipant.user_id == user.id,
        CallParticipant.active_user_id == user.id, CallParticipant.joined_at.is_(None)).values(joined_at=now, last_seen=now))
    if changed.rowcount:
        rtc.audit(db, call_id, user.id, "client_media_joined", now)
    await db.commit()
    return rtc.snapshot(call)


@router.get("/calls/{call_id}/audit")
async def audit(call_id: str, after: int = Query(0, ge=0), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await rtc.get_call(db, user, call_id)
    entries = (await db.scalars(select(CallAudit).where(CallAudit.call_id == call_id, CallAudit.id > after)
                              .order_by(CallAudit.id).limit(200))).all()
    return [{"id": e.id, "actor_id": e.actor_id, "event": e.event, "at": e.at} for e in entries]


@router.post("/calls/{call_id}/{action_name}")
async def action(call_id: str, action_name: Literal["accept", "reject", "end"],
                 user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await rtc.action(db, user, call_id, action_name)
