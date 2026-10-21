"""认证鉴权路由 — 监理四角色登录 + RBAC 分权分域"""
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import io
import logging
import re
import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, func, or_, select, update
from pydantic import BaseModel, ConfigDict, Field, field_validator
import jwt
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.config import settings
from app.db import Base, get_db
from app.models.user import (
    AUDIT_APPROVED,
    AUDIT_PENDING,
    AUDIT_REJECTED,
    ORG_ROLE_SUPER_ADMIN,
    ORG_ROLES,
    ROLE_LABELS,
    Project,
    User,
    UserRole,
)
from app.models.invite_code import InviteCode
from app.models.miniprogram import WechatBindingAudit, WechatUserBinding
from app.models.organization import Organization
from app.models.project_member import ProjectMember
from app.models.security_audit import SecurityAudit
from app.models.security_question import (
    MAX_SECURITY_QUESTIONS,
    UserSecurityQuestion,
)
from app.services.passwords import pwd_context

router = APIRouter()
logger = logging.getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    username: str
    real_name: str
    role: str
    permissions: list[str]
    avatar_url: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    # Step 1：公司级角色（'super_admin' / 'admin' / None）。
    # 三端据此判断「是否超管」，取代原先的 username == 'admin' 硬编码。
    org_role: str | None = None
    # 「忘记密码」/ 强制改密：管理员重置为一次性口令后置 True，
    # 各端登录成功但须先跳转改密页（改完自动清 False）。
    must_change_password: bool = False


class LoginRequest(BaseModel):
    """登录请求（用户名 + 密码）。

    ``role`` 为**可选**：省略时服务端按账号真实角色自动判定（见 login）。
    旧调用方仍可传 role，行为完全不变（向后兼容）。

    🔴 2026-10-21：原「手机号 + 短信验证码（OTP）」登录已下线 ——
    腾讯云短信资质审批要 2~3 周且按条计费，而注册准入改为邀请码后，
    OTP 只剩「忘记密码」一个用途，而该场景已由密保问题自助重置
    （``/forgot/questions`` → ``/forgot/verify`` → ``/forgot/reset``）覆盖。
    """
    username: str | None = None
    password: str | None = None
    role: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    real_name: str = Field(min_length=1, max_length=64)
    role: str
    phone: str | None = Field(default=None, max_length=20)
    project_id: int | None = None
    # 公司级角色（跨项目）。None / "" = 纯项目级成员。仅超管可设。
    org_role: str | None = None


class UpdateUserRequest(BaseModel):
    real_name: str | None = Field(default=None, min_length=1, max_length=64)
    role: str | None = None
    phone: str | None = Field(default=None, max_length=20)
    project_id: int | None = None
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=128)
    # 公司级角色。语义与 other 字段不同，见 update_user 的显式处理：
    #   None（未传）  = 不修改
    #   ""（空串）    = 清除公司级角色（降为纯项目级）
    #   "super_admin"/"admin" = 授予
    org_role: str | None = None


class RoleInfoResponse(BaseModel):
    roles: list[dict]
    # 🔴 2026-10-21：registration_roles / public_registration_enabled 已下线 ——
    # 注册改为「邀请码准入」，可注册岗位由码上的 role 决定，不再是全局白名单。
    # 两端前端从未读过这两个字段（grep 零命中），删除无兼容影响。


# ---------- 「忘记密码」自助重置 与 密保问题（2026-10-12） ----------
class ForgotQuestionsRequest(BaseModel):
    """按用户名取回密保题目（不含答案）"""
    username: str = Field(min_length=1, max_length=64)


class ForgotVerifyRequest(BaseModel):
    """提交密保答案以换取一次性重置凭证"""
    username: str = Field(min_length=1, max_length=64)
    answers: list[str] = Field(default_factory=list)


class ForgotResetRequest(BaseModel):
    """凭重置凭证设置新密码"""
    reset_token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class SecurityQuestionItem(BaseModel):
    question: str = Field(min_length=1, max_length=256)
    answer: str = Field(min_length=1, max_length=128)


class SetSecurityQuestionsRequest(BaseModel):
    """设置密保问题（需原密码，防止 token 外泄后被静默换绑）"""
    current_password: str
    questions: list[SecurityQuestionItem] = Field(default_factory=list)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def hash_password(password: str) -> str:
    try:
        return pwd_context.hash(password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无法验证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ============ 短信验证码辅助（手机号注册 / 登录 OTP） ============
async def _issue_token(user: User, db: AsyncSession, role: str | None = None) -> TokenResponse:
    """为已通过身份校验的用户签发 Web 登录态 TokenResponse。

    从 ``login`` 抽出，供密码登录 / OTP 登录 / 注册即登录复用，避免三处重复。
    角色口径与 login 完全一致（公司级记 org_role，项目级记主岗）。
    """
    from app.services.permissions import effective_permissions

    permissions = await effective_permissions(user, db)
    if user.is_super_admin() and role in ORG_ROLES:
        login_role = role
    elif user.is_org_level():
        login_role = user.org_role or user.role
    elif role in ORG_ROLES:
        login_role = role
    else:
        login_role = user.role

    token = create_access_token({
        "sub": str(user.id),
        "role": login_role,
        "permissions": permissions,
    })
    project = await db.get(Project, user.project_id) if user.project_id else None
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        username=user.username,
        real_name=user.real_name,
        role=login_role,
        permissions=permissions,
        avatar_url=user.avatar_url,
        project_id=user.project_id,
        project_name=project.name if project else None,
        org_role=user.org_role,
        must_change_password=bool(user.must_change_password),
    )


async def get_user_from_token(token: str, db: AsyncSession) -> User:
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if user_id is None or payload.get("purpose") not in (None, "access"):
        raise HTTPException(status_code=401, detail="无效访问凭证")
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return user


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """解析凭证并**合成生效权限**。

    Step 2：这里是一次性的权限合成点（设计说明 §7.6）——
    把「公司级 ∪ 当前项目岗位集合」挂到 ``user.effective_permissions``，
    使同步的 ``has_permission()`` 无需签名变更即可反映**当前项目**的授权。
    这是"跨项目角色串权"（A 项目总监、B 项目监理员）的修复落点。

    另外解析 ``X-Project-Id``：公司级账号在「全部项目」视角下新建记录时，
    前端用它指定本次写入的目标项目（见 ``_attach_write_project``）。
    """
    user = await get_user_from_token(token, db)
    from app.services.permissions import attach_effective_permissions

    user = await attach_effective_permissions(user, db)
    await _attach_write_project(request, user, db)
    return user


# 公司级账号在「全部项目」视角下新建时，用这个头指定「这次写到哪个项目」。
WRITE_PROJECT_HEADER = "X-Project-Id"


async def _attach_write_project(request: Request, user: User, db: AsyncSession) -> None:
    """把请求头指定的写入目标项目挂到 ``user._write_project_id``。

    只在**公司级账号**（超管 / 管理员）上生效，且项目必须真实存在 ——
    其他人即使伪造该头也不会改变写入归属，仍按 ``user.project_id`` 落库。
    没有该头（普通单项目视角）时置 None，行为与改造前完全一致。
    """
    setattr(user, "_write_project_id", None)
    raw = (request.headers.get(WRITE_PROJECT_HEADER) or "").strip()
    if not raw or not _can_manage_all_projects(user):
        return
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    if await db.get(Project, pid) is None:
        return
    setattr(user, "_write_project_id", pid)


def require_project_id(user: User) -> int:
    """**写入**归属项目。

    默认取 ``user.project_id``（用户当前所在项目）。公司级账号在「全部项目」
    视角下新建时，前端先让用户选一个目标项目，再以 ``X-Project-Id`` 带过来，
    由 ``get_current_user`` 挂到 ``user._write_project_id`` —— 这里优先采用，
    保证「全部项目」视角下也能正常新建，且数据落到用户明确选择的项目。
    """
    target = getattr(user, "_write_project_id", None)
    if target and _can_manage_all_projects(user):
        return int(target)
    if user.project_id is None:
        raise HTTPException(status_code=400, detail="请先选择或绑定项目")
    return user.project_id


def project_scope_id(user: User, all_projects: bool = False) -> int | None:
    """**读接口**的项目范围：公司级账号 + ``all_projects`` → ``None``（不限项目）。

    写入接口一律用 ``require_project_id``（必须落到具体项目）：全部项目视角下
    新建记录没有归属，不能让用户在不知情的情况下把数据写进某个项目。

    用法::

        scope = project_scope_id(user, all_projects)
        stmt = select(Model)
        if scope is not None:
            stmt = stmt.where(Model.project_id == scope)
    """
    if all_projects and _can_manage_all_projects(user):
        return None
    return require_project_id(user)


def can_read_project(
    obj_project_id: int | None, user: User, all_projects: bool = False
) -> bool:
    """**详情/下载类只读接口**的归属校验（替代 ``obj.project_id != require_project_id(user)``）。

    公司级账号 + ``all_projects`` → 任意项目可读；否则仍严格限本项目。
    写入接口一律不得使用本函数。
    """
    if obj_project_id is None:
        return False
    if all_projects and _can_manage_all_projects(user):
        return True
    return obj_project_id == user.project_id


def require_permission(permission: str):
    async def permission_checker(user: User = Depends(get_current_user)) -> User:
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"您的角色（{ROLE_LABELS.get(user.role, user.role)}）无权访问此功能，需要权限: {permission}",
            )
        return user
    return permission_checker


def require_role(*roles: str):
    async def role_checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"需要角色: {', '.join(roles)}")
        return user
    return role_checker


# ============ 密保问题 / 忘记密码自助重置 / 强制改密（2026-10-12） ============
# 「忘记密码」共有两条落地路径：
#   ① 自助 —— 密保问题校验通过 → 一次性重置凭证 → 用户自行设新密码；
#   ② 兜底 —— 管理员在用户管理把账号重置为一次性临时口令，用户首登强制改密。
# 二者都以「只存哈希、不落明文」为前提，并写 security_audit 留痕。

#: 重置凭证有效期（分钟）。凭证与当前密码哈希绑定，改密即失效（见 _password_fingerprint）。
RESET_TOKEN_TTL_MINUTES = 10
#: 同一用户名在窗口内的密保尝试上限（抬高答案爆破成本）
FORGOT_MAX_ATTEMPTS = 5
FORGOT_WINDOW_SECONDS = 600
#: 一次性临时口令字符集（剔除易混字符 I l O o 0 1）
_TEMP_PWD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
#: 进程内密保尝试记录（key: 小写用户名）。单进程 uvicorn 下够用，重启即清零。
_forgot_attempts: dict[str, list[float]] = {}


def _request_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def _audit(
    db: AsyncSession,
    action: str,
    *,
    user: User | None = None,
    username: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    """写一条安全审计（随调用方事务提交）。"""
    db.add(SecurityAudit(
        user_id=getattr(user, "id", None),
        username=username or getattr(user, "username", None),
        action=action,
        detail=(detail or None) and detail[:500],
        ip=ip,
    ))


def _normalize_answer(raw: str | None) -> str:
    """答案归一化：去首尾空白 + 转小写（英文大小写不敏感；中文不受影响）。"""
    return (raw or "").strip().lower()


def _hash_answer(raw: str) -> str:
    return pwd_context.hash(_normalize_answer(raw))


def _verify_answer(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(_normalize_answer(raw), hashed)
    except Exception:
        return False


def _password_fingerprint(password_hash: str | None) -> str:
    """重置凭证与「当前密码」绑定的指纹。

    改密后 ``password_hash`` 变化 ⇒ 指纹变化 ⇒ 旧 reset_token 立即失效，
    于是无需额外数据表即可获得「一次性」语义（用一次就作废）。
    """
    return hashlib.sha256((password_hash or "").encode("utf-8")).hexdigest()[:16]


def _create_reset_token(user: User) -> str:
    return create_access_token(
        {
            "sub": str(user.id),
            "purpose": "pwd_reset",
            "fp": _password_fingerprint(user.password_hash),
        },
        expires_delta=timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
    )


def _decode_reset_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="重置凭证无效或已过期，请重新验证密保问题"
        ) from exc
    # purpose 隔离：pwd_reset 凭证不能当 access_token 用（get_user_from_token 亦拦截）
    if payload.get("purpose") != "pwd_reset":
        raise HTTPException(status_code=400, detail="重置凭证无效，请重新验证密保问题")
    return payload


def _forgot_throttle_key(username: str) -> str:
    return (username or "").strip().lower()


def _check_forgot_throttle(username: str) -> None:
    now = time.time()
    key = _forgot_throttle_key(username)
    hits = [t for t in _forgot_attempts.get(key, []) if now - t < FORGOT_WINDOW_SECONDS]
    _forgot_attempts[key] = hits
    if len(hits) >= FORGOT_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="尝试次数过多，请 10 分钟后再试")


def _record_forgot_failure(username: str) -> None:
    key = _forgot_throttle_key(username)
    _forgot_attempts.setdefault(key, []).append(time.time())


def _generate_temp_password(length: int = 12) -> str:
    return "".join(secrets.choice(_TEMP_PWD_ALPHABET) for _ in range(length))


# ============ 邀请码验证函数（从数据库查询） ============
async def validate_invite_code(code: str, db: AsyncSession) -> dict:
    """从数据库验证邀请码。

    Step 4（§8.8.1 / A-1）：出参补 ``org_id`` / ``org_name`` —— 码只承载**企业归属**，
    注册时据此写 ``users.org_id``；项目与岗位一律由审批指定（码不带 project_id）。
    """
    # 查询邀请码
    stmt = select(InviteCode).where(
        InviteCode.code == code.upper(),
        InviteCode.is_active == True
    )
    result = await db.execute(stmt)
    invite = result.scalar_one_or_none()
    
    if not invite:
        return {"valid": False, "message": "邀请码不存在", "level": None, "role": None,
                "org_id": None, "org_name": None}
    
    # 检查是否过期
    if invite.expires_at and invite.expires_at < datetime.now():
        return {"valid": False, "message": "邀请码已过期", "level": None, "role": None,
                "org_id": None, "org_name": None}
    
    # 检查使用次数
    if invite.max_uses and invite.used_count >= invite.max_uses:
        return {"valid": False, "message": "邀请码已使用完", "level": None, "role": None,
                "org_id": None, "org_name": None}

    # Step 4：企业归属（码上的 org_id，可能为 None —— 老码/未指定）
    org_name = None
    if invite.org_id:
        org = await db.get(Organization, invite.org_id)
        org_name = org.name if org else None

    return {
        "valid": True,
        "message": "有效",
        "level": invite.level,
        "role": invite.role,
        "invite_id": invite.id,
        "org_id": invite.org_id,
        "org_name": org_name,
        "project_id": invite.project_id,  # 升级：项目邀请码带 project_id
    }


@router.get("/invite-codes/validate")
async def check_invite_code(code: str, db: AsyncSession = Depends(get_db)):
    """只读校验邀请码，供小程序注册页实时回显企业归属。

    🔴 2026-10-21 起这句话**终于是事实**：注册（``/miniprogram/auth/wechat-register``）
    改成了「邀请码准入」，与本接口共用同一个 ``validate_invite_code``，
    校验口径不存在第二套规则。此前注册走手机号+短信、根本不查邀请码，
    这段注释是 Step 1 遗留的过期描述。

    注意：本接口**不消耗使用次数**，仅在注册/join 成功时才递增 ``used_count``。
    公开只读（无登录态依赖）——注册时用户还没有 token。
    """
    result = await validate_invite_code(code, db)
    if not result["valid"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return {
        "valid": True,
        "level": result["level"],
        "role": result["role"],
        # Step 4：注册页可显示「将加入：XX 公司」，并提示注册后需等待审核
        "org_name": result.get("org_name"),
    }


# ============ 邀请码管理（仅超管，权限 key = user_manage） ============
# 背景：invite_codes 表自建立起只有读取没有写入，生产 0 行、注册链路因此
# 完全不可用（Web 强制邀请码 → 无码可填；小程序不校验 → 绕过准入）。
# 这里补齐创建/列表/停用三个接口，把「邀请码」从死数据变成可运营资产。

INVITE_LEVELS = ("personal", "team", "enterprise")
_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉 I/O/0/1 易混字符


def _gen_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))


def _invite_row_dict(r: InviteCode, project_name: str | None, now: datetime) -> dict:
    """邀请码出参统一序列化（全局列表与项目级列表共用，避免两处走形）。"""
    return {
        "id": r.id,
        "code": r.code,
        # 项目归属：非空 = 项目负责人发的「加成员」码；None = 历史公司码
        "project_id": r.project_id,
        "project_name": project_name,
        "level": r.level,
        "role": r.role,
        "role_label": ROLE_LABELS.get(r.role, r.role),
        "max_uses": r.max_uses,
        "used_count": r.used_count,
        "remaining": max((r.max_uses or 0) - (r.used_count or 0), 0),
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "expired": bool(r.expires_at and r.expires_at < now),
        "is_active": r.is_active,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "used_by": r.used_by,
        "used_at": r.used_at.isoformat() if r.used_at else None,
        # Step 4：企业归属（A-1 只加这一个字段）
        "org_id": r.org_id,
    }


async def _project_names(db: AsyncSession, rows: list[InviteCode]) -> dict[int, str]:
    """一次性取回列表涉及的 project_id → 项目名，避免 N+1。"""
    project_ids = sorted({r.project_id for r in rows if r.project_id})
    if not project_ids:
        return {}
    prows = (await db.execute(
        select(Project.id, Project.name).where(Project.id.in_(project_ids))
    )).all()
    return {pid: name for pid, name in prows}


class CreateInviteCodeRequest(BaseModel):
    """创建邀请码。role 必须为项目级岗位（公司级角色不经邀请码下发）。"""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: str = Field(min_length=1, max_length=32)
    level: str = Field(default="personal", max_length=20)
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    code: str | None = Field(default=None, min_length=4, max_length=20)


@router.get("/invite-codes")
async def list_invite_codes(
    include_inactive: bool = Query(False),
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """邀请码列表（最近 200 条）。

    权限：``user_manage`` —— 目前仅超管持有（公司级 key）。
    ``include_inactive=False`` 时不返回已停用/已用完/已过期的码。
    """
    stmt = select(InviteCode).order_by(InviteCode.id.desc()).limit(200)
    if not include_inactive:
        stmt = stmt.where(InviteCode.is_active.is_(True))
    rows = list((await db.execute(stmt)).scalars().all())
    now = datetime.now()
    # 项目邀请码（project_id 非空）：一次性取回项目名，供列表展示所属项目
    project_names = await _project_names(db, rows)
    return {
        "items": [_invite_row_dict(r, project_names.get(r.project_id), now) for r in rows]
    }


# —— 全局「无项目」邀请码创建端点已移除（2026-10-13 A+B 收口）——
# 邀请码唯一语义 = 项目负责人把成员加入指定项目（POST /auth/projects/{id}/invite-codes）；
# 无项目码无任何消费方（join 返回 400 / 注册已与码解耦），历史公司码已清理。


@router.post("/invite-codes/{code}/revoke")
async def revoke_invite_code(
    code: str,
    manager: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """停用邀请码（软停用，保留历史记录供审计）。

    权限二选一：
      - ``user_manage``（超管，全局码管理）；
      - 该码所属项目的负责人（``member_manage`` + 本项目成员）—— 项目负责人
        自助管理自家项目码时需要，否则发出去的码自己撤不掉。
    """
    invite = (await db.execute(
        select(InviteCode).where(InviteCode.code == code.strip().upper())
    )).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="邀请码不存在")

    # 全局码（project_id 为空的历史公司码）保持原判据（user_manage，仅超管）不变；
    # 项目码追加「项目负责人可自助停用」，否则自己发出的码撤不掉。
    if invite.project_id is None:
        allowed = manager.has_permission("user_manage")
    else:
        allowed = manager.has_permission("user_manage") or await _can_manage_project_invites(
            db, manager, invite.project_id
        )
    if not allowed:
        raise HTTPException(status_code=403, detail="无权停用该邀请码")

    if not invite.is_active:
        return {"detail": "邀请码已处于停用状态", "code": invite.code}
    invite.is_active = False
    await db.commit()
    return {"detail": "邀请码已停用", "code": invite.code}


# ---------------------------------------------------------------------------
# 微信绑定管理（超管）：查看 / 解绑
#
# 存在意义：`wechat_user_bindings` 是「一微信一账号」独占表。一旦某个微信被
# 错误账号（尤其公司级账号，小程序端登录会被 _token_response 403 拦下）占用，
# 用户既登不进、也无法自助换绑 —— 且旧接口 /unbind 要求**本人已登录**，
# 被占用的用户根本拿不到 token，形成无逃生通道的死局。
# 这两个接口给超管提供逃生通道：查看全部绑定 + 强制解绑释放微信。
# 权限：``user_manage``（与邀请码管理同口径，目前仅超管持有）。
# ---------------------------------------------------------------------------


def _mini_unusable_reason(u: "User | None") -> str | None:
    """返回该账号在小程序端不可登录的**具体原因**；可用则返回 None。

    🔴 单一真源：判定顺序与 ``miniprogram_auth._token_response`` 的闸门一致，
    ``mini_unusable`` 布尔值直接由「本函数是否返回 None」推导，避免前后端两处
    各判一次而口径漂移（2026-10-20 那个 bug 的根因）。
    """
    if u is None:
        return "绑定账号已被删除"
    if not u.is_active:
        return "账号已禁用"
    if u.is_super_admin():
        return "超级管理员账号不支持小程序登录"
    status = getattr(u, "audit_status", None)
    if status == AUDIT_PENDING:
        return "注册申请待审批"
    if status == AUDIT_REJECTED:
        reason = getattr(u, "reject_reason", None)
        return f"注册申请已被拒绝：{reason}" if reason else "注册申请已被拒绝"
    return None


@router.get("/wechat-bindings")
async def list_wechat_bindings(
    q: str | None = Query(None, max_length=64),
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """微信绑定列表（按绑定时间倒序，最多 300 条）。

    ``q`` 可按用户名 / 真实姓名 / 手机号模糊筛选。
    出参**只返回 openid 前 12 位**（脱敏），避免后台页面泄露完整微信标识。
    """
    stmt = (
        select(WechatUserBinding, User)
        .join(User, User.id == WechatUserBinding.user_id, isouter=True)
        .order_by(WechatUserBinding.id.desc())
        .limit(300)
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            (User.username.like(like))
            | (User.real_name.like(like))
            | (User.phone.like(like))
        )
    rows = (await db.execute(stmt)).all()
    return {
        "items": [
            {
                "id": b.id,
                "user_id": b.user_id,
                # 脱敏：只给前 12 位，够区分记录但不可用于外部复现
                "openid_masked": (b.openid[:12] + "…") if b.openid else None,
                "has_unionid": bool(b.unionid),
                # 用户确认的微信昵称/头像（微信平台不提供真实 wxid，据此识别绑定者）
                "wx_nickname": b.wx_nickname,
                "wx_avatar_url": b.wx_avatar_url,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "last_login_at": b.last_login_at.isoformat() if b.last_login_at else None,
                "username": u.username if u else None,
                "real_name": u.real_name if u else None,
                "phone": u.phone if u else None,
                "role": u.role if u else None,
                "role_label": ROLE_LABELS.get(u.role, u.role) if u else None,
                "org_role": u.org_role if u else None,
                "is_active": bool(u.is_active) if u else False,
                "audit_status": getattr(u, "audit_status", None) if u else None,
                # 小程序端不可登录 → 前端高亮提示。
                # 2026-10-20 起口径与 miniprogram_auth._token_response 的真实闸门
                # 严格对齐：只有「超管 / 未激活 / 审批待审 / 审批被拒」不可用；
                # 公司级「管理员」＝项目负责人，现场要用小程序，**已放行**。
                # （此前误用 `org_role in ORG_ROLES`，会把管理员一并标成不可用。）
                # 🔴 布尔值与原因共用 _mini_unusable_reason 一次计算，单一真源，
                #    前端只展示不再自行推断，杜绝两处判定再次漂移（本 bug 根因）。
                "mini_unusable": (_mini_reason := _mini_unusable_reason(u)) is not None,
                "mini_unusable_reason": _mini_reason,
            }
            for b, u in rows
        ]
    }


# ============ Step 4：人员准入审批（设计说明 §8.8.4） ============
# 邀请码 = 申请凭证，审批 = 授权动作。批准时在**一个事务**里完成五件事，
# 把「开户」与「项目归属」合并为一次动作（§8.8.9：不做这一步，就会留下
# 一台无审计的自动开户机 + 注册后仍不进任何项目的双坑）。


class ApproveRegistrationRequest(BaseModel):
    """批准入册。``project_id`` 可空（只激活不落项目）。

    ``roles`` = 岗位集合：第 1 个为主岗，其余为附加岗（排他岗位不得作附加岗）。
    ``org_role`` = 公司级角色，**仅超管可给**（复用 ``_validate_org_role``）。
    """
    project_id: int | None = None
    roles: list[str] = Field(default_factory=list)
    org_role: str | None = None
    remark: str | None = Field(default=None, max_length=256)


class RejectRegistrationRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


def _registration_brief(u: User) -> dict:
    """待审批列表行。"""
    return {
        "user_id": u.id,
        "username": u.username,
        "real_name": u.real_name,
        "phone": u.phone,
        "company_name": u.company_name,
        "invite_code": u.invite_code,
        # 码上的岗位 = 申请意向（审批弹窗预填用）
        "apply_role": u.role,
        "apply_role_label": ROLE_LABELS.get(u.role, u.role),
        "org_id": u.org_id,
        "audit_status": u.audit_status,
        "reject_reason": u.reject_reason,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/registrations")
async def list_registrations(
    status_filter: str = Query("pending", alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    manager: User = Depends(require_permission("user_approve")),
    db: AsyncSession = Depends(get_db),
):
    """待审批（或已拒绝）注册申请列表。

    权限：``user_approve``（设计与 ``user_manage`` 分离 —— 审批人可以只看申请
    而不能建号/改密码）。多企业后只返回**本企业**申请；当前 ``org_id`` 全为 1，
    故等价于全部。
    """
    if status_filter not in (AUDIT_PENDING, AUDIT_APPROVED, AUDIT_REJECTED):
        raise HTTPException(status_code=400, detail="status 无效")

    stmt = select(User).where(User.audit_status == status_filter)
    # 企业隔离：管理员只能看本企业申请（org_id 为空视为本企业历史数据）
    if manager.org_id is not None:
        stmt = stmt.where(
            (User.org_id == manager.org_id) | (User.org_id.is_(None))
        )
    total = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar() or 0
    rows = (await db.execute(
        stmt.order_by(User.id.asc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"total": total, "items": [_registration_brief(u) for u in rows]}


@router.post("/registrations/{user_id}/approve")
async def approve_registration(
    user_id: int,
    req: ApproveRegistrationRequest,
    manager: User = Depends(require_permission("user_approve")),
    db: AsyncSession = Depends(get_db),
):
    """批准入册 —— 一个事务完成五件事（§8.8.4）：

    1. ``audit_status='approved'`` + ``approved_by`` + ``approved_at``；
    2. ``users.org_id`` 补齐；
    3. ``users.role`` = 岗位集合主岗（兜底展示口径）；
    4. 指定了项目 → **Upsert ``project_members``**（归属落定，顺带修 §2.5 坑 1）；
    5. 写 ``security_audit``。

    约束：**不得批自己**；排他岗位（总监/总监代表）每项目 1 名；
    ``org_role`` 仅超管可给（走 ``_validate_org_role`` 的双保险与防锁死规则）。
    """
    # 函数内延迟导入：project.py 依赖 auth.py，模块级导入会形成环。
    from app.routers.project import _assert_exclusive_ok, _validate_member_roles

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == manager.id:
        raise HTTPException(status_code=403, detail="不能审批自己的注册申请")
    if target.audit_status == AUDIT_APPROVED:
        raise HTTPException(status_code=409, detail="该申请已批准，无需重复操作")
    # 企业隔离：管理员只能批本企业申请
    if (
        manager.org_id is not None
        and target.org_id is not None
        and target.org_id != manager.org_id
    ):
        raise HTTPException(status_code=403, detail="只能审批本企业的注册申请")

    # 岗位集合：roles[0] = 主岗，其余 = 附加岗
    roles = [r for r in (req.roles or []) if r]
    main_role = roles[0] if roles else (target.role or UserRole.INSPECTOR.value)
    main_role, extra_roles = _validate_member_roles(main_role, roles[1:] if len(roles) > 1 else [])

    # 公司级角色（仅超管）—— 复用既有校验：None=不改 / ""=清除 / 有效值=授予
    new_org_role = _validate_org_role(req.org_role, manager)

    # 项目归属校验 + 排他岗位基数校验
    if req.project_id is not None:
        project = await db.get(Project, req.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="目标项目不存在")
        await _assert_exclusive_ok(db, req.project_id, main_role, exclude_user_id=target.id)

    # ---- 五件事 ----
    # ① 审批状态
    target.audit_status = AUDIT_APPROVED
    target.approved_by = manager.id
    target.approved_at = datetime.now()
    target.reject_reason = None
    # ② 企业归属补齐（注册时没码或老数据）
    if target.org_id is None and manager.org_id is not None:
        target.org_id = manager.org_id
    # ③ 主岗兜底
    target.role = main_role
    # ③′ 公司级角色（仅超管可给；_validate_org_role 内已做 403 双保险）
    if new_org_role is not None:
        target.org_role = new_org_role or None
    # ④ 项目归属落定
    if req.project_id is not None:
        existing_member = (await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == req.project_id,
                ProjectMember.user_id == target.id,
            )
        )).scalar_one_or_none()
        if existing_member is None:
            db.add(ProjectMember(
                project_id=req.project_id,
                user_id=target.id,
                role=main_role,
                extra_roles=extra_roles or None,
            ))
        else:
            existing_member.role = main_role
            existing_member.extra_roles = extra_roles or None
            existing_member.left_at = None
        # 首个项目即设为该用户的上下文项目，便于登录后直接落到工作台
        if target.project_id is None:
            target.project_id = req.project_id
    # ⑤ 审计留痕
    db.add(SecurityAudit(
        user_id=manager.id,
        username=manager.username,
        action="user_approve",
        detail=(
            f"批准注册 user_id={target.id} username={target.username}"
            f" 主岗={main_role} 附加岗={extra_roles or []} 项目={req.project_id}"
            f"{' 公司级=' + new_org_role if new_org_role else ''}"
            f"{' 备注=' + req.remark if req.remark else ''}"
        ),
    ))
    await db.commit()
    return {
        "detail": "已批准，该账号现在可以登录",
        "user_id": target.id,
        "audit_status": target.audit_status,
        "role": target.role,
        "project_id": target.project_id,
        "extra_roles": extra_roles,
    }


@router.post("/registrations/{user_id}/reject")
async def reject_registration(
    user_id: int,
    req: RejectRegistrationRequest,
    manager: User = Depends(require_permission("user_approve")),
    db: AsyncSession = Depends(get_db),
):
    """拒绝注册申请（附理由）。

    ``rejected`` 后**账号保留**（用户名不释放，避免被抢注）；
    管理员可随时改判 ``approved``（调 approve 接口），不必让用户重新注册。
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == manager.id:
        raise HTTPException(status_code=403, detail="不能审批自己的注册申请")
    if (
        manager.org_id is not None
        and target.org_id is not None
        and target.org_id != manager.org_id
    ):
        raise HTTPException(status_code=403, detail="只能审批本企业的注册申请")

    target.audit_status = AUDIT_REJECTED
    target.reject_reason = (req.reason or "").strip() or None
    target.approved_by = manager.id
    target.approved_at = datetime.now()
    db.add(SecurityAudit(
        user_id=manager.id,
        username=manager.username,
        action="user_reject",
        detail=(
            f"拒绝注册 user_id={target.id} username={target.username}"
            f" 理由={target.reject_reason or '（未填写）'}"
        ),
    ))
    await db.commit()
    return {
        "detail": "已拒绝该注册申请",
        "user_id": target.id,
        "audit_status": target.audit_status,
        "reject_reason": target.reject_reason,
    }


@router.get("/roles", response_model=RoleInfoResponse)
async def get_available_roles(client: str = Query("web")):
    """可选岗位列表。

    Step 1 起：``value`` 为**英文 key**（提交/存储口径），``label`` 为中文
    （展示口径）。各端提交角色时必须用 ``value``。

    Step 2（设计说明 §7.3）按端过滤：
      - ``client=web``（默认）返回**全部**，含公司级 ``super_admin``/``admin``
        （Web 管理后台是唯一的公司级角色分配入口）；
      - ``client=miniprogram`` 只返回**项目级**岗位 —— 小程序端不接受公司级
        角色（公司级的落地场景是管理后台，且注册链路只发项目级邀请码）。
    """
    if client == "miniprogram":
        source: list[tuple[str, str]] = [
            (member.value, member.value) for member in UserRole
        ]
    else:
        source = [(member.value, member.value) for member in UserRole]
        source += [(code, code) for code in ORG_ROLES]
    # Step 3：权限改从**可配置矩阵**取（DB 覆盖 / 代码兜底），
    # 不再直接读代码常量 —— 否则界面改完权限，登录页仍按老口径显示。
    from app.services.permissions import get_matrix

    matrix = get_matrix()
    roles = [
        {
            "value": value,
            "label": ROLE_LABELS.get(value, value),
            "permissions": matrix.get(value, []),
        }
        for value, _ in source
    ]
    return RoleInfoResponse(roles=roles)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    # 可选角色 = 项目级岗位 ∪ 公司级角色（Web 管理后台是公司级角色的唯一入口，
    # 与 GET /auth/roles?client=web 的输出保持一致；小程序端走
    # miniprogram_auth._token_response 拦截公司级，不经此处）。
    valid_roles = [r.value for r in UserRole] + sorted(ORG_ROLES)
    if req.role is not None and req.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"无效角色，可选: {', '.join(valid_roles)}")

    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="请提供用户名和密码")

    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()

    if not user or not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="该账号已被禁用")

    # Step 4（设计说明 §8.8.5「最容易漏」）：审批状态拦截。
    # 与 is_active 分工 —— audit_status 管「进门」，is_active 管「停用」。
    # 只改注册不改登录 = 没做：注册不发 token，但用户重登一次就绕过去了。
    if user.audit_status == AUDIT_PENDING:
        raise HTTPException(
            status_code=403,
            detail="账号待管理员审批，审核通过后即可登录（请联系发放邀请码的管理员）",
        )
    if user.audit_status == AUDIT_REJECTED:
        _reason = f"：{user.reject_reason}" if user.reject_reason else ""
        raise HTTPException(
            status_code=403,
            detail=f"注册申请已被拒绝{_reason}，如有疑问请联系管理员",
        )

    # 角色匹配（Step 2 两级角色）：
    #   ⓪ req.role 省略 → **自动判定**：公司级账号用其 org_role，项目级用主岗。
    #      这是 Web 登录页「不选角色」模式的支撑点 —— 普通用户（尤其小程序
    #      注册的账号）不需知道自己的岗位 key 即可登录，消除因选错角色而
    #      「登不上」的体验障碍（错误提示为 403，用户无法自助解决）。
    #   ① 请求公司级角色 → 必须 org_role 一致（超管/管理员的身份入口）；
    #   ② 请求项目级岗位 → 超管放行（便于超管直接以任一岗位视角进入后台），
    #      其余持有公司级角色的账号**不允许**以项目岗位身份登录 ——
    #      否则会出现「role 显示为监理员、permissions 却是公司级全权」的
    #      角色/权限不一致，前端按 role 渲染菜单就会错位。
    #   ③ 纯项目级账号 → 按 users.role（主岗）比对。
    if req.role is None:
        # 自动判定：不做 403，直接采用账号真实身份（安全性等价 —— 身份来自
        # 数据库而非请求参数；此处只决定 token.role 的展示口径）。
        pass
    elif req.role in ORG_ROLES:
        if user.org_role != req.role:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"该账号的角色为「{ROLE_LABELS.get(user.role, user.role)}」，"
                    f"无法以「{ROLE_LABELS.get(req.role, req.role)}」身份登录"
                ),
            )
    elif user.is_super_admin():
        pass  # 超管可代任意项目岗位登录
    elif user.is_org_level():
        raise HTTPException(
            status_code=403,
            detail=(
                f"该账号是公司级「{ROLE_LABELS.get(user.org_role, user.org_role)}」，"
                f"请以公司级身份登录（无法以「{ROLE_LABELS.get(req.role, req.role)}」身份登录）"
            ),
        )
    elif user.role != req.role:
        raise HTTPException(
            status_code=403,
            detail=f"该账号的角色为「{ROLE_LABELS.get(user.role, user.role)}」，无法以「{ROLE_LABELS.get(req.role, req.role)}」身份登录",
        )

    return await _issue_token(user, db, req.role)


@router.post("/logout")
async def logout(user: User = Depends(get_current_user)):
    return {"detail": "已退出登录", "user_id": user.id}


# ============ 邀请码语义升级：项目负责人加项目成员 ============
@router.post("/projects/{project_id}/invite-codes")
async def create_project_invite_code(
    project_id: int,
    req: CreateInviteCodeRequest,
    manager: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """项目负责人为「本项目」生成邀请码（加成员用）。

    权限：持 ``member_manage`` 且为该项目有效成员（项目负责人口径）。
    生成码带 project_id + role，持有人在 ``/invite-codes/{code}/join`` 加入即获该岗位。
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not await _can_manage_project_invites(db, manager, project_id):
        raise HTTPException(status_code=403, detail="仅本项目负责人（或公司级管理员）可发项目邀请码")

    valid_project_roles = {m.value for m in UserRole}
    if req.role in ORG_ROLES:
        raise HTTPException(status_code=400, detail="邀请码不能发放公司级角色")
    if req.role not in valid_project_roles:
        raise HTTPException(status_code=400, detail=f"岗位无效，可选: {', '.join(sorted(valid_project_roles))}")
    if req.level not in INVITE_LEVELS:
        raise HTTPException(status_code=400, detail=f"级别无效，可选: {', '.join(INVITE_LEVELS)}")

    if req.code:
        code = req.code.strip().upper()
        if (await db.execute(select(InviteCode).where(InviteCode.code == code))).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="邀请码已存在")
    else:
        for _ in range(10):
            code = _gen_invite_code()
            if not (await db.execute(select(InviteCode).where(InviteCode.code == code))).scalar_one_or_none():
                break
        else:
            raise HTTPException(status_code=500, detail="邀请码生成冲突，请重试")

    invite = InviteCode(
        code=code,
        level=req.level,
        role=req.role,
        created_by=manager.username,
        max_uses=req.max_uses,
        used_count=0,
        is_active=True,
        org_id=project.org_id,
        project_id=project_id,
        expires_at=(datetime.now() + timedelta(days=req.expires_in_days)) if req.expires_in_days else None,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return {
        "id": invite.id,
        "code": invite.code,
        "level": invite.level,
        "role": invite.role,
        "role_label": ROLE_LABELS.get(invite.role, invite.role),
        "project_id": project_id,
        "max_uses": invite.max_uses,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
    }


@router.get("/projects/{project_id}/invite-codes")
async def list_project_invite_codes(
    project_id: int,
    include_inactive: bool = Query(False),
    manager: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """本项目邀请码列表（最近 200 条）。

    权限：持 ``member_manage`` 且为该项目有效成员 —— 与发码口径一致，
    项目负责人可自助查看/管理自家项目的码，无需超管介入。
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not await _can_manage_project_invites(db, manager, project_id):
        raise HTTPException(status_code=403, detail="仅本项目负责人（或公司级管理员）可查看项目邀请码")

    stmt = (
        select(InviteCode)
        .where(InviteCode.project_id == project_id)
        .order_by(InviteCode.id.desc())
        .limit(200)
    )
    if not include_inactive:
        stmt = stmt.where(InviteCode.is_active.is_(True))
    rows = list((await db.execute(stmt)).scalars().all())
    now = datetime.now()
    return {
        "project_id": project_id,
        "project_name": project.name,
        "items": [_invite_row_dict(r, project.name, now) for r in rows],
    }


@router.post("/invite-codes/{code}/join")
async def join_project_by_invite(
    code: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """已登录用户用项目邀请码加入项目，并获对应岗位。

    🔴 2026-10-21 起有两条入项目路径，共用同一个 ``validate_invite_code`` 与
    同一套 used_count 递增规则：
      ① 注册时填码（``/miniprogram/auth/wechat-register``）→ 建号即入项目；
      ② 已登录用户走本接口 → 适用于「注册时填的是企业码（无 project_id）」
         或「事后被拉进第二个项目」的场景。
    """
    result = await validate_invite_code(code, db)
    if not result["valid"]:
        raise HTTPException(status_code=400, detail=result["message"])
    project_id = result.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="该邀请码不是项目邀请码")
    role = result.get("role") or "inspector"

    # 排他岗位基数校验（总监每项目 1 名）
    from app.routers.project import _assert_exclusive_ok

    await _assert_exclusive_ok(db, project_id, role, exclude_user_id=user.id)

    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="目标项目不存在")

    existing = (await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )).scalar_one_or_none()
    if existing is None:
        db.add(ProjectMember(project_id=project_id, user_id=user.id, role=role))
    else:
        existing.role = role
        existing.left_at = None

    if user.project_id is None:
        user.project_id = project_id
    user.role = role  # 主岗占位更新为项目岗位

    # 递增邀请码使用
    invite_id = result.get("invite_id")
    if invite_id:
        inv = await db.get(InviteCode, invite_id)
        if inv:
            inv.used_count = (inv.used_count or 0) + 1
            inv.used_by = user.id
            inv.used_at = datetime.now()
            if inv.max_uses and inv.used_count >= inv.max_uses:
                inv.is_active = False
    await db.commit()
    return {
        "detail": "已加入项目",
        "project_id": project_id,
        "project_name": project.name,
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
    }


@router.get("/me")
async def get_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # get_current_user 已合成挂到 effective_permissions，这里直接取
    permissions = user.permission_set()
    project = await db.get(Project, user.project_id) if user.project_id else None
    from app.services.permissions import current_perm_version

    return {
        "user_id": user.id,
        "username": user.username,
        "real_name": user.real_name,
        "role": user.role,
        "phone": user.phone,
        "id_card": user.id_card,
        "certificate_type": user.certificate_type,
        "certificate_no": user.certificate_no,
        "gender": user.gender,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "remark": user.remark,
        "project_id": user.project_id,
        "project_name": project.name if project else None,
        "is_active": user.is_active,
        "permissions": permissions,
        # Step 3：权限配置版本号 —— 前端拿它比对本地快照，
        # 不一致就重新拉取，从而在登录态下即时跟随权限变更（无需重登）。
        "perm_version": await current_perm_version(db),
        "org_role": user.org_role,
        "company_name": user.company_name,
        "invite_code": user.invite_code,
        # 强制改密标记 —— 各端据此把用户锁在改密页直到他改完
        "must_change_password": bool(user.must_change_password),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.put("/me/profile")
async def update_profile(
    data: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    allowed_fields = {"real_name", "phone", "gender", "email", "remark"}
    for field, value in data.items():
        if field in allowed_fields and value is not None:
            setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return {"detail": "资料更新成功"}


@router.put("/me/password")
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not pwd_context.verify(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if pwd_context.verify(req.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    user.password_hash = hash_password(req.new_password)
    # 用户已自行改密 → 解除「强制改密」锁定（管理员重置后置的 True 在此清除）
    user.must_change_password = False
    # 同时清空一次性临时口令明文（若存在），使后台列表不再展示旧口令
    user.temp_password = None
    _audit(db, "password_change", user=user, detail="用户自助修改密码")
    await db.commit()
    return {"detail": "密码修改成功，请妥善保管新密码"}


# ============ 密保问题（认证态） ============
@router.get("/me/security-questions")
async def get_security_questions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查看本账号已设置的密保题目（**只回题目，绝不回答案**）。"""
    rows = (
        await db.execute(
            select(UserSecurityQuestion)
            .where(UserSecurityQuestion.user_id == user.id)
            .order_by(UserSecurityQuestion.sort_order)
        )
    ).scalars().all()
    return {
        "is_set": bool(rows),
        "questions": [row.question for row in rows],
        "max_questions": MAX_SECURITY_QUESTIONS,
    }


@router.put("/me/security-questions")
async def set_security_questions(
    req: SetSecurityQuestionsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """设置 / 更新密保问题（全量替换）。

    要求提供**当前密码**：密保问题是「忘记密码」的唯一自助凭据，一旦被
    他人（例如 token 外泄）静默改写，等于把重置通道拱手让人。
    ``questions`` 传空数组 = 清除已设问题。
    """
    if not pwd_context.verify(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(req.questions) > MAX_SECURITY_QUESTIONS:
        raise HTTPException(
            status_code=400, detail=f"最多设置 {MAX_SECURITY_QUESTIONS} 个密保问题"
        )
    seen: set[str] = set()
    for item in req.questions:
        q = item.question.strip()
        if q in seen:
            raise HTTPException(status_code=400, detail="密保问题不能重复")
        seen.add(q)

    await db.execute(
        delete(UserSecurityQuestion).where(UserSecurityQuestion.user_id == user.id)
    )
    for idx, item in enumerate(req.questions):
        db.add(UserSecurityQuestion(
            user_id=user.id,
            sort_order=idx,
            question=item.question.strip(),
            answer_hash=_hash_answer(item.answer),
        ))
    _audit(
        db, "security_questions_set", user=user,
        detail=f"设置密保问题 {len(req.questions)} 个",
    )
    await db.commit()
    return {"detail": "密保问题已保存", "count": len(req.questions)}


# ============ 忘记密码：自助重置（公开，无需登录） ============
@router.post("/forgot/questions")
async def forgot_questions(
    req: ForgotQuestionsRequest,
    db: AsyncSession = Depends(get_db),
):
    """按用户名取回密保题目（供登录页「忘记密码」引导作答）。

    ⚠️ 抗枚举：账号不存在 / 已停用 / 未设密保 —— 三种情况返回**同一响应**，
    调用方无法据此判断用户名是否存在。
    """
    result = await db.execute(select(User).where(User.username == req.username.strip()))
    user = result.scalar_one_or_none()
    questions: list[str] = []
    if user is not None and user.is_active and user.audit_status == AUDIT_APPROVED:
        rows = (
            await db.execute(
                select(UserSecurityQuestion)
                .where(UserSecurityQuestion.user_id == user.id)
                .order_by(UserSecurityQuestion.sort_order)
            )
        ).scalars().all()
        questions = [row.question for row in rows]
    if not questions:
        return {
            "available": False,
            "questions": [],
            "message": "该账号未设置密保问题，请联系管理员重置密码",
        }
    return {"available": True, "questions": questions}


@router.post("/forgot/verify")
async def forgot_verify(
    req: ForgotVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """校验密保答案 → 发放一次性重置凭证。

    全部答对才通过；失败计数用于抬高暴力破解成本（见 _check_forgot_throttle）。
    """
    _check_forgot_throttle(req.username)
    result = await db.execute(select(User).where(User.username == req.username.strip()))
    user = result.scalar_one_or_none()

    ok = False
    if (
        user is not None
        and user.is_active
        and user.audit_status == AUDIT_APPROVED
    ):
        rows = (
            await db.execute(
                select(UserSecurityQuestion)
                .where(UserSecurityQuestion.user_id == user.id)
                .order_by(UserSecurityQuestion.sort_order)
            )
        ).scalars().all()
        if rows and len(req.answers) == len(rows):
            ok = all(
                _verify_answer(ans, row.answer_hash)
                for ans, row in zip(req.answers, rows)
            )

    if not ok:
        _record_forgot_failure(req.username)
        _audit(
            db, "password_reset_verify_failed",
            username=req.username.strip(),
            detail="密保答案校验失败",
            ip=_request_ip(request),
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="密保答案不正确")

    token = _create_reset_token(user)
    _audit(
        db, "password_reset_verify_ok", user=user,
        detail="密保问题校验通过，已发放重置凭证",
        ip=_request_ip(request),
    )
    await db.commit()
    return {
        "reset_token": token,
        "expires_in": RESET_TOKEN_TTL_MINUTES * 60,
        "username": user.username,
    }


@router.post("/forgot/reset")
async def forgot_reset(
    req: ForgotResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """凭重置凭证设置新密码（凭证一次性：改密后指纹变化即失效）。"""
    payload = _decode_reset_token(req.reset_token)
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="重置凭证无效，请重新验证密保问题")
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="账号不可用，请联系管理员")
    if payload.get("fp") != _password_fingerprint(user.password_hash):
        raise HTTPException(
            status_code=400, detail="重置凭证已失效，请重新验证密保问题"
        )
    if pwd_context.verify(req.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

    user.password_hash = hash_password(req.new_password)
    user.must_change_password = False
    # 清空一次性临时口令明文（若存在），与 change_password 同一口径
    user.temp_password = None
    _audit(
        db, "password_reset_self", user=user,
        detail="通过密保问题自助重置密码",
        ip=_request_ip(request),
    )
    await db.commit()
    return {"detail": "密码已重置，请用新密码登录", "username": user.username}


# ============ 忘记密码：管理员兜底（一次性临时口令 + 强制改密） ============
@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: int,
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """管理员把账号重置为**统一默认口令 admin123**（不再随机、不再强制改密）。

    - 权限：``user_manage``（公司级，默认仅超管）；
    - 口令统一为 **admin123**，写入 ``temp_password`` 使后台列表「密码」列可直接展示；
    - 不置 ``must_change_password`` —— 用户保留该默认口令即可登录，之后可随时自行修改。
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if not _can_manage_all_projects(manager) and not await _is_project_member(
        db, manager, target.project_id
    ):
        raise HTTPException(status_code=404, detail="账号不存在")
    if target.is_super_admin() and not manager.is_super_admin():
        raise HTTPException(status_code=403, detail="无权重置超级管理员的密码")

    default_password = "admin123"
    target.password_hash = hash_password(default_password)
    target.must_change_password = False
    # 暂存明文，使后台列表「密码」列可直接展示默认口令（用户改密后立即清空）。
    target.temp_password = default_password
    _audit(
        db, "password_reset_by_admin", user=manager,
        detail=f"将账号 {target.username}(id={target.id}) 重置为默认口令",
    )
    await db.commit()
    return {
        "detail": "已重置为统一默认密码 admin123，请转交用户；其可随时自行修改密码",
        "username": target.username,
        "user_id": target.id,
        "temp_password": default_password,
        "must_change_password": False,
    }


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """管理员删除指定账号（user_manage 权限）。

    - 不能删除当前登录账号（避免把自己锁死）；
    - 非超管不能删除超级管理员账号；
    - 删除前清理所有指向该用户的子表引用（归属类整行删、业务类仅置空外键），
      随后硬删除用户行。
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if not _can_manage_all_projects(manager) and not await _is_project_member(
        db, manager, target.project_id
    ):
        raise HTTPException(status_code=404, detail="账号不存在")
    if target.id == manager.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的账号")
    if target.is_super_admin() and not manager.is_super_admin():
        raise HTTPException(status_code=403, detail="无权重限删除超级管理员账号")

    await _purge_user_references(db, user_id)
    await db.delete(target)
    _audit(db, "user_delete", user=manager, detail=f"删除账号 {target.username}(id={target.id})")
    await db.commit()
    return {"detail": "账号已删除"}


# 这些表以「用户」为归属主体，用户删除时应整行清理（而非仅置空外键）。
_OWNERSHIP_TABLES = {
    "project_members",
    "wechat_user_bindings",
    "agent_drafts",
    "user_security_questions",
    "wechat_subscriptions",
    "rtc_call_sessions",
    "rtc_call_participants",
    "rtc_call_audit",
    "signatures",
    "knowledge_base_members",
}


async def _purge_user_references(db: AsyncSession, user_id: int) -> None:
    """删除用户前，解除/清理所有指向 ``users.id`` 的外键引用。

    归属类子表（见 ``_OWNERSHIP_TABLES``）整行删除；其余业务表的 user 外键
    若可空则置 NULL（保留业务数据，仅解除归属），不可空则整行删除。
    """
    for table in Base.metadata.tables.values():
        if table.name == "users":
            continue
        for col in table.columns:
            if not any(fk.column.table.name == "users" for fk in col.foreign_keys):
                continue
            if table.name in _OWNERSHIP_TABLES:
                await db.execute(delete(table).where(col == user_id))
            elif col.nullable:
                await db.execute(update(table).where(col == user_id).values({col: None}))
            else:
                await db.execute(delete(table).where(col == user_id))


def _can_manage_all_projects(user: User) -> bool:
    """跨项目操作总开关（9 个调用点依赖）。

    Step 1 起判据为公司级角色，取代原先的 ``username == "admin"``。
    当前生产数据 ``org_role`` 全为 NULL，故与改前行为**逐位相同**；
    公司级「管理员」的实际可用能力仍受各接口自身的
    ``require_permission(...)`` 限制（Step 2 才会下发 ``member_manage``）。
    """
    return user.is_super_admin() or user.is_org_admin()


def _can_view_all_projects(user: User) -> bool:
    """项目**可见性**总开关（项目列表 / 项目切换 / 项目编辑范围）。

    与 :func:`_can_manage_all_projects`（公司级管理动作）刻意分离：
    2026-10-20 起「管理员」定位为项目负责人——默认**只能查看自己的项目**
    （成员项目 ∪ 当前绑定项目）；要看其他项目负责人的项目，由超管将其
    加为该项目成员（成员关系即授权，成员管理＝公司级全部项目）。
    超管仍全量可见。
    """
    return user.is_super_admin()


async def _is_project_member(db: AsyncSession, user: User, project_id: int | None) -> bool:
    """该用户是否为指定项目的**有效成员**（Design §7.7②）。

    归属真源是 ``project_members``，不是 ``users.project_id``（后者只是上下文）。
    有效成员 = ``left_at IS NULL``。
    """
    if project_id is None:
        return False
    row = (
        await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
                ProjectMember.left_at.is_(None),
            )
        )
    ).first()
    return row is not None


async def _can_manage_project_invites(
    db: AsyncSession, user: User, project_id: int | None
) -> bool:
    """能否管理「指定项目」的邀请码（发码 / 列表 / 停用三处共用）。

    判据二选一：
      - 公司级（超管 / 组织管理员）—— 跨项目，``_can_manage_all_projects``；
      - 项目负责人 —— 持 ``member_manage`` **且**是该项目有效成员。

    之所以不能用「仅 ``_is_project_member``」：公司级账号未必是每个项目的成员，
    否则超管反而进不了项目级接口（自锁）。
    """
    if _can_manage_all_projects(user):
        return True
    return user.has_permission("member_manage") and await _is_project_member(db, user, project_id)


async def _validate_managed_project(
    manager: User,
    project_id: int | None,
    db: AsyncSession,
) -> int:
    """确定本次账号管理的目标项目，并做管辖校验（Step 2 改判据）。

    公司级（`_can_manage_all_projects`）可指定任意项目；
    其余人只能管**自己当前所在**的项目，且该权限以**成员归属**为准
    （设计说明 §7.7②：管辖范围 = "我是成员的项目集合"，不是单值上下文）。
    """
    if _can_manage_all_projects(manager):
        effective_id = project_id if project_id is not None else manager.project_id
    else:
        effective_id = manager.project_id
    if effective_id is None:
        raise HTTPException(status_code=400, detail="请先选择项目")
    project = await db.get(Project, effective_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not _can_manage_all_projects(manager):
        if effective_id != manager.project_id:
            raise HTTPException(status_code=403, detail="只能管理当前项目账号")
        # 归属校验：必须是该项目的有效成员
        if not await _is_project_member(db, manager, effective_id):
            raise HTTPException(status_code=403, detail="只能管理当前项目账号")
    return effective_id


def _validate_org_role(raw: str | None, manager: User) -> str | None:
    """校验并归一化公司级角色入参。

    - ``None`` → ``None``（调用方自行决定是"不修改"还是"不设置"）
    - ``""`` / ``"none"`` / ``"null"`` → ``None``（清除）
    - 其余必须在 ``ORG_ROLES`` 内

    **只有超管能给他人授公司级角色**。管理员（``org_role='admin'``）虽然持
    ``user_manage``? —— 不，它不持有（`ORG_ROLE_ADMIN` 的权限只有
    ``user_approve`` + ``member_manage``）。因此本函数的超管校验是**双保险**：
    即使将来有人误把 ``user_manage`` 加给管理员，也不能借此提权。
    """
    if raw is None:
        return None
    value = raw.strip()
    if value == "" or value.lower() in {"none", "null", "0"}:
        return None
    if not manager.is_super_admin():
        raise HTTPException(status_code=403, detail="只有超级管理员可以授予公司级角色")
    if value not in ORG_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"公司级角色无效，可选: {', '.join(sorted(ORG_ROLES))}",
        )
    return value


@router.get("/users")
async def list_users(
    user: User = Depends(require_permission("settings")),
    db: AsyncSession = Depends(get_db),
):
    """账号列表。Step 2：非公司级的可见范围改按**成员归属**判定。

    设计说明 §7.4 第 9 条：范围 = "我是成员的项目的成员"，
    而不是 ``User.project_id == 我的 project_id``（后者会把
    「已转场但上下文还挂在该项目」的人算进来、也会漏掉跨项目成员）。

    **权限 key 保留 ``settings`` 而非 ``user_manage``**（2026-10-10 定）：
    本接口是**只读查看**，总监在本项目内查看成员属正常业务需求（工作台/台账
    页要显示同事），且可见范围已被上方的成员归属子查询限制在本项目。
    写操作（建号/改信息/解绑）才需要 ``user_manage``，见 create_user。
    """
    query = select(User).order_by(User.created_at.desc())
    if not _can_manage_all_projects(user):
        # 与当前上下文项目同为有效成员的人（含跨项目兼职者）
        member_ids = select(ProjectMember.user_id).where(
            ProjectMember.project_id == user.project_id,
            ProjectMember.left_at.is_(None),
        )
        query = query.where(User.id.in_(member_ids))
    result = await db.execute(query)
    users = result.scalars().all()

    from app.models.miniprogram import WechatUserBinding
    binding_result = await db.execute(
        select(WechatUserBinding.user_id).where(
            WechatUserBinding.user_id.in_([item.id for item in users] or [-1])
        )
    )
    bound_ids = set(binding_result.scalars().all())
    return {
        "items": [
            {
                "id": item.id,
                "username": item.username,
                "real_name": item.real_name,
                "role": item.role,
                "org_role": item.org_role,
                "phone": item.phone,
                "project_id": item.project_id,
                "is_active": item.is_active,
                # Step 4：列表必须暴露审批状态 —— 否则 pending/rejected 账号
                # 会以「启用」面目出现（is_active 与 audit_status 分工不同），
                # 管理员看到「启用」却登不进，极易误判为系统故障。
                "audit_status": item.audit_status,
                "reject_reason": item.reject_reason,
                "wechat_bound": item.id in bound_ids,
                "company_name": item.company_name,
                "invite_code": item.invite_code,
                # 2026-10-19：暴露「强制改密」标记与一次性临时口令明文，
                # 供后台用户管理列表「密码」列展示（仅临时口令，真实密码不可见）。
                "must_change_password": bool(item.must_change_password),
                "temp_password": item.temp_password,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in users
        ]
    }


@router.post("/users")
async def create_user(
    req: CreateUserRequest,
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """管理员建号（Step 2：权限 key 由 ``settings`` 改为 ``user_manage``）。

    **为什么必须改**：``settings`` 是总监也持有的业务 key，而本接口能指定
    任意项目角色 —— 留在 ``settings`` 上等于"总监可在本项目内建号并设任意岗位"，
    属真实越权。设计说明 §7.2 明确「修改人员信息」＝ ``user_manage``（仅超管）。
    """
    # 保留用户名校验：与超管判据无关（`admin` 只是禁止被抢注的保留名）
    if req.username.strip().lower() == "admin":
        raise HTTPException(status_code=400, detail="admin 为保留账号")
    if req.role not in {role.value for role in UserRole}:
        raise HTTPException(status_code=400, detail="用户角色无效")
    # 公司级角色：可空；设了则建号即为公司级（仅超管可授，见 _validate_org_role）
    org_role = _validate_org_role(req.org_role, manager)
    existing = await db.execute(select(User).where(User.username == req.username.strip()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已存在")
    project_id = await _validate_managed_project(manager, req.project_id, db)
    created = User(
        username=req.username.strip(),
        password_hash=hash_password(req.password),
        real_name=req.real_name.strip(),
        role=req.role,
        org_role=org_role,
        phone=req.phone,
        project_id=project_id,
        is_active=True,
    )
    db.add(created)
    await db.flush()  # 取 created.id 供成员关系外键使用
    # 管理员建号即授权：同步写入项目成员关系（权限边界的源头）
    db.add(ProjectMember(project_id=project_id, user_id=created.id, role=created.role))
    await db.commit()
    await db.refresh(created)
    return {"detail": "账号已创建", "user_id": created.id}


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    req: UpdateUserRequest,
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """修改人员信息（Step 2：权限 key 由 ``settings`` 改为 ``user_manage``，见 create_user 说明）。"""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    # Step 2（设计说明 §7.4 第 9 条）：管辖判据从「上下文 project_id 相等」
    # 改为「是否同为该项目成员」——单人可跨项目，上下文只表示会话所在项目。
    if not _can_manage_all_projects(manager) and not await _is_project_member(
        db, manager, target.project_id
    ):
        raise HTTPException(status_code=404, detail="账号不存在")
    if target.is_super_admin() and not manager.is_super_admin():
        raise HTTPException(status_code=403, detail="无权修改超级管理员")
    if req.is_active is False and target.id == manager.id:
        raise HTTPException(status_code=400, detail="不能停用当前登录账号")
    # 公司级角色变更（显式区分"未传"与"清空"，见 UpdateUserRequest 注释）
    if req.org_role is not None:
        new_org_role = _validate_org_role(req.org_role, manager)
        if new_org_role != target.org_role:
            # 防锁死：超管不能自我降级，否则系统将没有任何超管可管理
            if target.id == manager.id and target.is_super_admin() and new_org_role != ORG_ROLE_SUPER_ADMIN:
                raise HTTPException(
                    status_code=400,
                    detail="不能降低当前登录超管自身的公司级角色（请由另一位超管操作）",
                )
            # 防锁死：也不允许把**最后一个**超管降级
            if target.is_super_admin() and new_org_role != ORG_ROLE_SUPER_ADMIN:
                remaining = (
                    await db.execute(
                        select(func.count(User.id)).where(
                            User.org_role == ORG_ROLE_SUPER_ADMIN,
                            User.id != target.id,
                            User.is_active.is_(True),
                        )
                    )
                ).scalar_one()
                if not remaining:
                    raise HTTPException(
                        status_code=400,
                        detail="系统必须保留至少一名启用状态的超级管理员",
                    )
            target.org_role = new_org_role
    if req.role is not None:
        if req.role not in {role.value for role in UserRole}:
            raise HTTPException(status_code=400, detail="用户角色无效")
        target.role = req.role
    if req.project_id is not None:
        effective_id = await _validate_managed_project(manager, req.project_id, db)
        # Step 2（设计说明 §7.7①）：**取消对成员表的写入**。
        # `project_id` 只表示「当前上下文」，归属真源是 project_members，
        # 只由成员管理接口写。原先"改所属项目就删旧成员行 + 加新成员行"
        # 会把「一人多项目」误判为「调岗」，导致跨项目归属被静默删除。
        # 双写问题不是靠"同步"解决，是靠取消这次写。
        target.project_id = effective_id
    if req.real_name is not None:
        target.real_name = req.real_name.strip()
    if req.phone is not None:
        target.phone = req.phone
    if req.is_active is not None:
        target.is_active = req.is_active
    if req.new_password:
        target.password_hash = hash_password(req.new_password)
        # 管理员直接设定了已知密码：清除一次性临时口令与强制改密标记，
        # 用户无需再被强制改密，列表「密码」列也随即归位为「—」。
        target.temp_password = None
        target.must_change_password = False
    await db.commit()
    return {"detail": "账号已更新"}


@router.delete("/users/{user_id}/wechat-binding")
async def unbind_user_wechat(
    user_id: int,
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """管理员解除指定账号的微信绑定。

    与小程序侧 `POST /miniprogram/auth/unbind`（只能解绑当前登录者本人）互补：
    账号被错误身份占用（例如调试用的 mock 身份）、用户换手机或换微信号时，
    管理员可以直接兜底解绑，无需用户本人能登录。

    Step 2：权限 key 由 ``settings`` 改为 ``user_manage``（同上，防总监越权）。
    """
    from sqlalchemy import delete as sql_delete
    from app.models.miniprogram import WechatBindingAudit, WechatUserBinding

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    # Step 2：同 update_user，判据改成员归属
    if not _can_manage_all_projects(manager) and not await _is_project_member(
        db, manager, target.project_id
    ):
        raise HTTPException(status_code=404, detail="账号不存在")

    binding_result = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.user_id == user_id)
    )
    binding = binding_result.scalar_one_or_none()
    if binding is None:
        return {"detail": "该账号未绑定微信", "unbound": False, "username": target.username}

    openid = binding.openid
    await db.execute(
        sql_delete(WechatUserBinding).where(WechatUserBinding.user_id == user_id)
    )
    db.add(WechatBindingAudit(
        user_id=user_id,
        openid_hash=hashlib.sha256(openid.encode("utf-8")).hexdigest(),
        action="admin_unbind",
    ))
    await db.commit()
    return {
        "detail": "已解除微信绑定",
        "unbound": True,
        "username": target.username,
        # 只回传可辨识前缀：让管理员能分辨解掉的是真实微信还是调试身份，不泄露完整 openid
        "openid_kind": "mock" if openid.startswith("mock_") else "wechat",
        "openid_hint": f"{openid[:10]}…",
    }


# ============ 眼镜端扫码登录 ============

# 一次性扫码凭证：ticket -> (user_id, expires_at)
_glasses_tickets: dict[str, tuple[int, float]] = {}
_GLASSES_TICKET_TTL = 120  # 秒


@router.post("/glasses/qr-create")
async def create_glasses_qr(
    user: User = Depends(get_current_user),
):
    """为当前登录用户生成一次性扫码登录凭证。"""
    ticket = secrets.token_urlsafe(24)
    _glasses_tickets[ticket] = (user.id, time.time() + _GLASSES_TICKET_TTL)
    return {"ticket": ticket, "expires_in": _GLASSES_TICKET_TTL}


class GlassesScanRequest(BaseModel):
    ticket: str = Field(min_length=8, max_length=128)


@router.post("/glasses/scan")
async def glasses_scan_login(
    req: GlassesScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """眼镜端凭一次性码换取登录令牌（码仅可用一次）。"""
    now = time.time()
    entry = _glasses_tickets.pop(req.ticket, None)
    if entry is None:
        raise HTTPException(status_code=404, detail="凭证无效或已被使用")
    user_id, expires_at = entry
    if now > expires_at:
        raise HTTPException(status_code=404, detail="二维码已过期，请刷新后重试")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="账号不存在或已禁用")

    # Step 2：眼镜扫码登录同样走合成（公司级 ∪ 项目岗位集合）
    from app.services.permissions import effective_permissions

    permissions = await effective_permissions(user, db)

    token = create_access_token({
        "sub": str(user.id),
        "role": user.role,
        "permissions": permissions,
        "purpose": "access",
        "device": "glasses",
    })

    project = await db.get(Project, user.project_id) if user.project_id else None
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        username=user.username,
        real_name=user.real_name,
        role=user.role,
        permissions=permissions,
        avatar_url=user.avatar_url,
        project_id=user.project_id,
        project_name=project.name if project else None,
        org_role=user.org_role,
        must_change_password=bool(user.must_change_password),
    )


# ===========================================================================
# 批量导入 / 导出账号（2026-10-20）
#
# 场景：一个项目部一次进几十号人，逐个点「新增用户」不现实；交付时还要拉一张
# 用户名 / 初始口令表发给现场。故提供三个接口：
#   GET  /auth/users/import-template  下载导入模板（xlsx）
#   POST /auth/users/batch-import     上传 xlsx/csv 批量建号（dry_run 可先预览）
#   GET  /auth/users/export           导出账号表（xlsx，含初始口令）
#
# 口径与「新增用户」一致：主岗必须是项目级岗位 key，公司级角色仅超管可授，
# 建号即写项目成员关系（权限边界的源头），并直接置为已审批可登录 —— 批量导入
# 是管理员主动建号，不应再让每人走一遍注册审批。
# ===========================================================================

# 批量导入的初始口令：与「重置密码」的统一默认口令保持一致（admin123），
# 导入后写入 temp_password，使账号表与列表「密码」列可直接展示；用户可自行修改。
DEFAULT_IMPORT_PASSWORD = "admin123"

# 导入/导出统一列序（2026-10-20 用户定稿）：只保留这 7 列，去掉微信绑定/审批状态/创建时间
_IMPORT_HEADERS = ["用户名", "姓名", "主岗", "公司级角色", "所属项目", "手机号", "密码"]

# 表头别名：现场填表常带 * 或不同叫法，能认就认，认不出才报「缺少姓名列」
_HEADER_ALIASES: dict[str, str] = {
    "姓名": "real_name", "名字": "real_name", "人员姓名": "real_name", "员工姓名": "real_name",
    "用户名": "username", "账号": "username", "账号名": "username", "登录名": "username",
    "手机号": "phone", "手机": "phone", "电话": "phone", "手机号码": "phone", "联系电话": "phone",
    "主岗": "role", "岗位": "role", "岗位名称": "role", "角色": "role", "项目岗位": "role",
    "公司级角色": "org_role", "公司角色": "org_role", "公司级岗位": "org_role",
    "所属项目": "project", "项目": "project", "项目名称": "project",
    "初始密码": "password", "密码": "password", "登录密码": "password",
}

# 主岗：中文名 / 英文 key / 现场口语写法都能填
_ROLE_BY_TEXT: dict[str, str] = {}
for _role_item in UserRole:
    _ROLE_BY_TEXT[_role_item.value] = _role_item.value
    _ROLE_BY_TEXT[ROLE_LABELS.get(_role_item.value, "")] = _role_item.value
_ROLE_BY_TEXT.pop("", None)
_ROLE_BY_TEXT.update({
    "super_admin": "super_admin", "admin": "admin",
    "超级管理员": "super_admin", "管理员": "admin",
    "总监": "chief_supervisor", "总监理工程师": "chief_supervisor",
    "总监代表": "deputy_chief", "总代": "deputy_chief",
    "专监": "special_supervisor", "专业监理": "special_supervisor",
    "专业监理工程师": "special_supervisor",
    "监理员": "inspector", "监理": "inspector",
    "资料": "documenter", "资料员": "documenter",
    "见证": "witness", "见证员": "witness", "见证取样员": "witness",
    "安全": "safety_supervisor", "安全员": "safety_supervisor",
    "安全监理员": "safety_supervisor",
})
del _role_item

_HEADER_STRIP_RE = re.compile(r"[\s*＊:：]")
_USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,64}")
_PHONE_RE = re.compile(r"1\d{10}")


def _norm_role_text(text: str) -> str:
    """主岗文本 → 英文 key；识别不出返回空串（由调用方报错，不静默兜底）。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    return _ROLE_BY_TEXT.get(raw) or _ROLE_BY_TEXT.get(raw.lower(), "")


def _read_import_grid(filename: str, data: bytes) -> list[list]:
    """把 xlsx / csv 读成二维表（统一入口，便于一种校验逻辑覆盖两种格式）。"""
    name = (filename or "").lower()
    if name.endswith((".csv", ".txt")):
        text = ""
        for encoding in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            raise HTTPException(status_code=400, detail="CSV 编码无法识别，请另存为 UTF-8 或改用 xlsx")
        return [list(row) for row in csv.reader(io.StringIO(text))]
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 - 用户上传任意文件，统一转成 400
        raise HTTPException(status_code=400, detail=f"文件无法解析（{type(exc).__name__}），请上传 .xlsx 或 .csv") from exc
    sheet = workbook.active
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def _parse_import_rows(filename: str, data: bytes) -> list[dict]:
    """解析人员表 → [{line, real_name, username, phone, role, org_role, project, password}]。"""
    grid = _read_import_grid(filename, data)
    if not grid:
        return []
    header = [_HEADER_STRIP_RE.sub("", str(cell or "")) for cell in grid[0]]
    columns: dict[str, int] = {}
    for index, text in enumerate(header):
        key = _HEADER_ALIASES.get(text)
        if key and key not in columns:
            columns[key] = index
    if "real_name" not in columns:
        raise HTTPException(status_code=400, detail="表头缺少「姓名」列，请下载导入模板后按模板填写")

    def cell(row: list, key: str) -> str:
        index = columns.get(key)
        if index is None or index >= len(row):
            return ""
        value = row[index]
        if value is None:
            return ""
        # Excel 里手机号/用户名常被识别成数字（如 13800000001.0），统一还原成整数文本
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, datetime):
            value = value.strftime("%Y-%m-%d")
        return str(value).strip()

    rows: list[dict] = []
    for line_no, raw in enumerate(grid[1:], start=2):
        item = {
            "line": line_no,
            "real_name": cell(raw, "real_name"),
            "username": cell(raw, "username"),
            "phone": cell(raw, "phone"),
            "role": cell(raw, "role"),
            "org_role": cell(raw, "org_role"),
            "project": cell(raw, "project"),
            "password": cell(raw, "password"),
        }
        if not any(item[k] for k in ("real_name", "username", "phone", "role", "org_role", "project")):
            continue  # 整行空白（常见于表格末尾）直接跳过，不计入失败
        rows.append(item)
    return rows


@router.get("/users/import-template")
async def download_user_import_template(
    manager: User = Depends(require_permission("user_manage")),
):
    """下载批量导入模板（xlsx）：人员名单只给表头，填写口径放「填写说明」页。"""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "人员名单"
    sheet.append(_IMPORT_HEADERS)

    header_fill = PatternFill("solid", fgColor="DCE6F1")
    for index in range(1, len(_IMPORT_HEADERS) + 1):
        cell = sheet.cell(row=1, column=index)
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        sheet.column_dimensions[get_column_letter(index)].width = 16
    sheet.freeze_panes = "A2"

    guide = workbook.create_sheet("填写说明")
    guide_rows = [
        ("列名", "填写要求"),
        ("用户名", "可选。留空则自动生成（优先用手机号，其次 user001 递增）；填了就必须是 3~64 位字母/数字/下划线/点/横线。"),
        ("姓名", "必填。证件姓名。"),
        ("主岗", "必填。可填中文或英文 key：总监理工程师/总监代表/专业监理工程师/监理员/资料员/见证员/安全监理员。"),
        ("公司级角色", "可选。只能填 超级管理员 / 管理员，且只有超级管理员能导入；其余人留空。"),
        ("所属项目", "可选。填项目全称或编号；留空则归属当前登录账号所在项目。非超管只能导入自己项目。"),
        ("手机号", "可选。填了必须 11 位手机号，且不能与已有账号重复。"),
        ("密码", "可选。留空统一使用 " + DEFAULT_IMPORT_PASSWORD + "；填了至少 8 位。"),
        ("", ""),
        ("导入结果", "建号即生效（已审批、可登录），并在「用户管理」列表显示初始密码；用户可自行修改。"),
        ("重复判定", "用户名或手机号已存在时该行跳过并给出原因，不影响其他行。"),
        ("上限", "单次最多 500 行，超过请分批。"),
    ]
    for row in guide_rows:
        guide.append(list(row))
    guide.column_dimensions["A"].width = 18
    guide.column_dimensions["B"].width = 96
    for cell in guide[1]:
        cell.font = Font(bold=True)

    buffer = io.BytesIO()
    workbook.save(buffer)
    filename = "白泽_账号批量导入模板.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/users/batch-import")
async def batch_import_users(
    file: UploadFile = File(...),
    default_password: str = Form(DEFAULT_IMPORT_PASSWORD),
    dry_run: bool = Form(False),
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """批量建号。``dry_run=true`` 只校验不落库，供前端「先看结果再确认导入」。

    逐行独立事务：某行失败不影响其它行，返回逐行明细（行号/用户名/结果/原因）。
    """
    password_default = (default_password or "").strip() or DEFAULT_IMPORT_PASSWORD
    if len(password_default) < 8:
        raise HTTPException(status_code=400, detail="初始密码至少 8 位")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件超过 8MB，请分批导入")

    rows = _parse_import_rows(file.filename or "", data)
    if not rows:
        raise HTTPException(status_code=400, detail="表格里没有可导入的数据行")
    if len(rows) > 500:
        raise HTTPException(status_code=400, detail=f"本次 {len(rows)} 行，超过单次 500 行上限，请分批上传")

    # 判重口径与建号一致：同企业内用户名唯一；(org_id, username) 才是数据库唯一约束
    existing_query = select(User.username, User.phone)
    if manager.org_id is not None:
        existing_query = existing_query.where(User.org_id == manager.org_id)
    existing_rows = (await db.execute(existing_query)).all()
    used_names = {str(name) for name, _ in existing_rows if name}
    used_phones = {str(phone) for _, phone in existing_rows if phone}
    next_seq = len(used_names) + 1

    all_projects = (await db.execute(select(Project))).scalars().all()
    if manager.org_id is not None:
        # 多企业隔离：项目匹配只在当前企业内进行，避免把同名的外部项目匹配进来
        all_projects = [p for p in all_projects if p.org_id in (None, manager.org_id)]
    can_all = _can_manage_all_projects(manager)

    results: list[dict] = []
    created = 0
    failed = 0

    for item in rows:
        line = item["line"]
        real_name = item["real_name"]
        username = item["username"]
        phone = item["phone"]
        role_text = item["role"]
        org_role_text = item["org_role"]
        project_text = item["project"]

        def fail(reason: str, name: str = "") -> None:
            nonlocal failed
            failed += 1
            results.append({"line": line, "real_name": real_name, "username": name or username,
                            "status": "error", "detail": reason})

        if not real_name:
            fail("缺少姓名")
            continue

        role = _norm_role_text(role_text)
        if not role:
            fail(f"主岗「{role_text or '空'}」无法识别，可填：总监理工程师/总监代表/专业监理工程师/监理员/资料员/见证员/安全监理员")
            continue
        if role in ORG_ROLES:
            fail(f"「{role_text}」是公司级角色，请填在「公司级角色」列")
            continue

        org_role: str | None = None
        if org_role_text:
            normalized = _norm_role_text(org_role_text)
            if normalized not in ORG_ROLES:
                fail(f"公司级角色「{org_role_text}」无法识别，只能填 超级管理员 / 管理员")
                continue
            if not manager.is_super_admin():
                fail("只有超级管理员可以授予公司级角色")
                continue
            org_role = normalized

        if phone:
            if not _PHONE_RE.fullmatch(phone):
                fail(f"手机号「{phone}」格式不对（需 11 位）")
                continue
            if phone in used_phones:
                fail(f"手机号 {phone} 已被其它账号使用")
                continue

        if not username:
            if phone and phone not in used_names:
                username = phone
            else:
                while True:
                    candidate = f"user{next_seq:03d}"
                    next_seq += 1
                    if candidate not in used_names:
                        username = candidate
                        break
        if not _USERNAME_RE.fullmatch(username):
            fail("用户名只能是 3~64 位字母、数字、下划线、点或横线")
            continue
        if username in used_names:
            fail(f"用户名 {username} 已存在")
            continue

        project_id = manager.project_id
        if project_text:
            matched = (
                [p for p in all_projects if (p.name or "") == project_text]
                or [p for p in all_projects if project_text in (p.name or "")]
                or [p for p in all_projects if (p.code or "") == project_text]
            )
            if not matched:
                fail(f"找不到项目「{project_text}」，请填项目全称或编号")
                continue
            if not can_all and matched[0].id != manager.project_id:
                fail(f"非超级管理员只能导入到自己项目，不能导入「{project_text}」")
                continue
            project_id = matched[0].id
        if project_id is None and not can_all:
            fail("当前账号还没有所属项目，请先选择项目或填写「所属项目」")
            continue

        # 🔴 导出表的密码列在用户自行改密后是「—（用户已自行设置）」占位符。
        # 现场常见做法是「导出 → 改几行 → 再导回去」，占位符必须视为未填，
        # 否则会把这串说明文字当成新密码写进账号。
        raw_password = item["password"]
        if raw_password.startswith("—") or "已自行设置" in raw_password:
            raw_password = ""
        password = raw_password or password_default
        if len(password) < 8:
            fail("初始密码至少 8 位")
            continue

        if dry_run:
            results.append({"line": line, "real_name": real_name, "username": username,
                            "status": "ok", "detail": "校验通过（未写入）"})
            used_names.add(username)
            if phone:
                used_phones.add(phone)
            continue

        try:
            account = User(
                username=username,
                password_hash=hash_password(password),
                real_name=real_name,
                role=role,
                org_role=org_role,
                phone=phone or None,
                project_id=project_id,
                is_active=True,
                temp_password=password,
            )
            db.add(account)
            await db.flush()
            # 建号即授权：与 create_user 一致，同步写项目成员关系（权限边界的源头）
            if project_id is not None:
                db.add(ProjectMember(project_id=project_id, user_id=account.id, role=role))
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - 单行失败回滚该行，继续下一行
            await db.rollback()
            logger.warning("批量导入第 %s 行失败：%s", line, exc)
            fail(f"写入失败：{type(exc).__name__}")
            continue

        created += 1
        used_names.add(username)
        if phone:
            used_phones.add(phone)
        results.append({"line": line, "real_name": real_name, "username": username,
                        "status": "ok", "detail": "已创建"})

    if not dry_run:
        _audit(
            db, "batch_import_users", user=manager,
            detail=f"批量导入账号 {created} 个，失败 {failed} 个（文件 {file.filename}）",
        )
        await db.commit()

    return {
        "dry_run": bool(dry_run),
        "total": len(rows),
        "created": created,
        "failed": failed,
        "default_password": password_default,
        "rows": results,
    }


@router.get("/users/export")
async def export_users(
    project_id: int | None = Query(
        None,
        ge=1,
        description="仅公司级账号生效：只导出该项目的账号（与页面筛选同口径）",
    ),
    manager: User = Depends(require_permission("user_manage")),
    db: AsyncSession = Depends(get_db),
):
    """导出账号表（xlsx）：含初始口令，便于一次性发给现场人员。

    可见范围与 ``list_users`` 同口径：公司级看全部，其余人只看本项目成员。
    真实密码不可逆，只导出管理员设过的临时口令（用户自行改密后即为空）。

    ``project_id``（2026-10-21 新增）：公司级账号在页面选了具体项目后导出，
    只导该项目的账号，口径与前端筛选**逐字一致** ——
    命中 = 该项目成员（含已离场，对应前端 ``members(pid, include_left=true)``）
    ∪ 所属项目 = 该项目。

    🔴 **该参数只对公司级账号生效**：非公司级账号本来就被限定在「自己项目的
    成员」，若认这个参数，等于让他导出别的项目的账号表（**含初始口令明文**），
    属真实越权 —— 故直接忽略而非报错（与 ``X-Project-Id`` 写入头的处理一致）。
    """
    scope_project_id: int | None = None
    scope_project: "Project | None" = None
    if project_id is not None and _can_manage_all_projects(manager):
        target = await db.get(Project, project_id)
        if target is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        # 企业隔离：不得借该参数跨企业导出（org_id 为空视为本企业历史数据）
        if manager.org_id is not None and target.org_id not in (None, manager.org_id):
            raise HTTPException(status_code=403, detail="无权导出该项目账号")
        scope_project_id = project_id
        scope_project = target

    query = select(User).order_by(User.created_at.asc(), User.id.asc())
    if manager.org_id is not None:
        query = query.where(User.org_id == manager.org_id)
    if scope_project_id is not None:
        # 含已离场成员：与页面列表看到的一致，导出的就是屏幕上那批人
        member_ids = select(ProjectMember.user_id).where(
            ProjectMember.project_id == scope_project_id,
        )
        query = query.where(
            or_(User.project_id == scope_project_id, User.id.in_(member_ids))
        )
    elif not _can_manage_all_projects(manager):
        member_ids = select(ProjectMember.user_id).where(
            ProjectMember.project_id == manager.project_id,
            ProjectMember.left_at.is_(None),
        )
        query = query.where(User.id.in_(member_ids))
    users = (await db.execute(query)).scalars().all()

    projects = {p.id: p.name for p in (await db.execute(select(Project))).scalars().all()}

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "账号表"
    # 只保留用户要的 7 列：微信绑定 / 审批状态 / 创建时间不进表（需要时看列表页）
    headers = ["用户名", "姓名", "主岗", "公司级角色", "所属项目", "手机号", "密码"]
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="DCE6F1")
    for index in range(1, len(headers) + 1):
        cell = sheet.cell(row=1, column=index)
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"

    for account in users:
        sheet.append([
            account.username,
            account.real_name,
            ROLE_LABELS.get(account.role, account.role),
            ROLE_LABELS.get(account.org_role, account.org_role or ""),
            projects.get(account.project_id, "") if account.project_id else "",
            account.phone or "",
            account.temp_password or "—（用户已自行设置）",
        ])

    widths = [20, 12, 18, 14, 24, 16, 22]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    buffer = io.BytesIO()
    workbook.save(buffer)
    # 指定了项目就把项目名放进文件名，避免同时导出多个项目时分不清谁是谁
    suffix = f"_{scope_project.name}" if scope_project else ""
    filename = f"白泽_账号表{suffix}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
