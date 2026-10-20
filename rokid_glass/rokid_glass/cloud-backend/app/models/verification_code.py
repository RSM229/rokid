"""短信验证码 —— 手机号注册/登录的身份校验凭证。

与 ``users`` 解耦：验证码只在「发送 → 校验」窗口内有效，校验成功后即作废，
不长期存储。``purpose`` 区分注册/登录，避免同一码跨场景复用。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class VerificationCode(Base):
    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), index=True, comment="手机号")
    code: Mapped[str] = mapped_column(String(8), comment="6 位验证码")
    # purpose: register=注册 | login=登录(OTP)
    purpose: Mapped[str] = mapped_column(String(16), default="register", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, comment="过期时间")
    # 校验成功后写入，防止重放
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="校验成功时间(NULL=未用)"
    )
    # 失败尝试次数（防爆破）
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
