"""用户与角色模型 — 监理四角色 RBAC 分权分域

**角色值约定（Step 1 起）**：``users.role`` / ``project_members.role`` 等
一律存**英文 key**（见 :class:`UserRole`），中文仅用于展示，由各端
自行映射（后端 ``ROLE_LABELS`` 仅用于日志/审计等后端自产文案）。

**两级角色**：
- 公司级 —— ``users.org_role``（``super_admin`` / ``admin`` / ``None``），跨项目；
- 项目级 —— ``project_members.role``（每项目一份，Step 2 起参与授权）。

**超管判据**：唯一来源是 :meth:`User.is_super_admin`（即
``org_role == 'super_admin'``），不再使用 ``username == "admin"``。
"""
from datetime import datetime
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.permission_catalog import ALL_PERMISSION_KEYS


class UserRole(str, enum.Enum):
    """监理岗位枚举（值为英文 key，中文见 ROLE_LABELS）"""
    CHIEF_SUPERVISOR = "chief_supervisor"        # 总监理工程师
    DEPUTY_CHIEF = "deputy_chief"                # 总监代表
    SPECIAL_SUPERVISOR = "special_supervisor"    # 专业监理工程师
    INSPECTOR = "inspector"                      # 监理员
    DOCUMENTER = "documenter"                    # 资料员
    WITNESS = "witness"                          # 见证员
    SAFETY_SUPERVISOR = "safety_supervisor"      # 安全监理员


# 英文 key -> 中文展示名。仅用于后端自产文案（日志/审计/403 提示）。
# 接口出参一律返回 key，由各端自行映射为中文展示。
ROLE_LABELS: dict[str, str] = {
    UserRole.CHIEF_SUPERVISOR.value: "总监理工程师",
    UserRole.DEPUTY_CHIEF.value: "总监代表",
    UserRole.SPECIAL_SUPERVISOR.value: "专业监理工程师",
    UserRole.INSPECTOR.value: "监理员",
    UserRole.DOCUMENTER.value: "资料员",
    UserRole.WITNESS.value: "见证员",
    UserRole.SAFETY_SUPERVISOR.value: "安全监理员",
    # 公司级角色（Step 2）：仅 Web 管理后台可见，小程序端不下发
    "super_admin": "超级管理员",
    "admin": "管理员",
}

# 岗位分类（Step 2 的岗位集合模型会用到；此处先落常量，便于统一口径）
# 可叠加：同一人可在同一项目同时持有多个
ROLE_STACKABLE: frozenset[str] = frozenset({
    UserRole.SPECIAL_SUPERVISOR.value,
    UserRole.INSPECTOR.value,
    UserRole.DOCUMENTER.value,
    UserRole.WITNESS.value,
    UserRole.SAFETY_SUPERVISOR.value,
})
# 排他：每项目最多 1 名（监理规范要求"总监唯一"）
ROLE_EXCLUSIVE: frozenset[str] = frozenset({
    UserRole.CHIEF_SUPERVISOR.value,
    UserRole.DEPUTY_CHIEF.value,
})

# 公司级角色
ORG_ROLE_SUPER_ADMIN = "super_admin"
ORG_ROLE_ADMIN = "admin"
ORG_ROLES: frozenset[str] = frozenset({ORG_ROLE_SUPER_ADMIN, ORG_ROLE_ADMIN})

# 审批状态（Step 4 使用；默认 approved 以保证存量账号不受影响）
AUDIT_PENDING = "pending"
AUDIT_APPROVED = "approved"
AUDIT_REJECTED = "rejected"


def role_label(role_key: str | None) -> str:
    """key -> 中文；未知值原样返回（便于排查）。"""
    if not role_key:
        return ""
    return ROLE_LABELS.get(role_key, role_key)


# 角色权限矩阵：每个角色可访问的功能模块。
# 键为英文 key（与 UserRole.value 一致）。
#
# 🔴 Step 3 起，本常量降级为**初始化种子 + 兜底值**：
#   - 启动时若 ``role_permissions`` 表为空，用它播种；
#   - DB 读不到权限时（表为空 / 查询异常）用它兜底，保证不会"全站无权限"。
# 运行时真源是 DB（可在界面配置），读取入口统一收敛到
# ``app.services.permissions.get_matrix()``。
ROLE_PERMISSIONS: dict[str, list[str]] = {
    UserRole.CHIEF_SUPERVISOR.value: [
        "dashboard", "work_record", "inspection", "acceptance", "witness", "station",
        "hazard_ai", "knowledge_base", "kg_manage", "task_assign", "task_manage",
        "document_approve", "ledger", "settings", "user_center", "todo_center",
        "acceptance_template", "local_ai",
        # Step 2：成员增删从 `settings` 拆出为独立 key（设计说明 §7.4 第 7 条）。
        # 总监在本项目内可用；公司级「管理员」凭同一 key 跨项目。
        "member_manage",
        # 2026-10-13：项目信息编辑改为**跨两层**键 —— 总监可维护自家项目的
        # 基本信息（名称/地点/参建单位/项目章）；公司级＝任意项目。
        "project_edit",
        # Step 3：签章管理原为「孤儿键」（前端门控、后端无此权限），
        # 补进目录后默认给到需要签署文书的岗位。
        "signature_manage",
    ],
    UserRole.DEPUTY_CHIEF.value: [
        "dashboard", "work_record", "inspection", "acceptance", "witness", "station",
        "hazard_ai", "knowledge_base", "kg_manage", "task_assign", "task_manage",
        "document_approve", "ledger", "todo_center", "user_center",
        "acceptance_template", "local_ai", "signature_manage",
    ],
    UserRole.SPECIAL_SUPERVISOR.value: [
        "dashboard", "work_record", "inspection", "acceptance", "witness", "station",
        "hazard_ai", "knowledge_base", "task_receive", "ledger", "todo_center", "user_center",
        "local_ai", "signature_manage",
    ],
    UserRole.INSPECTOR.value: [
        "dashboard", "work_record", "inspection", "hazard_ai", "knowledge_base",
        "task_receive", "todo_center", "user_center", "local_ai",
    ],
    # 以下三个岗位为 Step 1 新增（原枚举缺失，见设计说明 §2.8）。
    # 权限集先给最小可用集，Step 2 随岗位集合模型细化。
    UserRole.DOCUMENTER.value: [
        "dashboard", "work_record", "ledger", "user_center", "todo_center", "local_ai",
    ],
    UserRole.WITNESS.value: [
        "dashboard", "work_record", "witness", "user_center", "todo_center", "local_ai",
    ],
    UserRole.SAFETY_SUPERVISOR.value: [
        "dashboard", "work_record", "inspection", "hazard_ai", "task_receive",
        "user_center", "todo_center", "local_ai",
    ],
    # ---------- 公司级角色（Step 2，设计说明 §7.2 权限表） ----------
    # 这些是**跨项目**权限，由 org_role 承载；`member_manage` 是唯一
    # 同时出现在公司级与项目级的 key（公司级＝全公司，项目级＝本项目）。
    ORG_ROLE_SUPER_ADMIN: [
        "project_create", "project_edit", "project_complete", "user_manage",
        "user_approve", "role_grant", "member_manage",
    ],
    ORG_ROLE_ADMIN: [
        "user_approve", "member_manage",
    ],
}

# 全部权限的并集（超管用）——含公司级键。
#
# 🔴 Step 3 起：超管的「全量」改取**权限目录**（permission_catalog），
# 不再取矩阵并集。矩阵现在可以在界面上配置，若继续用并集，把某个权限
# 从所有角色上取消勾选就会连带把超管的权限一起删掉，且**再也配不回来**
# （没人有这个权限 → 没人能授权）＝ 系统锁死。详见 permission_catalog 模块头。
ALL_PERMISSIONS: list[str] = list(ALL_PERMISSION_KEYS)

# 公司级权限 key（仅由 org_role 承载，项目岗位不应持有）。
# 注意：member_manage 与 project_edit 是**跨两层**键（公司级＝全公司/任意项目，
# 项目级＝本项目），故都不在此集合内 —— 它们同时出现在上方公司级角色的权限
# 清单里，两者不矛盾（一个是「谁能持有」，一个是「公司级角色拿什么」）。
ORG_LEVEL_PERMISSIONS: frozenset[str] = frozenset({
    "project_create", "project_complete", "user_manage",
    "user_approve", "role_grant",
})


class User(Base):
    __tablename__ = "users"
    # org_id 为 NULL 时，MySQL 的唯一索引把 NULL 视为互不相同，
    # 因此单企业场景下 (org_id, username) 复合唯一不会误伤；
    # 迁移脚本会把存量数据全部回填 org_id，确保约束真正生效。
    __table_args__ = (
        UniqueConstraint("org_id", "username", name="uq_user_org_username"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    real_name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # ============ 新增字段 ============
    company_name: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="公司/团队名称")
    invite_code: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="邀请码")
    # ============ 新增结束 ============
    # ---- Step 1：组织锚点与两级角色 ----
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True, comment="所属企业"
    )
    org_role: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="公司级角色: super_admin|admin|NULL"
    )
    audit_status: Mapped[str] = mapped_column(
        String(16), default=AUDIT_APPROVED, server_default=AUDIT_APPROVED,
        comment="审批状态: pending|approved|rejected（存量账号默认 approved）",
    )
    # ---- Step 4：人员准入审批（设计说明 §8.8） ----
    # 四个字段均 **nullable**（无 NOT NULL 约束），因此 init_db() 的
    # create_all 与 _reconcile_missing_columns() 都能安全补齐，不需要停机 ALTER。
    approved_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="审批人 user_id（批准或拒绝的操作人）"
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="审批时间"
    )
    reject_reason: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="拒绝理由（仅 rejected 时有值）"
    )
    # ---- Step 4 结束 ----
    platform_user_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="外部平台用户标识（穗建通人员映射预留）"
    )
    # ---- Step 1 结束 ----
    role: Mapped[str] = mapped_column(
        String(32), default=UserRole.INSPECTOR.value,
        comment="主岗位 key（项目级角色真源在 project_members.role）"
    )
    supervisor_level: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="监理级别: 总监|专监|监理员")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="账号是否启用")
    # ---- 「忘记密码」/ 强制改密（2026-10-12） ----
    # 管理员把账号重置为「一次性临时口令」后置 True；用户下次登录（Web/小程序）
    # 会被强制跳转到改密页，改完自动清 False。默认 False（存量账号不受影响）。
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"),
        comment="下次登录是否强制修改密码（管理员重置一次性口令时置 True）",
    )
    # ---- 管理员一次性临时口令的明文暂存（2026-10-19 新增） ----
    # 仅用于后台用户管理列表**直接展示**管理员最近一次重置生成的临时口令，
    # 让用户免记背。它**绝不是用户真实密码**——真实密码只以 bcrypt 哈希存于
    # ``password_hash``，永不可见。本字段在以下任一动作后立即清空：
    #   ① 用户首次登录后自行改密（change_password / forgot_reset）；
    #   ② 管理员直接给账号设新密码（update_user 的 new_password）。
    # 因此列表上「密码」列要么显示一次性临时口令，要么显示「—」（真实密码不可见），
    # 不会出现泄露真实密码的可能。字段可空、无默认值，init_db 的
    # _reconcile_missing_columns 会在启动时自动 ALTER 补列。
    temp_password: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
        comment="管理员重置的一次性临时口令明文（用户改密后立即清空；真实密码永不可见）",
    )
    id_card: Mapped[str | None] = mapped_column(String(18), nullable=True, comment="证件号码")
    certificate_type: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="证件类型")
    certificate_no: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="证件编号")
    gender: Mapped[str | None] = mapped_column(String(4), nullable=True, comment="性别")
    email: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="邮箱")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="头像URL")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True, comment="个人简介")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    project: Mapped["Project | None"] = relationship(back_populates="users")

    # ---------- 两级角色判定 ----------

    def is_super_admin(self) -> bool:
        """超管判据的**唯一来源**，取代散落的 ``username == "admin"``。"""
        return self.org_role == ORG_ROLE_SUPER_ADMIN

    def is_org_admin(self) -> bool:
        """公司级「管理员」（跨项目，但权限小于超管）。"""
        return self.org_role == ORG_ROLE_ADMIN

    def is_org_level(self) -> bool:
        """是否持有任一公司级角色。"""
        return self.org_role in ORG_ROLES

    # ---------- 权限判定 ----------

    def has_permission(self, permission: str) -> bool:
        """检查当前角色是否拥有指定权限。

        Step 1 阶段：超管持全量；其余按 ``users.role``（主岗）查表。
        Step 2 起：``effective_permissions`` 会由 ``get_current_user`` 注入，
        内容为「公司级权限 ∪ 当前项目岗位集合的权限」，此处优先取它，
        因此本方法的调用方（19 处）**无需改动**。
        """
        if self.is_super_admin():
            return True
        effective = getattr(self, "effective_permissions", None)
        if effective is not None:
            return permission in effective
        return permission in ROLE_PERMISSIONS.get(self.role, [])

    def permission_set(self) -> list[str]:
        """当前生效的权限集合（供登录/JWT/``/me`` 组装）。"""
        if self.is_super_admin():
            return list(ALL_PERMISSIONS)
        effective = getattr(self, "effective_permissions", None)
        if effective is not None:
            return list(effective)
        return list(ROLE_PERMISSIONS.get(self.role, []))


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_project_org_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(64))
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="工程名称简称")
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 记录文书的基础资料，未维护的数据不以通用占位冒充。
    construction_unit: Mapped[str | None] = mapped_column(String(256), nullable=True)
    supervision_unit: Mapped[str | None] = mapped_column(String(256), nullable=True)
    supervision_org: Mapped[str | None] = mapped_column(String(256), nullable=True)
    acceptance_participants: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[str | None] = mapped_column(String(32), nullable=True)
    longitude: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seal_signature_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ---- Step 1：组织锚点与生命周期 ----
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True, comment="所属企业"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active",
        comment="项目状态: active(进行中)|completed(已完工)|archived(已归档)",
    )
    # Step 5：结项时间（转场留痕——项目生命周期的终点）。
    # nullable，故可被 init_db 的 _reconcile_missing_columns 自动补列。
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="结项时间（NULL=未结项）"
    )
    # ---- Step 1 结束 ----
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # 创建 / 负责的管理员（项目负责人）。用于「按管理员授权」时界定
    # "某管理员名下的项目" = 其创建 / 负责的项目，而非其作为成员的全部项目。
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True,
        comment="创建 / 负责的管理员",
    )

    users: Mapped[list["User"]] = relationship(back_populates="project")
