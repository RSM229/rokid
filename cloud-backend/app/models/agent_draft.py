"""聊天追问草稿 —— 「聊天即操作」多轮补字段的持久状态。

为什么落表（而不是进程内存）
--------------------------
追问场景下服务端原本靠「最近 6 轮文本历史」让模型重新拼参数，历史滑出或模型
每轮抽参不一致就会反复追问、丢字段。内存态草稿虽能兜住单次会话，但
``uvicorn --reload`` / 重启即丢，且多进程不安全；工地现场「上午记一半、下午
回来接着补」是常态，有效期应按小时计，因此改为落库。

迁移安全
--------
新表由 ``db.init_db`` 的 ``create_all`` 直接建（与 ``user_security_questions``
同一套路），不动既有表结构，无需停机 ALTER，也不受 ``_reconcile_missing_columns``
「非空且无默认值的列不补」限制影响。

约束
----
一个用户 + 一个工具只保留一条草稿：唯一索引实名 ``ix_agent_drafts_user_tool``
（沿用 ``ix_<表>_<列>`` 命名规则）；新草稿覆盖旧草稿，落库成功后整条删除。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentDraft(Base):
    __tablename__ = "agent_drafts"
    __table_args__ = (
        Index("ix_agent_drafts_user_tool", "user_id", "tool", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户 id")
    tool: Mapped[str] = mapped_column(String(64), comment="待完成的工具名，如 create_work_record")
    args_json: Mapped[str] = mapped_column(Text, comment="已收集参数 JSON（空值不入草稿）")
    missing_json: Mapped[str] = mapped_column(Text, comment="仍缺字段中文标签 JSON")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, index=True, comment="最后更新时间（超 TTL 视为过期）"
    )


class AgentConversationDraft(Base):
    """会话隔离的智能体草稿；独立建表以免改动旧草稿表的唯一索引。"""
    __tablename__ = "agent_conversation_drafts"
    __table_args__ = (
        Index("ix_agent_conv_drafts_user_session_tool", "user_id", "conversation_id", "tool", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, default="{}")
    missing_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class AgentConversationMemory(Base):
    """最近会话原话，独立于业务草稿保存，支持稍后回顾早先分段提供的信息。"""
    __tablename__ = "agent_conversation_memory"
    __table_args__ = (
        Index("ix_agent_conversation_memory_user_session", "user_id", "conversation_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class AgentConversationMessage(Base):
    """智能体账号级聊天记录，供换设备或清缓存后恢复完整会话。"""
    __tablename__ = "agent_conversation_messages"
    __table_args__ = (
        Index(
            "ix_agent_conversation_messages_user_session_id",
            "user_id", "conversation_id", "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, index=True,
    )


class AgentReviewPlan(Base):
    """多事项资料整理计划；确认前只保存草稿，不写正式业务记录。"""
    __tablename__ = "agent_review_plans"
    __table_args__ = (
        Index("ix_agent_review_plans_user_project_session", "user_id", "project_id", "conversation_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
