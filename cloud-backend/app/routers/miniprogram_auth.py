"""微信小程序登录与现有监理通账号绑定。"""
from datetime import datetime, timedelta
import hashlib
import secrets

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
import jwt
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.miniprogram import (
    WechatBindingAudit,
    WechatLoginCodeUse,
    WechatUserBinding,
)
from app.models.user import (
    AUDIT_APPROVED,
    AUDIT_PENDING,
    AUDIT_REJECTED,
    ORG_ROLES,
    User,
    UserRole,
)
from app.routers.auth import (
    _PHONE_RE,
    _verify_sms_code,
    create_access_token,
    get_current_user,
    hash_password,
    pwd_context,
)

router = APIRouter()


class WechatLoginRequest(BaseModel):
    code: str


class BindAccountRequest(BaseModel):
    bind_token: str
    username: str
    password: str
    role: str
    # 用户在绑定表单确认的微信昵称（官方「头像昵称填写能力」）；可空。
    # 注意：微信平台不提供真实微信号（wxid），昵称+头像是管理端识别绑定者的依据。
    wx_nickname: str | None = Field(default=None, max_length=64)


class RegisterRequest(BaseModel):
    """小程序注册：手机号 + 短信验证码，可选择性绑定当前微信身份。

    与 Web 注册同口径（设计说明 §8.8.3 新版）：注册产出**零权限裸用户**，
    项目岗位由「加入项目」动作授予，注册即激活、自动登录。
    """
    username: str
    password: str
    real_name: str
    # 主岗占位（项目岗位由加入项目决定）；拒绝公司级角色伪装。
    role: str | None = None
    phone: str  # 必填：手机号注册
    sms_code: str  # 必填：短信验证码（用途=register）
    # 可选：先调用 /login 拿到 bind_token（微信身份），注册后直接绑定并登录。
    bind_token: str | None = None
    # 绑定微信时用户确认的微信昵称（可空，见 BindAccountRequest 注释）
    wx_nickname: str | None = Field(default=None, max_length=64)


class WechatRegisterRequest(BaseModel):
    """路径 A 微信自助注册：用 /login 拿到的 bind_token（含 openid）一键注册。

    与 :class:`RegisterRequest`（手机号+短信）并列，二者都走 ``_token_response``
    唯一出口，且都产出零权限裸用户（``org_role=None`` / ``project_id=None`` /
    ``AUDIT_APPROVED``）。区别：本端点身份锚点是微信 openid，**不依赖手机号/短信**，
    零成本，契合监理通「组织系统」定位（已知、受公司背书的人纳入组织）。
    """
    bind_token: str
    real_name: str | None = Field(default=None, max_length=64)
    # 用户在注册表单确认的微信昵称（官方「头像昵称填写能力」），可空。
    wx_nickname: str | None = Field(default=None, max_length=64)


class UnbindRequest(BaseModel):
    password: str


class SetPasswordRequest(BaseModel):
    """设置/修改密码。已绑微信的用户 current_password 可省略。"""
    new_password: str = Field(min_length=8, max_length=128)
    current_password: str | None = None


async def _permissions_for(user: User, db: AsyncSession) -> list[str]:
    """当前生效权限（Step 2：公司级 ∪ 当前项目岗位集合）。"""
    from app.services.permissions import effective_permissions

    return await effective_permissions(user, db)


async def _release_unusable_binding(db: AsyncSession, openid: str) -> None:
    """若 openid 当前绑定的账号在小程序端**不可用**（超管/禁用/已删除/被拒绝），
    删除旧绑定并留审计，由调用方的新绑定接管该 openid。

    「换绑」唯一情形的统一实现 —— 登录绑定（/bind）与注册绑定（/register）
    两条路共用，避免出现「登录能换绑、注册却 409」的口径分裂
    （实测死局：微信被超管账号占死后，注册页报「该微信身份已绑定其他账号」）。
    正常情况（已绑项目级有效账号）仍 409，保持「一个微信一个账号」。

    Step 4 补充：``audit_status='rejected'`` 的账号同样视为不可用 —— 否则
    审批被拒的人会被自己的微信**永久占死**，无法用同一微信重新申请。

    2026-10-20 起：公司级「管理员」＝项目负责人，小程序**放行**；
    仅超管仍视为不可用（管理端只在 Web）。
    """
    existing = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.openid == openid)
    )
    old_binding = existing.scalar_one_or_none()
    if old_binding is None:
        return
    old_user = await db.get(User, old_binding.user_id)
    old_unusable = (
        old_user is None
        or not old_user.is_active
        or old_user.is_super_admin()
        or (getattr(old_user, "audit_status", None) == AUDIT_REJECTED)
    )
    if not old_unusable:
        raise HTTPException(status_code=409, detail="该微信身份已绑定其他账号")
    db.add(WechatBindingAudit(
        user_id=old_binding.user_id,
        openid_hash=hashlib.sha256(openid.encode("utf-8")).hexdigest(),
        action="unbind",
    ))
    await db.delete(old_binding)
    await db.flush()


async def _token_response(user: User, db: AsyncSession) -> dict:
    from app.models.user import Project as ProjectModel

    # Step 2（设计说明 §7.3）：小程序端**不接受超级管理员登录** ——
    # 跨项目授权能力（role_grant / project_create…）只在 Web 管理后台落地。
    # 2026-10-20 起：公司级「管理员」＝项目负责人，是现场用眼镜/小程序干活
    # 的人，**放行**；其项目范围仍由可见性规则收敛（成员项目 + 超管授权项目）。
    if user.is_super_admin():
        raise HTTPException(
            status_code=403,
            detail="超级管理员账号不支持小程序登录，请使用 Web 管理后台",
        )

    # Step 4（§8.8.5）：审批状态拦截。本函数是小程序**唯一**发 token 出口，
    # 加一次即覆盖登录 / 绑定 / 注册 / 扫码全部路径。
    if user.audit_status == AUDIT_PENDING:
        raise HTTPException(
            status_code=403,
            detail="账号待管理员审批，审核通过后即可使用小程序",
        )
    if user.audit_status == AUDIT_REJECTED:
        _reason = f"：{user.reject_reason}" if user.reject_reason else ""
        raise HTTPException(
            status_code=403,
            detail=f"注册申请已被拒绝{_reason}，如有疑问请联系管理员",
        )

    permissions = await _permissions_for(user, db)
    token = create_access_token({
        "sub": str(user.id),
        "role": user.role,
        "permissions": permissions,
        "purpose": "access",
        "client": "wechat_miniprogram",
    })
    project_name = None
    if user.project_id:
        proj = await db.get(ProjectModel, user.project_id)
        if proj:
            project_name = proj.name
    return {
        "bound": True,
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "real_name": user.real_name,
        "role": user.role,
        "org_role": user.org_role,
        "project_id": user.project_id,
        "project_name": project_name,
        "permissions": permissions,
        # 「忘记密码」兜底：管理员重置为一次性口令后置 True，
        # 小程序端据此把用户强制引导到「设置登录密码」页
        "must_change_password": bool(user.must_change_password),
    }


async def _exchange_code(code: str) -> tuple[str, str | None]:
    if settings.wechat_mock_login_enabled and code.startswith("mock:"):
        parts = code.split(":", 2)
        device_id = parts[1] if len(parts) > 1 and parts[1] else "default-device"
        digest = hashlib.sha256(device_id.encode("utf-8")).hexdigest()
        return f"mock_{digest[:48]}", None

    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(
            status_code=503,
            detail="微信小程序登录未配置，请设置 WECHAT_APP_ID/WECHAT_APP_SECRET",
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.wechat_api_base.rstrip('/')}/sns/jscode2session",
            params={
                "appid": settings.wechat_app_id,
                "secret": settings.wechat_app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        data = response.json()
    if data.get("errcode") or not data.get("openid"):
        raise HTTPException(
            status_code=401,
            detail=f"微信登录失败：{data.get('errmsg', '未返回 openid')}",
        )
    return data["openid"], data.get("unionid")


@router.post("/login")
async def wechat_login(
    req: WechatLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """用 wx.login 一次性 code 登录；未绑定时返回短期 bind_token。"""
    openid, unionid = await _exchange_code(req.code)
    code_hash = hashlib.sha256(req.code.encode("utf-8")).hexdigest()
    await db.execute(
        delete(WechatLoginCodeUse).where(
            WechatLoginCodeUse.used_at < datetime.now() - timedelta(days=1)
        )
    )
    used_result = await db.execute(
        select(WechatLoginCodeUse).where(WechatLoginCodeUse.code_hash == code_hash)
    )
    if used_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="微信登录 code 已使用，请重新调用 wx.login")
    db.add(WechatLoginCodeUse(code_hash=code_hash))
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="微信登录 code 已使用，请重新调用 wx.login",
        ) from exc
    result = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.openid == openid)
    )
    binding = result.scalar_one_or_none()
    if binding:
        user_result = await db.execute(select(User).where(User.id == binding.user_id))
        user = user_result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status_code=403, detail="绑定账号不存在或已禁用")
        # Step 2：若该微信绑定的是**超管账号**，小程序端不放行（见 _token_response）。
        # 此处不能直接抛 403 —— 那样微信身份被这个绑定占死，用户既登不进去、
        # 前端又拿不到 bind_token 去切换账号，页面成死局（实测 2026-10-10）。
        # 改为与「未绑定」同构：发一个 bind_token，让前端进入绑定表单，
        # 用户可用任意可用账号重新绑定；旧绑定由 /bind 的迁移逻辑让位。
        # 2026-10-20 起：公司级「管理员」＝项目负责人，放行；仅超管走换绑。
        if user.is_super_admin():
            bind_token = create_access_token(
                {
                    "sub": "wechat-binding",
                    "purpose": "wechat_bind",
                    "openid": openid,
                    "unionid": unionid,
                },
                expires_delta=timedelta(minutes=10),
            )
            await db.commit()
            return {
                "bound": False,
                "bind_token": bind_token,
                "expires_in": 600,
                "rebind_from_org_account": True,
                "message": "当前微信绑定的账号是超级管理员账号，小程序端不支持，请改绑其他账号",
            }
        binding.last_login_at = datetime.now()
        await db.commit()
        return await _token_response(user, db)

    bind_token = create_access_token(
        {
            "sub": "wechat-binding",
            "purpose": "wechat_bind",
            "openid": openid,
            "unionid": unionid,
        },
        expires_delta=timedelta(minutes=10),
    )
    await db.commit()
    return {
        "bound": False,
        "bind_token": bind_token,
        "expires_in": 600,
    }


@router.post("/bind")
async def bind_account(
    req: BindAccountRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = jwt.decode(
            req.bind_token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="绑定凭证无效或已过期") from exc
    if payload.get("purpose") != "wechat_bind" or not payload.get("openid"):
        raise HTTPException(status_code=401, detail="绑定凭证用途错误")

    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="该账号已被禁用")
    # Step 1：超管判据由 username=="admin" 改为 org_role
    if not user.is_super_admin() and user.role != req.role:
        raise HTTPException(status_code=403, detail="账号角色与所选角色不一致")

    openid = payload["openid"]
    # 允许"换绑"的唯一情形：该 openid 当前绑定的账号在小程序端**不可用**
    #（公司级账号 / 已禁用 / 已删除）→ 释放旧绑定并由本次接管；否则 409。
    await _release_unusable_binding(db, openid)
    existing_user = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.user_id == user.id)
    )
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该监理通账号已绑定微信")

    db.add(WechatUserBinding(
        user_id=user.id,
        openid=openid,
        unionid=payload.get("unionid"),
        wx_nickname=(req.wx_nickname or "").strip()[:64] or None,
    ))
    db.add(WechatBindingAudit(
        user_id=user.id,
        openid_hash=hashlib.sha256(openid.encode("utf-8")).hexdigest(),
        action="bind",
    ))
    await db.commit()
    return await _token_response(user, db)


@router.post("/register")
async def register_account(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """小程序注册：手机号 + 短信验证码（与 Web 同口径）。

    **权限隔离铁律（设计说明 §8.8.3 新版）**：注册产出**零权限裸用户**——
      - ``org_role`` = None，注册通道不可能拿到公司级角色；
      - ``project_id`` = None，项目岗位由「加入项目」动作授予；
      - ``audit_status`` = AUDIT_APPROVED，注册即激活、自动登录；
      - 微信 ``bind_token`` 绑定保留（绑定与激活解耦，绑定后即可用小程序）。
    授权全部来自「项目负责人发项目邀请码 → 成员加入项目」独立路径。
    """
    if not settings.allow_public_registration:
        raise HTTPException(status_code=403, detail="公开注册已关闭，请联系系统管理员创建账号")

    phone = (req.phone or "").strip()
    if not _PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")

    # 拒绝公司级角色伪装：注册只产出项目级裸用户
    if req.role and req.role in ORG_ROLES:
        raise HTTPException(status_code=400, detail="注册不支持公司级角色")

    # 先做「不消耗验证码」的校验：用户名/手机号查重失败时验证码仍可用
    username = req.username.strip()
    if username.lower() == "admin":
        raise HTTPException(status_code=400, detail="该用户名不可注册")

    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已存在")
    # 手机号唯一（防止同一手机号绑定多个账号）
    phone_taken = await db.execute(select(User).where(User.phone == phone))
    if phone_taken.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该手机号已被其他账号绑定")

    # 校验短信验证码（注册用途）
    if not await _verify_sms_code(db, phone, "register", req.sms_code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    user = User(
        username=username,
        password_hash=hash_password(req.password),
        real_name=req.real_name.strip(),
        role=req.role or "inspector",   # 主岗占位（项目岗位由加入项目决定）
        phone=phone,
        project_id=None,
        org_id=settings.registration_default_org_id,
        org_role=None,                  # 注册绝不给公司级角色
        audit_status=AUDIT_APPROVED,    # 注册即激活
    )
    db.add(user)
    await db.flush()

    bound_openid: str | None = None
    if req.bind_token:
        try:
            payload = jwt.decode(
                req.bind_token,
                settings.secret_key,
                algorithms=[settings.algorithm],
            )
        except Exception:
            raise HTTPException(status_code=401, detail="绑定凭证无效或已过期")
        if payload.get("purpose") != "wechat_bind" or not payload.get("openid"):
            raise HTTPException(status_code=401, detail="绑定凭证用途错误")
        bound_openid = payload["openid"]
        # 与 /bind 同口径：旧绑定账号在小程序端不可用（如被公司级账号占用）
        # → 释放并接管；正常占用 → 409。修「注册页报已绑定其他账号」死局。
        await _release_unusable_binding(db, bound_openid)
        db.add(WechatUserBinding(
            user_id=user.id,
            openid=bound_openid,
            unionid=payload.get("unionid"),
            wx_nickname=(req.wx_nickname or "").strip()[:64] or None,
        ))

    await db.commit()
    await db.refresh(user)

    # 注册即激活 + 自动登录：复用同一小程序发 token 出口（内含公司级/审批拦截）。
    return await _token_response(user, db)


@router.post("/wechat-register")
async def wechat_register(
    req: WechatRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """路径 A：微信授权即注册（零短信成本）。

    用 bind_token 里的 openid **自动建裸用户**：
      - ``username`` = ``wx_<openid 前段>``（openid 在本小程序全局唯一 ⇒ 用户名唯一）；
      - 随机强密码哈希（用户本人未知，须走 ``/password`` 设密才能在 Web 端登录）；
      - ``org_role=None`` / ``project_id=None`` / ``audit_status=AUDIT_APPROVED``。
    绑微信后走 ``_token_response`` 发 token（内含公司级/审批拦截）。
    """
    if not settings.allow_wechat_registration:
        raise HTTPException(status_code=403, detail="微信自助注册已关闭，请联系管理员创建账号")

    try:
        payload = jwt.decode(
            req.bind_token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="绑定凭证无效或已过期") from exc
    if payload.get("purpose") != "wechat_bind" or not payload.get("openid"):
        raise HTTPException(status_code=401, detail="绑定凭证用途错误")

    openid = payload["openid"]
    unionid = payload.get("unionid")

    # 该微信已注册且绑定可用 ⇒ 直接登录（幂等，避免重复建号）
    existing = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.openid == openid)
    )
    old_binding = existing.scalar_one_or_none()
    if old_binding is not None:
        old_user = await db.get(User, old_binding.user_id)
        # 2026-10-20 起：公司级「管理员」＝项目负责人，小程序可用；仅超管不可用。
        if (
            old_user is not None
            and old_user.is_active
            and not old_user.is_super_admin()
            and getattr(old_user, "audit_status", None) != AUDIT_REJECTED
        ):
            return await _token_response(old_user, db)
        # 旧绑定不可用（超管/禁用/已拒绝）⇒ 释放后由本次接管
        await _release_unusable_binding(db, openid)

    # 生成唯一 username：openid 全局唯一；超长则哈希降级到 64 位以内。
    username = f"wx_{openid}"
    if len(username) > 64:
        username = f"wx_{hashlib.sha256(openid.encode('utf-8')).hexdigest()[:60]}"

    # 极端兜底：同一 org 内 username 撞名（理论上不会发生）⇒ 加随机后缀重试。
    for _ in range(5):
        clash = await db.execute(select(User).where(User.username == username))
        if clash.scalar_one_or_none() is None:
            break
        username = f"{username[:56]}_{secrets.token_hex(3)}"
    else:
        raise HTTPException(status_code=409, detail="账号生成冲突，请稍后重试")

    real_name = (req.real_name or req.wx_nickname or username).strip()[:64] or username

    user = User(
        username=username,
        password_hash=hash_password(secrets.token_urlsafe(16)),
        real_name=real_name,
        role=UserRole.INSPECTOR.value,       # 主岗占位（项目岗位由加入项目决定）
        phone=None,
        project_id=None,
        org_id=settings.registration_default_org_id,
        org_role=None,                        # 微信注册绝不给公司级角色
        audit_status=AUDIT_APPROVED,          # 注册即激活
    )
    db.add(user)
    await db.flush()
    db.add(WechatUserBinding(
        user_id=user.id,
        openid=openid,
        unionid=unionid,
        wx_nickname=(req.wx_nickname or "").strip()[:64] or None,
    ))
    db.add(WechatBindingAudit(
        user_id=user.id,
        openid_hash=hashlib.sha256(openid.encode("utf-8")).hexdigest(),
        action="bind",
    ))
    await db.commit()
    await db.refresh(user)
    return await _token_response(user, db)


@router.post("/password")
async def set_password(
    req: SetPasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """小程序端设置/修改账号密码（③ 微信用户跨端登录支撑点）。

    存在意义：微信一键绑定登录的用户**从未输入过密码**，其 `password_hash`
    是注册时或管理员创建时写的随机/初始值，本人并不知道 —— 于是无法在 Web
    端用「账号 + 密码」登录（用户诉求：同一账号登 Web 端、数据同步）。
    手机端拿不到旧密码，故本接口对**已绑定微信**的用户免旧密码校验。

    安全依据（与「免旧密码」的等价性论证）：
      - 调用者必须已持有本端 access_token（``get_current_user``），而小程序
        端的 token 只能经 ``_token_response`` 发放 —— 该出口要求微信 openid
        已绑定到本账号，等于「微信身份即身份证明」；
      - 额外再校验一次绑定关系存在（防御 token 由其它路径签发的情况）；
      - 已绑微信且能通过微信登录 ⇒ 控制该微信 ⇒ 是该账号的合法持有人。

    若用户**知道**旧密码，仍会校验（知道则必须提供，避免 token 泄露后被改密）。
    """
    # 已绑定微信 = 身份可信；未绑定则必须知道旧密码
    binding_result = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.user_id == user.id)
    )
    bound = binding_result.scalar_one_or_none() is not None
    if not bound:
        if not req.current_password:
            raise HTTPException(
                status_code=400,
                detail="当前账号未绑定微信，需提供原密码",
            )
        if not pwd_context.verify(req.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="原密码错误")
    elif req.current_password:
        # 提供了就校验（防止 token 外泄被直接改密）
        if not pwd_context.verify(req.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="原密码错误")

    if pwd_context.verify(req.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    user.password_hash = hash_password(req.new_password)
    # 用户已自行设密 → 解除「强制改密」锁定（管理员重置后置的 True 在此清除）
    user.must_change_password = False
    await db.commit()
    return {
        "detail": "密码已设置，可在 Web 端用「账号 + 密码」登录",
        "username": user.username,
    }


@router.post("/unbind")
async def unbind_account(
    req: UnbindRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="密码错误")
    binding_result = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.user_id == user.id)
    )
    binding = binding_result.scalar_one_or_none()
    if binding is None:
        return {"detail": "当前账号未绑定微信"}
    await db.execute(
        delete(WechatUserBinding).where(WechatUserBinding.user_id == user.id)
    )
    db.add(WechatBindingAudit(
        user_id=user.id,
        openid_hash=hashlib.sha256(binding.openid.encode("utf-8")).hexdigest(),
        action="unbind",
    ))
    await db.commit()
    return {"detail": "微信账号已解绑"}


@router.get("/status")
async def binding_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.user_id == user.id)
    )
    binding = result.scalar_one_or_none()
    return {
        "bound": binding is not None,
        "bound_at": binding.created_at.isoformat() if binding else None,
        "last_login_at": binding.last_login_at.isoformat() if binding else None,
        # 用户确认的微信昵称/头像（「头像昵称填写能力」采集），供「我的」页展示与预填
        "wx_nickname": binding.wx_nickname if binding else None,
        "wx_avatar_url": binding.wx_avatar_url if binding else None,
    }


# ============ 微信资料（昵称/头像）—— 管理端识别绑定者 ============
# 微信平台不向小程序提供真实微信号（wxid）。管理端「微信绑定管理」要能认出
# 绑定者是谁，唯一合规途径是官方「头像昵称填写能力」：用户主动确认自己的
# 昵称与头像，此处入库并在绑定列表展示。头像文件落 /uploads/avatars/。

_AVATAR_MAX_BYTES = 2 * 1024 * 1024
_AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


async def _save_avatar(image_bytes: bytes, filename: str, user_id: int) -> str:
    from pathlib import Path
    import time as _time

    ext = Path(filename or "").suffix.lower() or ".png"
    if ext not in _AVATAR_EXTS:
        ext = ".png"
    if len(image_bytes) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="头像图片不能超过 2MB")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="头像文件为空")
    name = f"avatar_{user_id}_{int(_time.time())}{ext}"
    path = settings.upload_dir / "avatars" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes)
    return f"/uploads/avatars/{name}"


@router.post("/wechat-profile")
async def update_wechat_profile(
    nickname: str | None = Form(default=None),
    avatar: UploadFile | None = File(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新本人微信资料的昵称/头像（multipart，两项均可选但至少传其一）。"""
    result = await db.execute(
        select(WechatUserBinding).where(WechatUserBinding.user_id == user.id)
    )
    binding = result.scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="当前账号未绑定微信")

    if nickname is not None:
        binding.wx_nickname = nickname.strip()[:64] or None
    if avatar is not None:
        data = await avatar.read()
        binding.wx_avatar_url = await _save_avatar(data, avatar.filename or "", user.id)

    if nickname is None and avatar is None:
        raise HTTPException(status_code=400, detail="未提供任何要更新的内容")
    await db.commit()
    return {
        "detail": "微信资料已更新",
        "wx_nickname": binding.wx_nickname,
        "wx_avatar_url": binding.wx_avatar_url,
    }
