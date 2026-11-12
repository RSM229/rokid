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

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# MySQL TEXT 仅 65535 字节，一次多图计划的 UTF-8 JSON 会溢出（1406 Data too long）。
# 用 variant 而不是直接 MEDIUMTEXT：SQLite/测试库仍按 TEXT 走，避免方言编译失败。
_LONG_TEXT = Text().with_variant(MEDIUMTEXT(), "mysql")


class AgentDraft(Base):
    __tablename__ = "agent_drafts"
    __table_args__ = (
        Index("ix_agent_drafts_user_tool", "user_id", "tool", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户 id")
    project_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True, comment="项目隔离键；NULL=老数据/无项目上下文",
    )
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
    project_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True, comment="项目隔离键；与 conversation_id 同源",
    )
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, default="{}")
    missing_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class AgentConversationMemory(Base):
    """最近会话原话，独立于业务草稿保存，支持稍后回顾早先分段提供的信息。

    ``project_id`` 是项目隔离键：同一用户切换项目后，旧项目的会话原话不再
    进入新项目的模型上下文（读取方必须带当前项目过滤，见 agent_tools）。
    """
    __tablename__ = "agent_conversation_memory"
    __table_args__ = (
        Index("ix_agent_conversation_memory_user_session", "user_id", "conversation_id", unique=True),
        Index("ix_agent_conversation_memory_user_project", "user_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True, comment="项目隔离键；NULL=老数据/无项目",
    )
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
        Index("ix_agent_conversation_messages_user_project", "user_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    project_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True, comment="项目隔离键；历史/恢复都按它过滤",
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
    plan_json: Mapped[str] = mapped_column(_LONG_TEXT, default="{}")
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_result_json: Mapped[str | None] = mapped_column(_LONG_TEXT, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class AgentRequestTrace(Base):
    """One trace spanning request planning, chat, confirmation and execution.

    Trace writes use an isolated database session. A broken or not-yet-migrated
    trace table must never roll back an already valid business request.
    """
    __tablename__ = "agent_request_traces"
    __table_args__ = (
        Index("ix_agent_request_traces_request_id", "request_id", unique=True),
        Index("ix_agent_request_traces_user_project", "user_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    current_stage: Mapped[str] = mapped_column(String(64), default="")
    message_excerpt: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(160), default="")
    request_plan_json: Mapped[str] = mapped_column(_LONG_TEXT, default="{}")
    stages_json: Mapped[str] = mapped_column(_LONG_TEXT, default="[]")
    result_json: Mapped[str] = mapped_column(_LONG_TEXT, default="{}")
    error_stage: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    review_plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_record_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AgentUserPreference(Base):
    """用户级长期记忆（个人记忆）—— 跨会话生效。

    与 ``AgentConversationMemory``（会话级原话）和 ``AgentProjectMemory``
    （项目级共享规则）不同，本表存的是**用户自己的、希望以后一直遵守**
    的做事规则。来源两类：
    - ``user_declared``：用户在管理页/聊天中主动声明（如「整改回复必须附前后对比照片」）；
    - ``assistant_suggested``：助手发现用户重复表达同一习惯后建议沉淀（可被用户关闭）。

    作用域：按 ``user_id`` 隔离，跨项目不串。``project_id`` 为 NULL 表示
    「跟人走、所有项目都生效」；非空表示「只在当前项目生效」，用于避免
    A 项目的个人口径泄漏到 B 项目。默认（管理页/聊天建议）写 NULL。

    注入口径：只把 ``active=True`` 且（``project_id IS NULL`` 或等于当前项目）
    的规则注入 agent 系统上下文（见 ``app/services/agent_preferences.py``）。
    """
    __tablename__ = "agent_user_preferences"
    __table_args__ = (
        Index("ix_agent_preferences_user_active", "user_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户 id")
    project_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
        comment="NULL=全局个人记忆；非空=仅该项目生效",
    )
    rule: Mapped[str] = mapped_column(String(500), comment="偏好规则文本（注入 prompt 用）")
    category: Mapped[str] = mapped_column(String(32), default="general", comment="分类：general/rectification/record/writing")
    source: Mapped[str] = mapped_column(String(24), default="user_declared", comment="来源：user_declared/assistant_suggested")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, comment="是否生效；false 表示用户已关闭/删除")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, index=True,
    )


class AgentProjectMemory(Base):
    """项目级记忆 —— 由项目总监维护、全项目成员共享的长期口径。

    设计口径（2026-11-09）
    ---------------------
    - **挂在项目上，不挂在人上**：换总监/总监离场不丢，新总监直接继承；
      旧总监的个人记忆不会因此共享给项目。
    - **写权限**：当前项目的 ``chief_supervisor`` / ``deputy_chief``（读
      ``project_members`` 真源，不看全局 ``users.role``），``super_admin`` 兜底。
    - **读权限**：本项目有效成员；公司管理员排障可读。个人记忆与项目规约
      物理分表，互不影响。
    - **来源分层**：``chief_declared`` / ``deputy_declared`` / ``member_suggested``
      / ``system``。成员只能“建议”，``status='pending'`` 等总监审批后才生效。

    与 ``AgentUserPreference``（个人记忆）的注入优先级：项目规约 > 个人记忆；
    用户本轮的明确指令仍优先于两者（由 prompt 注入块里的说明约束模型）。
    """
    __tablename__ = "agent_project_memories"
    __table_args__ = (
        Index("ix_agent_project_memories_project_status", "project_id", "status"),
        Index("ix_agent_project_memories_project_active", "project_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True, comment="项目隔离键（记忆跟项目走）")
    content: Mapped[str] = mapped_column(String(500), comment="项目统一口径原文")
    kind: Mapped[str] = mapped_column(String(16), default="rule", comment="rule/fact/template")
    category: Mapped[str] = mapped_column(
        String(32), default="general", comment="general/rectification/record/writing",
    )
    source: Mapped[str] = mapped_column(
        String(24), default="chief_declared",
        comment="chief_declared/deputy_declared/member_suggested/system",
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", comment="pending/active/disabled/rejected",
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, comment="置顶，预算截断时优先保留")
    author_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="原提交人")
    approved_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="审批人（总监/代管）")
    origin_preference_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="由个人记忆发布而来时留痕",
    )
    # 使用统计：每轮注入时批量 +1，供管理页看「哪条口径真的在用」。
    use_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", comment="被注入到对话上下文的次数",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="最近一次被注入的时间",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, index=True,
    )


class AgentProjectMemoryAudit(Base):
    """项目规约变更/使用审计流水。

    - 变更类：create/suggest/update/enable/disable/delete/approve/reject/publish；
    - 使用类：use（每轮注入一条，detail 里带 memory_ids 与 conversation_id）。

    审计随项目删除清理；不存 500 字全文，只留摘要，避免审计表无限膨胀。
    """
    __tablename__ = "agent_project_memory_audit"
    __table_args__ = (
        Index("ix_agent_project_memory_audit_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(24), comment="create/update/enable/disable/delete/approve/reject/publish/use")
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class AgentFlowInstance(Base):
    """Durable, idempotent execution instance for formal business writes.

    The initial RUNNING row is committed before taking business locks. The final
    COMMITTED row is updated in the same transaction as the business rows. A
    process crash therefore leaves either RUNNING (safe to retry) or the whole
    batch plus COMMITTED (safe to replay), never a half-written batch.
    """
    __tablename__ = "agent_flow_instances"
    __table_args__ = (
        Index("ix_agent_flow_instances_execution_id", "execution_id", unique=True),
        Index("ix_agent_flow_instances_user_project", "user_id", "project_id"),
        Index("ix_agent_flow_instances_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(160), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    flow_type: Mapped[str] = mapped_column(String(48), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(160), default="")
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="PREPARED")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    result_json: Mapped[str] = mapped_column(_LONG_TEXT, default="{}")
    error_message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
