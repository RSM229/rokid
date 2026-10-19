"""密保问题 —— 「忘记密码」自助重置的身份校验依据。

设计取舍（为什么是独立表，而不是 users 上的 JSON 列）：
- 便于按 ``user_id`` 查询 / 清理，且答案哈希与 ``users.password_hash``
  同族（bcrypt），不必把可变长度结构塞进单列做解析；
- 迁移安全：新表由 ``create_all`` 直接建，不动既有 users 表结构。

安全约定：
- 答案**只存哈希**（bcrypt），与密码同强度，库里没有明文可读；
- 比对前统一 ``strip().lower()`` 归一化 —— 英文大小写 / 首尾空格不影响判定，
  中文不受影响；
- 每个用户最多 :data:`MAX_SECURITY_QUESTIONS` 题；重置时必须**全部答对**。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

#: 单个账号可设置的密保问题上限（重置时须全部答对）
MAX_SECURITY_QUESTIONS = 3


class UserSecurityQuestion(Base):
    __tablename__ = "user_security_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户 id")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="题目顺序 0..2")
    question: Mapped[str] = mapped_column(String(256), comment="问题文本（明文，展示用）")
    answer_hash: Mapped[str] = mapped_column(String(256), comment="答案 bcrypt 哈希")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
