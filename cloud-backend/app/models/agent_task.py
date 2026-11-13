"""Agent 异步任务状态 —— 「说一句就走」的服务端持久态。

背景（2026-11-13，参考同机 jl-service 的任务契约，但不搬它的引擎）：
``/api/agent/chat/stream`` 原先在客户端断开时直接 ``task.cancel()``，
SSE 一轮没跑完就丢了；用户回到 App 既看不到结果，也无法知道它跑没跑。
这里只补一张轻量状态表，让后台轮次的状态/结果/失败原因跨进程重启可查。

迁移安全
--------
新表由 ``db.init_db`` 的 ``create_all`` 直接建（与 ``agent_drafts`` 同一套路），
不动既有表结构；``migrations/2026-11-13_agent_async_tasks.sql`` 供只读库账号
显式执行。表不存在时 registry 全部写操作静默降级，绝不影响聊天主流程。

约束
----
- 只登记状态与回执，**不自动重跑建档类任务**（重跑会重复建记录）。
  ``retry`` 只把状态重置为 queued 并返回原始请求，由客户端决定是否重放。
- 多项目隔离：查询一律按 ``user_id`` 过滤。
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentAsyncTask(Base):
    __tablename__ = "agent_async_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="agent_chat", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    # JSON 一律存 Text，避开 MySQL/SQLite JSON 方言差异。
    request_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 租约：sweeper 把 lease_until 已过且仍在 queued/running 的任务判超时。
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_agent_async_tasks_sweep", "status", "lease_until"),
        Index("ix_agent_async_tasks_user_created", "user_id", "created_at"),
    )
