"""Agent 记忆的作用域解析 —— 项目隔离与用户隔离的唯一入口。

为什么需要这一层
----------------
2026-11-09 起记忆分成两层：

- **个人记忆**（``agent_user_preferences``）：按 ``user_id`` 隔离；
- **项目记忆**（``agent_project_memories``）：按 ``project_id`` 隔离，由项目
  总监维护、全项目成员共享。

两者都不能信任客户端传来的 ``user_id`` / ``project_id``。作用域一律由服务端
从登录态推导：

1. ``project_id`` 取 ``users.project_id``（当前上下文），并用
   ``project_members``（``left_at IS NULL``）校验有效成员身份；
2. 会话必须绑定同一项目，禁止把 A 项目的 ``conversation_id`` 拿到 B 项目续聊；
3. 公司管理员（``org_role``）只在排障读取时兜底，写权限仍按项目岗位判定。

成员岗位真源是 ``project_members.role``，不是全局单值 ``users.role`` —— 后者
会让「A 项目总监 / B 项目监理员」在 B 项目继续拿到总监权限（见
``app/services/permissions.py`` 模块头）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_draft import AgentConversationMemory, AgentConversationMessage
from app.models.user import ORG_ROLES, User, UserRole
from app.services.permissions import member_role_codes

logger = logging.getLogger(__name__)

#: 项目记忆的维护岗位：总监为主，总监代表可代管（与文书签认口径一致）。
PROJECT_MEMORY_MANAGER_ROLES = frozenset({
    UserRole.CHIEF_SUPERVISOR.value,
    UserRole.DEPUTY_CHIEF.value,
})

#: 表示「这条会话还没有任何落库记录」，区别于「记录存在但 project_id 为 NULL」。
UNBOUND = object()


class ConversationProjectMismatch(ValueError):
    """会话已绑定其他项目，禁止在当前项目续聊。"""

    def __init__(self, conversation_id: str, bound_project_id: int | None,
                 current_project_id: int | None) -> None:
        self.conversation_id = conversation_id
        self.bound_project_id = bound_project_id
        self.current_project_id = current_project_id
        super().__init__("会话不属于当前项目，请新建对话")


class ProjectContextError(ValueError):
    """当前项目上下文缺失或用户不是有效成员。"""


@dataclass(frozen=True)
class AgentScope:
    """一次 agent 请求的服务器端作用域。"""

    user_id: int
    project_id: int | None
    conversation_id: str | None
    role_codes: tuple[str, ...] = ()
    is_org_admin: bool = False
    is_super_admin: bool = False

    @property
    def is_member(self) -> bool:
        return bool(self.role_codes)

    @property
    def can_manage_project_memory(self) -> bool:
        if self.project_id is None:
            return False
        if self.is_super_admin:
            return True
        return bool(set(self.role_codes) & PROJECT_MEMORY_MANAGER_ROLES)

    @property
    def can_read_project_memory(self) -> bool:
        if self.project_id is None:
            return False
        return self.is_member or self.is_org_admin


async def get_conversation_project_id(
    db: AsyncSession,
    user_id: int,
    conversation_id: str,
) -> object:
    """返回会话已绑定的项目 id；没有记录时返回 ``UNBOUND``。

    先查细粒度消息表（新真源），再回退会话原话表（老数据）。两表都只按
    ``user_id`` 过滤，因此不会把别人的会话判给当前用户。
    """
    if not user_id or not conversation_id:
        return UNBOUND
    for model in (AgentConversationMessage, AgentConversationMemory):
        row = (
            await db.execute(
                select(model.project_id)
                .where(
                    model.user_id == int(user_id),
                    model.conversation_id == conversation_id,
                )
                .order_by(model.id.desc())
                .limit(1)
            )
        ).first()
        if row is not None:
            return row[0]
    return UNBOUND


async def resolve_agent_scope(
    db: AsyncSession,
    user: User,
    conversation_id: str | None = None,
) -> AgentScope:
    """解析本轮聊天的作用域；会话跨项目时抛 ``ConversationProjectMismatch``。"""
    user_id = int(user.id)
    project_id = getattr(user, "project_id", None)
    org_role = getattr(user, "org_role", None)
    is_super_admin = bool(getattr(user, "is_super_admin", lambda: False)())
    is_org_admin = bool(org_role in ORG_ROLES) or is_super_admin
    roles: list[str] = []
    if project_id is not None:
        roles = await member_role_codes(db, user_id, project_id)
        if not roles and not is_org_admin:
            # 脏数据（已离场但 users.project_id 未清）：降级为无项目上下文，
            # 不阻断聊天，但项目记忆/项目会话全部不参与。
            logger.warning(
                "agent scope: 用户 %s 不是项目 %s 的有效成员，降级为无项目上下文",
                user_id, project_id,
            )
            project_id = None
    normalized_conversation_id = (conversation_id or "").strip()[:64] or None
    if normalized_conversation_id and project_id is not None:
        bound = await get_conversation_project_id(db, user_id, normalized_conversation_id)
        # project_id=None 表示升级前的老会话：没有项目归属，允许在当前项目续聊，
        # 首次写入时由 agent_tools 把整条会话「认领」到当前项目（同用户数据）。
        if bound is not UNBOUND and bound is not None and bound != project_id:
            raise ConversationProjectMismatch(
                normalized_conversation_id, bound, project_id,
            )
    return AgentScope(
        user_id=user_id,
        project_id=project_id,
        conversation_id=normalized_conversation_id,
        role_codes=tuple(roles),
        is_org_admin=is_org_admin,
        is_super_admin=is_super_admin,
    )


async def resolve_project_memory_scope(
    db: AsyncSession,
    user: User,
) -> AgentScope:
    """项目记忆接口专用作用域：必须有当前项目，且是成员/管理员。

    与聊天不同，这里不能静默降级：读写项目记忆必须明确属于某个项目。
    """
    project_id = getattr(user, "project_id", None)
    if project_id is None:
        raise ProjectContextError("请先加入或切换到项目，再管理项目记忆")
    org_role = getattr(user, "org_role", None)
    is_super_admin = bool(getattr(user, "is_super_admin", lambda: False)())
    is_org_admin = bool(org_role in ORG_ROLES) or is_super_admin
    roles = await member_role_codes(db, int(user.id), project_id)
    if not roles and not is_org_admin:
        raise ProjectContextError("你不是当前项目的有效成员，无权访问项目记忆")
    return AgentScope(
        user_id=int(user.id),
        project_id=project_id,
        conversation_id=None,
        role_codes=tuple(roles),
        is_org_admin=is_org_admin,
        is_super_admin=is_super_admin,
    )
