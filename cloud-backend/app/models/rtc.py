"""Durable RTC signaling state; never stores media or provider credentials."""
from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class CallSession(Base):
    __tablename__ = "rtc_call_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    caller_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    callee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    media_type: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float)
    accepted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    ended_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadline: Mapped[float] = mapped_column(Float, index=True)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class CallParticipant(Base):
    __tablename__ = "rtc_call_participants"
    call_id: Mapped[str] = mapped_column(ForeignKey("rtc_call_sessions.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    # Unique reservation prevents simultaneous calls, including across workers.
    active_user_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    last_seen: Mapped[float] = mapped_column(Float)
    joined_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    left_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class CallAudit(Base):
    __tablename__ = "rtc_call_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("rtc_call_sessions.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event: Mapped[str] = mapped_column(String(32))
    at: Mapped[float] = mapped_column(Float)
