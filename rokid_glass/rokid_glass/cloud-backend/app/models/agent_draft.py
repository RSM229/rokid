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

from sqlalchemy import DateTime, Index, Integer, String, Text
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
