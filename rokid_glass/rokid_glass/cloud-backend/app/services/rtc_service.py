"""TRTC TLS 2.0 adapter and transactional one-to-one signaling.

Wire format: https://github.com/tencentyun/tls-sig-api-v2-python
Room permissions: https://cloud.tencent.com/document/product/647/32240
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import struct
import time
import uuid
import zlib

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.db import async_session
from app.models.rtc import CallSession, CallParticipant, CallAudit
from app.models.user import User, UserRole

ACTIVE = ("ringing", "accepted")
ROLES = tuple(role.value for role in UserRole)


def require_member(user):
    if not user or not user.is_active or user.project_id is None or user.role not in ROLES:
        raise HTTPException(403, "RTC requires an active project member with a supervision role")


def provider_status():
    configured = (
        settings.rtc_provider == "tencent_trtc"
        and 0 < settings.trtc_sdk_app_id < 2**32
        and re.fullmatch(r"[0-9a-fA-F]{64}", settings.trtc_secret_key) is not None
        and len(set(settings.trtc_secret_key)) > 4
        and settings.trtc_private_map_key_enabled
        and 60 <= settings.rtc_credential_ttl_seconds <= 600
        and 10 <= settings.rtc_ring_timeout_seconds <= 120
        and 30 <= settings.rtc_disconnect_timeout_seconds <= 300
        and 60 <= settings.rtc_max_call_seconds <= 86400
    )
    return {"configured": configured, "provider": "tencent_trtc", "transport": "polling",
            "code": "ready" if configured else "rtc_not_configured"}


def require_provider():
    if not provider_status()["configured"]:
        raise HTTPException(503, "rtc_not_configured: configure TRTC SDKAppID, SecretKey and advanced room permissions")


def _tls_signature(user_id, now, ttl, userbuf=None):
    fields = {"TLS.identifier": user_id, "TLS.sdkappid": settings.trtc_sdk_app_id,
              "TLS.time": now, "TLS.expire": ttl}
    if userbuf is not None:
        fields["TLS.userbuf"] = base64.b64encode(userbuf).decode()
    canonical = "".join(f"{key}:{value}\n" for key, value in fields.items())
    digest = hmac.new(settings.trtc_secret_key.encode(), canonical.encode(), hashlib.sha256).digest()
    fields.update({"TLS.ver": "2.0", "TLS.sig": base64.b64encode(digest).decode()})
    return base64.b64encode(zlib.compress(json.dumps(fields).encode())).decode().translate(
        str.maketrans("+/=", "*-_"))


def credentials(call, user):
    require_provider()
    now, ttl = int(time.time()), settings.rtc_credential_ttl_seconds
    identity = f"u{user.id}"
    room = call.id.encode("ascii")
    account = identity.encode("ascii")
    # String room format v1; audio has no video publish/subscribe permission.
    privileges = 15 if call.media_type == "audio" else 63
    userbuf = (struct.pack(">BH", 1, len(account)) + account
               + struct.pack(">IIIIIH", settings.trtc_sdk_app_id, 0, now + ttl,
                             privileges, 0, len(room)) + room)
    return {"provider": "tencent_trtc", "sdkAppID": settings.trtc_sdk_app_id,
            "userID": identity, "strRoomID": call.id, "scene": "rtc",
            "userSig": _tls_signature(identity, now, ttl),
            "privateMapKey": _tls_signature(identity, now, ttl, userbuf),
            "expires_at": now + ttl}


def snapshot(call):
    return {key: getattr(call, key) for key in (
        "id", "project_id", "caller_id", "callee_id", "media_type", "status", "version",
        "created_at", "accepted_at", "ended_at", "deadline", "end_reason")}


def audit(db, call_id, actor, event, now):
    db.add(CallAudit(call_id=call_id, actor_id=actor, event=event, at=now))


async def transition(db, call, status, actor=None, reason=None):
    now = time.time()
    values = {"status": status, "version": call.version + 1}
    if status == "accepted":
        values.update(accepted_at=now, deadline=now + settings.rtc_max_call_seconds)
    elif status not in ACTIVE:
        values.update(ended_at=now, end_reason=reason)
    changed = await db.execute(update(CallSession).where(
        CallSession.id == call.id, CallSession.version == call.version,
        CallSession.status.in_(ACTIVE)).values(**values).execution_options(synchronize_session=False))
    if not changed.rowcount:
        await db.rollback()
        raise HTTPException(409, "Call changed; refresh its current state")
    if status == "accepted":
        await db.execute(update(CallParticipant).where(CallParticipant.call_id == call.id).values(last_seen=now))
    elif status not in ACTIVE:
        await db.execute(update(CallParticipant).where(CallParticipant.call_id == call.id).values(
            active_user_id=None, left_at=now))
    audit(db, call.id, actor, reason or status, now)
    await db.commit()
    await db.refresh(call)
    return call


async def expire_calls(db):
    now = time.time()
    stale = select(CallParticipant.call_id).where(
        CallParticipant.last_seen < now - settings.rtc_disconnect_timeout_seconds)
    calls = (await db.scalars(select(CallSession).where(
        CallSession.status.in_(ACTIVE), or_(CallSession.deadline <= now,
            (CallSession.status == "accepted") & CallSession.id.in_(stale))))).all()
    for call in calls:
        reason = "unanswered" if call.status == "ringing" else (
            "max_duration" if call.deadline <= now else "disconnected")
        try:
            await transition(db, call, "timeout", reason=reason)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise


async def timeout_worker():
    while True:
        try:
            async with async_session() as db:
                await expire_calls(db)
        except Exception:
            logging.getLogger(__name__).exception("RTC timeout sweep failed")
        await asyncio.sleep(5)


async def get_call(db, user, call_id):
    require_member(user)
    await expire_calls(db)
    call = await db.get(CallSession, call_id, populate_existing=True)
    if not call or call.project_id != user.project_id or user.id not in (call.caller_id, call.callee_id):
        raise HTTPException(404, "Call not found")
    return call


async def validate_peers(db, call):
    peers = (await db.scalars(select(User).where(User.id.in_([call.caller_id, call.callee_id])))).all()
    if len(peers) != 2:
        raise HTTPException(403, "Call participant no longer available")
    for peer in peers:
        require_member(peer)
        if peer.project_id != call.project_id:
            raise HTTPException(403, "Call participant project changed")


async def initiate(db, user, callee_id, media_type):
    require_member(user)
    require_provider()
    if callee_id == user.id:
        raise HTTPException(400, "Cannot call yourself")
    peer = await db.get(User, callee_id)
    if not peer or peer.project_id != user.project_id:
        raise HTTPException(404, "Contact not found")
    require_member(peer)
    await expire_calls(db)
    now = time.time()
    call = CallSession(id=uuid.uuid4().hex, project_id=user.project_id, caller_id=user.id,
                       callee_id=peer.id, media_type=media_type, status="ringing", version=1,
                       created_at=now, deadline=now + settings.rtc_ring_timeout_seconds)
    db.add(call)
    try:
        await db.flush()
        for uid in (user.id, peer.id):
            db.add(CallParticipant(call_id=call.id, user_id=uid, active_user_id=uid, last_seen=now))
        audit(db, call.id, user.id, "ringing", now)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "You or the contact already has an active call")
    return snapshot(call)


async def action(db, user, call_id, action_name):
    call = await get_call(db, user, call_id)
    if action_name in ("accept", "reject") and user.id != call.callee_id:
        raise HTTPException(403, "Only the invited contact can answer")
    if action_name == "accept":
        require_provider()
        await validate_peers(db, call)
        if call.status == "accepted":
            return snapshot(call)
        if call.status != "ringing":
            raise HTTPException(409, "Call is no longer ringing")
        await transition(db, call, "accepted", user.id)
    elif action_name == "reject":
        if call.status == "rejected":
            return snapshot(call)
        if call.status != "ringing":
            raise HTTPException(409, "Call is no longer ringing")
        await transition(db, call, "rejected", user.id, "rejected")
    elif action_name == "end" and call.status in ACTIVE:
        await transition(db, call, "ended", user.id,
                         "cancelled" if call.status == "ringing" else "hangup")
    return snapshot(call)
