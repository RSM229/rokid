"""项目管理路由 - 创建/查询/切换项目 + 图纸/档案管理"""
import os
import time
import shutil
import re
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, delete, func, or_, select, text, update
from pydantic import BaseModel
from typing import Optional

from app.db import get_db
from app.models.user import (
    ORG_ROLE_ADMIN,
    ORG_ROLE_SUPER_ADMIN,
    ROLE_EXCLUSIVE,
    ROLE_LABELS,
    Project,
    User,
    UserRole,
)
from app.models.project_member import ProjectMember
from app.models.project_view_grant import ProjectViewGrant
from app.models.security_audit import SecurityAudit
from app.routers.auth import (
    _can_view_all_projects,
    _is_project_member,
    get_current_user,
    require_permission,
    require_project_id,
    require_role,
)
from app.config import settings

router = APIRouter()


def _write_audit(
    db: AsyncSession,
    user: User | None,
    action: str,
    detail: str | None,
    ip: str | None = None,
) -> None:
    """写一条安全审计（随调用方事务一起提交）。

    对标保利 AI 监理通"越权明确拒绝并写入审计"——审计与业务同事务，
    拒绝场景先落审计再 raise，保证抵赖不了。
    """
    db.add(SecurityAudit(
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        action=action,
        detail=(detail or None) and detail[:500],
        ip=ip,
    ))


async def _client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def _safe_base_name(value: str) -> str:
    """生成仅用于服务器端存储的安全文件名。"""
    value = Path(value).name
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("._")
    return value[:120] or f"file_{int(time.time())}"


async def _read_upload_limited(file: UploadFile) -> bytes:
    """读取受限大小的图纸/档案，避免无界写入耗尽磁盘。"""
    data = await file.read(settings.max_document_bytes + 1)
    await file.close()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > settings.max_document_bytes:
        raise HTTPException(413, "文件超过上传大小限制")
    return data


class CreateProjectRequest(BaseModel):
    """创建项目请求"""
    name: str
    code: str
    short_name: Optional[str] = None
    eng_type: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    construction_unit: Optional[str] = None
    supervision_unit: Optional[str] = None
    supervision_org: Optional[str] = None
    acceptance_participants: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    seal_signature_id: Optional[int] = None
    # 监理日志项目级自动任务的设定时点（HH:MM，NULL=用全局默认 DAILY_DOC_HOUR）
    log_daily_time: Optional[str] = None



class UpdateProjectRequest(BaseModel):
    """修改项目基本信息（仅传需要改的字段）"""
    name: Optional[str] = None
    short_name: Optional[str] = None
    eng_type: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    construction_unit: Optional[str] = None
    supervision_unit: Optional[str] = None
    supervision_org: Optional[str] = None
    acceptance_participants: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    seal_signature_id: Optional[int] = None
    log_daily_time: Optional[str] = None



class ProjectInfo(BaseModel):
    id: int
    name: str
    code: str
    short_name: Optional[str] = None
    eng_type: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    construction_unit: Optional[str] = None
    supervision_unit: Optional[str] = None
    supervision_org: Optional[str] = None
    acceptance_participants: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    seal_signature_id: Optional[int] = None
    # 监理日志自动任务时点（HH:MM，NULL=全局默认）
    log_daily_time: Optional[str] = None

    # Step 5：项目生命周期状态（active=进行中 / completed=已完工 / archived=已归档）
    status: str = "active"
    # 项目结束时间（结项动作写入；NULL=未结项）
    completed_at: Optional[str] = None

    # 项目负责人（创建 / 负责的管理员；owner_name 为回填的姓名，便于列表直显）
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None


# Step 5：项目生命周期状态取值（与 Project.status 的 comment 同口径）
PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_COMPLETED = "completed"
PROJECT_STATUS_ARCHIVED = "archived"


def _project_info(project: Project) -> ProjectInfo:
    """Project ORM → 出参（统一装配点，避免各处漏掉 status）。"""
    return ProjectInfo(
        id=project.id,
        name=project.name,
        code=project.code,
        short_name=project.short_name,
        # 兼容滚动发布：即使路由先上线、Project 模型尚未同步，列表也不应 500。
        # 完整功能仍需同时发布模型与数据库迁移；写接口由发布流程保证一致。
        eng_type=getattr(project, "eng_type", None),
        location=project.location,
        description=project.description,
        construction_unit=project.construction_unit,
        supervision_unit=project.supervision_unit,
        supervision_org=project.supervision_org,
        acceptance_participants=project.acceptance_participants,
        latitude=project.latitude,
        longitude=project.longitude,
        seal_signature_id=project.seal_signature_id,
        log_daily_time=getattr(project, "log_daily_time", None),

        status=project.status or PROJECT_STATUS_ACTIVE,
        completed_at=project.completed_at.isoformat() if project.completed_at else None,
        owner_id=project.owner_id,
    )


async def _fill_owner_names(
    db: AsyncSession, projects: list[Project], infos: list[ProjectInfo]
) -> None:
    """批量回填 owner_name（一次查询；姓名空则退回用户名）。"""
    owner_ids = {p.owner_id for p in projects if p.owner_id}
    if not owner_ids:
        return
    rows = await db.execute(
        select(User.id, User.real_name, User.username).where(User.id.in_(owner_ids))
    )
    names = {uid: (real or uname) for uid, real, uname in rows.all()}
    for p, info in zip(projects, infos):
        if p.owner_id:
            info.owner_name = names.get(p.owner_id)


@router.get("/list", response_model=list[ProjectInfo])
async def list_projects(
    include_closed: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前用户有权访问的项目列表（用于项目切换）。

    权限边界（对标保利 AI 监理通"候选项目权限过滤"）：
    - 超级管理员：全平台项目；
    - 其他角色（含公司级「管理员」）：仅本人为**有效成员**（``left_at IS NULL``）
      的项目；另把当前绑定的 ``project_id`` 兜底并入，防止成员数据漂移把用户
      锁在项目之外。管理员要看其他项目负责人的项目，由超管将其加为该项目
      成员（成员关系即授权）——2026-10-20 起「管理员」默认只看自己的项目。

    Step 5：默认只返回**进行中**（``status='active'``）的项目 —— 已完工 /
    已归档项目从各方列表中收口（历史数据仍可查）。需要查看历史项目时传
    ``include_closed=true``。
    """
    query = select(Project).order_by(Project.created_at.desc())
    if not _can_view_all_projects(user):
        member_ids = select(ProjectMember.project_id).where(
            ProjectMember.user_id == user.id,
            # Step 5：只有**在岗**成员行才产生可见项目 —— 离场（软删除）后
            # 项目自动从其列表中退出，这正是"转场"的对外效果。
            ProjectMember.left_at.is_(None),
        )
        conditions = [Project.id.in_(member_ids)]
        if user.project_id is not None:
            conditions.append(Project.id == user.project_id)
        # 2026-10-20：超管可授权管理员（项目负责人）查看其他项目 —— 并入授权项目
        granted = await _granted_project_ids(db, user.id)
        if granted:
            conditions.append(Project.id.in_(granted))
        query = query.where(or_(*conditions))
    if not include_closed:
        query = query.where(Project.status == PROJECT_STATUS_ACTIVE)
    result = await db.execute(query)
    projects = result.scalars().all()
    infos = [_project_info(p) for p in projects]
    await _fill_owner_names(db, projects, infos)
    return infos


async def _validate_project_fields(db, user, req, previous=None):
    import math
    for key, limit in (("latitude", 90), ("longitude", 180)):
        value = getattr(req, key, None)
        if value not in (None, ""):
            try:
                number = float(value)
                if not math.isfinite(number) or not -limit <= number <= limit:
                    raise ValueError()
            except (TypeError, ValueError):
                raise HTTPException(400, "项目经纬度格式或范围无效")
    for key in ("construction_unit", "supervision_unit", "supervision_org"):
        if len(getattr(req, key, None) or "") > 256:
            raise HTTPException(400, "单位名称最多256个字符")
    eng_type = getattr(req, "eng_type", None)
    if eng_type not in (None, "", "房建", "市政", "其他"):
        raise HTTPException(400, "工程类型只能是 房建 / 市政 / 其他")
    if req.seal_signature_id and (previous is None or previous.seal_signature_id != req.seal_signature_id):
        from app.models.signature import Signature
        signature = await db.get(Signature, req.seal_signature_id)
        if not signature or signature.user_id != user.id or not signature.is_active:
            raise HTTPException(403, "项目章必须选用本人持有的启用签章")


@router.post("/create", response_model=ProjectInfo)
async def create_project(
    req: CreateProjectRequest,
    user: User = Depends(require_permission("project_create")),
    db: AsyncSession = Depends(get_db),
):
    """创建新项目并自动分配给当前用户。

    权限：``project_create``（设计说明 §7.2 权限表）—— **仅超管持有**。
    原先用 ``get_current_user``（只要求登录），任何岗位都能立项，
    且建完即成为该项目成员（成员关系是权限边界根基）→ 属越权缺陷，
    2026-10-10 修正为按权限表判据。
    """
    await _validate_project_fields(db, user, req)
    existing = await db.execute(select(Project).where(Project.code == req.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"项目编号 {req.code} 已存在")

    project = Project(
        name=req.name,
        code=req.code,
        short_name=req.short_name,
        eng_type=req.eng_type,
        location=req.location,
        description=req.description,
        org_id=user.org_id,
        construction_unit=req.construction_unit,
        supervision_unit=req.supervision_unit,
        supervision_org=req.supervision_org,
        acceptance_participants=req.acceptance_participants,
        latitude=req.latitude,
        longitude=req.longitude,
        seal_signature_id=req.seal_signature_id,
        owner_id=user.id,

    )
    db.add(project)
    await db.flush()

    user.project_id = project.id
    # 创建者自动成为项目成员（创建即授权——成员关系是权限边界的根基）
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=user.role))
    await db.commit()
    await db.refresh(project)
    return _project_info(project)


@router.put("/{project_id}", response_model=ProjectInfo)
async def update_project(
    project_id: int,
    req: UpdateProjectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改项目基本信息（名称/简称/地点/描述/参建单位/经纬度/项目章）。

    权限（2026-10-13 修订）：
      - **范围闸**：仅**超管**（``_can_view_all_projects``）可改任意项目；
        其余账号（含公司级管理员）必须是该项目的**有效成员**（``project_members``
        中 ``left_at IS NULL``）才能修改。归属真源是成员表，**不是**
        ``users.project_id`` 单值上下文。（2026-10-20 起公司级「管理员」定位为
        项目负责人，默认只看自己项目，不再拥有跨项目修改范围。）
      - **权限闸**：需 ``project_edit`` —— 2026-10-13 起为跨两层键，公司级
        默认持有，项目级岗位（总监）默认持有，其余岗位由超管在「角色权限」页授予。
    只更新请求里显式带上的字段，未传字段保持不变。
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if not _can_view_all_projects(user) and not await _is_project_member(db, user, project_id):
        raise HTTPException(status_code=403, detail="只能修改自己所在项目的信息")

    if not user.has_permission("project_edit"):
        raise HTTPException(403, "需要项目编辑权限")
    await _validate_project_fields(db, user, req, previous=project)
    for key in ("eng_type", "construction_unit", "supervision_unit", "supervision_org", "acceptance_participants", "latitude", "longitude", "seal_signature_id", "log_daily_time"):
        if key in req.model_fields_set:
            value = getattr(req, key)
            setattr(project, key, (value.strip() or None) if isinstance(value, str) else value)

    if req.name is not None:
        name = req.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="项目名称不能为空")
        if len(name) > 128:
            raise HTTPException(status_code=400, detail="项目名称过长")
        project.name = name
    if req.short_name is not None:
        project.short_name = req.short_name.strip() or None
    if req.location is not None:
        project.location = req.location.strip() or None
    if req.description is not None:
        project.description = req.description.strip() or None

    await db.commit()
    await db.refresh(project)
    return _project_info(project)


class TransferProjectRequest(BaseModel):
    """项目转让请求：把项目负责人（owner_id）移交给另一位用户"""
    user_id: int


@router.post("/{project_id}/transfer")
async def transfer_project(
    project_id: int,
    req: TransferProjectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """转让项目负责人（owner_id）。

    权限：仅**超管**或当前**项目负责人本人**（``project.owner_id == user.id``）。
    owner_id 只是「创建 / 负责」标记（授权展开查询用），**不改变成员权限边界**
    ——对方若要管理项目，仍须是项目在岗成员（成员关系即授权）。转让时对方
    不是在岗成员不阻断，但返回 warning 提示去「分配人员」任命岗位。
    """
    project = await _get_project_or_404(db, project_id)
    if not (user.is_super_admin or project.owner_id == user.id):
        raise HTTPException(status_code=403, detail="仅超级管理员或项目负责人可转让项目")
    target = await db.get(User, req.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if project.org_id and target.org_id and target.org_id != project.org_id:
        raise HTTPException(status_code=400, detail="对方不属于本项目所属企业")
    if target.id == project.owner_id:
        raise HTTPException(status_code=400, detail="对方已是项目负责人")

    member = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == target.id,
            ProjectMember.left_at.is_(None),
        )
    )
    warning = None
    if member.scalar_one_or_none() is None:
        warning = "对方不是本项目在岗成员，转让后请到「分配人员」中为其任命岗位"

    old_owner_id = project.owner_id
    project.owner_id = target.id
    db.add(
        SecurityAudit(
            user_id=user.id,
            username=user.username,
            action="project_transfer",
            detail=(
                f"项目#{project.id}({project.name}) 负责人由 "
                f"用户#{old_owner_id} 转让给 用户#{target.id}({target.username})"
            ),
        )
    )
    await db.commit()
    return {"detail": "ok", "owner_id": project.owner_id, "warning": warning}


@router.post("/{project_id}/switch")
async def switch_project(
    project_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """切换当前用户的项目。

    权限边界（对标保利 AI 监理通"选择项目再次鉴权"）：非超管必须先取得
    项目成员资格；越权明确拒绝（403）并写入安全审计。
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    ip = await _client_ip(request)

    # Step 5：已结项（完工/归档）项目不得再切入 —— 项目已从各方列表收口。
    # 超管例外（需要查看历史项目）；若本就停留在该项目则不拦。
    if (
        (project.status or PROJECT_STATUS_ACTIVE) != PROJECT_STATUS_ACTIVE
        and project_id != user.project_id
        and not _can_view_all_projects(user)
    ):
        _write_audit(
            db, user, "project_switch_denied",
            f"尝试切换到已结项项目 #{project_id} {project.name}", ip,
        )
        await db.commit()
        raise HTTPException(status_code=403, detail="项目已结项，无法切换（历史项目请联系管理员查看）")

    if not _can_view_all_projects(user) and project_id != user.project_id:
        member = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
                # Step 5：只有**在岗**成员才有资格切入；离场后需重新加入
                ProjectMember.left_at.is_(None),
            )
        )
        if member.first() is None:
            # 2026-10-20：超管授权（按管理员）的跨项目查看，同样允许切入。
            # 授权粒度是管理员，这里把已授权源管理员展开成其名下项目再判等。
            granted = await _granted_project_ids(db, user.id)
            if project_id not in granted:
                _write_audit(
                    db, user, "project_switch_denied",
                    f"尝试切换到未授权项目 #{project_id} {project.name}", ip,
                )
                await db.commit()
                raise HTTPException(status_code=403, detail="您没有该项目的数据访问权限")

    previous = user.project_id
    user.project_id = project_id
    _write_audit(
        db, user, "project_switch",
        f"#{previous} -> #{project_id} {project.name}", ip,
    )
    await db.commit()
    return {"detail": f"已切换到项目: {project.name}", "project_id": project_id}


# ============ 管理员与跨项目查看授权（超管专用，2026-10-20） ============


async def _granted_project_ids(db: AsyncSession, user_id: int) -> list[int]:
    """当前用户被超管授权查看的**额外**项目 id 列表（与成员项目解耦）。

    授权粒度是「管理员」：``ProjectViewGrant`` 记录
    ``(grantee_user_id, grantor_admin_id)``，表示 grantee 可查看 grantor 管理员
    **名下（创建 / 负责）的全部项目**。此处把已授权的源管理员展开成其名下的
    项目——严格口径：``projects.owner_id == grantor_admin_id``，即该管理员创建 /
    负责的项目，而非其作为成员的全部项目。
    """
    grantor_rows = (
        await db.execute(
            select(ProjectViewGrant.grantor_admin_id).where(
                ProjectViewGrant.grantee_user_id == user_id
            )
        )
    ).scalars().all()
    grantor_ids = [g for g in grantor_rows if g is not None]
    if not grantor_ids:
        return []
    rows = (
        await db.execute(
            select(Project.id).where(Project.owner_id.in_(grantor_ids))
        )
    ).scalars().all()
    return list(rows)


class ViewGrantRequest(BaseModel):
    """授权某管理员查看另一个管理员名下的所有项目。"""
    grantee_user_id: int
    grantor_admin_id: int


class AdminSetRequest(BaseModel):
    """设置 / 取消某用户的公司级「管理员」角色。"""
    user_id: int


def _require_super(user: User) -> None:
    if not user.is_super_admin():
        raise HTTPException(status_code=403, detail="仅超级管理员可执行此操作")


@router.get("/view-grants")
async def list_view_grants(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """授权总览：全部用户（筛管理员）、各管理员名下项目数、全部授权记录。"""
    _require_super(user)
    users = (
        await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.id))
    ).scalars().all()
    grants = (await db.execute(select(ProjectViewGrant))).scalars().all()
    # 各管理员「创建 / 负责」的项目数（projects.owner_id），用于授权时直观展示
    owner_counts = (
        await db.execute(
            select(Project.owner_id, func.count(Project.id))
            .where(Project.owner_id.isnot(None))
            .group_by(Project.owner_id)
        )
    ).all()
    project_counts: dict[int, int] = {uid: c for uid, c in owner_counts}
    return {
        "admins": [
            {
                "id": u.id,
                "real_name": u.real_name,
                "username": u.username,
                "org_role": u.org_role,
                "project_id": u.project_id,
                "project_count": project_counts.get(u.id, 0),
            }
            for u in users
        ],
        "grants": [
            {
                "grantee_user_id": g.grantee_user_id,
                "grantor_admin_id": g.grantor_admin_id,
                "grantor_user_id": g.grantor_user_id,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
            for g in grants
        ],
    }


@router.post("/view-grants")
async def create_view_grant(
    req: ViewGrantRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """授权某管理员查看另一个管理员名下的所有项目（幂等）。"""
    _require_super(user)
    target = await db.get(User, req.grantee_user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="被授权人不存在")
    if target.org_role != ORG_ROLE_ADMIN:
        raise HTTPException(status_code=400, detail="被授权人必须是管理员（项目负责人）")
    if req.grantee_user_id == req.grantor_admin_id:
        raise HTTPException(status_code=400, detail="不能授权管理员查看自己的项目")
    grantor = await db.get(User, req.grantor_admin_id)
    if grantor is None:
        raise HTTPException(status_code=404, detail="源管理员不存在")
    if grantor.org_role != ORG_ROLE_ADMIN:
        raise HTTPException(status_code=400, detail="只能授权查看「管理员」名下的项目")
    existing = (
        await db.execute(
            select(ProjectViewGrant).where(
                ProjectViewGrant.grantee_user_id == req.grantee_user_id,
                ProjectViewGrant.grantor_admin_id == req.grantor_admin_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            ProjectViewGrant(
                grantee_user_id=req.grantee_user_id,
                grantor_admin_id=req.grantor_admin_id,
                grantor_user_id=user.id,
            )
        )
        await db.commit()
    db.add(
        SecurityAudit(
            user_id=user.id,
            username=user.username,
            action="project_view_grant",
            detail=f"授权 用户#{req.grantee_user_id} 查看管理员#{req.grantor_admin_id} 名下的项目",
        )
    )
    await db.commit()
    return {"detail": "ok"}


@router.delete("/view-grants")
async def delete_view_grant(
    req: ViewGrantRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """撤销某管理员查看另一个管理员项目的授权。"""
    _require_super(user)
    existing = (
        await db.execute(
            select(ProjectViewGrant).where(
                ProjectViewGrant.grantee_user_id == req.grantee_user_id,
                ProjectViewGrant.grantor_admin_id == req.grantor_admin_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="授权记录不存在")
    await db.delete(existing)
    db.add(
        SecurityAudit(
            user_id=user.id,
            username=user.username,
            action="project_view_grant_revoke",
            detail=f"撤销 用户#{req.grantee_user_id} 查看管理员#{req.grantor_admin_id} 名下的项目",
        )
    )
    await db.commit()
    return {"detail": "ok"}


@router.post("/admins/set")
async def set_admin(
    req: AdminSetRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """将某用户设为公司级「管理员」（项目负责人）。"""
    _require_super(user)
    target = await db.get(User, req.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.org_role == ORG_ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="不能修改超级管理员")
    target.org_role = ORG_ROLE_ADMIN
    db.add(
        SecurityAudit(
            user_id=user.id,
            username=user.username,
            action="org_role_set",
            detail=f"设 用户#{req.user_id}({target.username}) 为管理员",
        )
    )
    await db.commit()
    return {"detail": "ok", "org_role": ORG_ROLE_ADMIN}


@router.post("/admins/clear")
async def clear_admin(
    req: AdminSetRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消某用户的公司级「管理员」角色（恢复为普通项目成员）。"""
    _require_super(user)
    target = await db.get(User, req.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.org_role == ORG_ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="不能修改超级管理员")
    target.org_role = None
    db.add(
        SecurityAudit(
            user_id=user.id,
            username=user.username,
            action="org_role_clear",
            detail=f"取消 用户#{req.user_id}({target.username}) 的管理员角色",
        )
    )
    await db.commit()
    return {"detail": "ok", "org_role": None}


# ============ 项目成员管理（权限边界的管理入口） ============


class AddMemberRequest(BaseModel):
    """加入项目成员请求（Step 2：新增项目内岗位指定）。"""
    user_id: int
    # 主岗；不传则沿用该用户当前主岗（兼容旧调用）
    role: str | None = None
    # 附加岗（可叠加岗位的多岗支持，设计说明 §7.8.3 α）
    extra_roles: list[str] | None = None


def _validate_member_roles(
    role: str | None, extra_roles: list[str] | None
) -> tuple[str | None, list[str]]:
    """校验岗位合法性 + 排他岗位约束。返回 (主岗, 附加岗)。

    规则（设计说明 §7.8.1）：
      - 岗位必须是 ``UserRole`` 内的合法 key；
      - **排他岗位**（总监 / 总监代表）只能作为**主岗**，不得出现在附加岗；
      - 主岗与附加岗不得重复。
    """
    valid = {m.value for m in UserRole}
    if role is not None and role not in valid:
        raise HTTPException(status_code=400, detail="项目岗位无效")

    extras: list[str] = []
    for item in extra_roles or []:
        if item not in valid:
            raise HTTPException(status_code=400, detail=f"附加岗位无效: {item}")
        if item in ROLE_EXCLUSIVE:
            raise HTTPException(
                status_code=400,
                detail=f"「{ROLE_LABELS.get(item, item)}」为排他岗位，不能作为附加岗位",
            )
        if item == role:
            continue
        if item not in extras:
            extras.append(item)
    return role, extras


async def _assert_exclusive_ok(
    db: AsyncSession, project_id: int, role: str | None, exclude_user_id: int | None = None
) -> None:
    """排他岗位基数校验：同一项目内「总监 / 总监代表」各最多 1 名。

    ``MySQL`` 无部分唯一索引，故在代码层校验（设计说明 §7.8.3）。
    ``exclude_user_id`` 用于"修改本人岗位"时排除自己那条行。
    """
    if role not in ROLE_EXCLUSIVE:
        return
    query = select(ProjectMember.user_id).where(
        ProjectMember.project_id == project_id,
        ProjectMember.role == role,
        ProjectMember.left_at.is_(None),
    )
    if exclude_user_id is not None:
        query = query.where(ProjectMember.user_id != exclude_user_id)
    row = (await db.execute(query)).first()
    if row is not None:
        raise HTTPException(
            status_code=409,
            detail=f"该项目已有「{ROLE_LABELS.get(role, role)}」，排他岗位每项目仅 1 名",
        )


def _require_project_scope(manager: User, project_id: int) -> None:
    """成员管理的作用域判定（Step 2 改判据）。

    设计说明 §7.4 第 7 条：**持公司级 ``member_manage`` 即放行跨项目**
    （管理员由此获得全公司成员增删能力）；总监凭项目级同一 key，
    但作用域限**本项目**（须与当前上下文项目一致）。
    """
    if manager.has_permission("member_manage") and manager.is_org_level():
        return  # 公司级管理员：跨项目
    if not manager.has_permission("member_manage"):
        raise HTTPException(status_code=403, detail="无权管理项目成员")
    if manager.project_id != project_id:
        raise HTTPException(status_code=403, detail="只能管理自己所在项目的成员")


async def _get_project_or_404(db: AsyncSession, project_id: int) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _require_same_org(user: User, project: Project) -> None:
    """企业隔离：非空 ``org_id`` 的账号只能操作**本企业**项目。

    ``org_id`` 为空视为本企业历史数据（与 ``auth.export_users`` 的企业隔离口径一致），
    放行。超管通常 ``org_id`` 为空，天然不受限。用于结项 / 恢复这类公司级动作，
    防止多企业部署下 A 企业管理员对 B 企业项目「结项/恢复」。
    """
    if user.org_id is not None and project.org_id not in (None, user.org_id):
        raise HTTPException(status_code=403, detail="无权操作其他企业的项目")


@router.get("/{project_id}/members")
async def list_members(
    project_id: int,
    include_left: bool = False,
    manager: User = Depends(require_permission("member_manage")),
    db: AsyncSession = Depends(get_db),
):
    """项目成员列表（Step 2：权限 key 由 settings 拆为 member_manage）。

    公司级管理员看任意项目；总监限本项目。返回主岗 + 附加岗（岗位集合）。

    Step 5：默认只列**在岗**成员（``left_at IS NULL``）；传 ``include_left=true``
    则连带返回**已离场**成员，并可读到其在场区间（``joined_at`` ~ ``left_at``），
    用于"某人某段时间在哪个项目"的追溯。
    """
    await _get_project_or_404(db, project_id)
    _require_project_scope(manager, project_id)
    conditions = [ProjectMember.project_id == project_id]
    if not include_left:
        conditions.append(ProjectMember.left_at.is_(None))
    result = await db.execute(
        select(
            User.id, User.username, User.real_name, User.is_active,
            ProjectMember.role, ProjectMember.extra_roles, ProjectMember.joined_at,
            ProjectMember.left_at,
        )
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(*conditions)
        # 在岗（left_at 为 NULL）排在前，其余按加入时间
        .order_by(
            case((ProjectMember.left_at.is_(None), 0), else_=1),
            ProjectMember.joined_at,
        )
    )
    return {
        "project_id": project_id,
        "include_left": include_left,
        "items": [
            {
                "user_id": row.id,
                "username": row.username,
                "real_name": row.real_name,
                # 项目内真源：主岗 + 附加岗（不再返回全局 users.role）
                "role": row.role,
                "role_label": ROLE_LABELS.get(row.role or "", row.role),
                "extra_roles": row.extra_roles or [],
                # 岗位集合（主岗 + 附加岗），顺序稳定：主岗在前
                "roles": (
                    ([row.role] if row.role else []) + list(row.extra_roles or [])
                ),
                "joined_at": row.joined_at.isoformat() if row.joined_at else None,
                # Step 5：在场区间与在岗标记（离场成员靠这两项追溯）
                "left_at": row.left_at.isoformat() if row.left_at else None,
                "is_present": row.left_at is None,
                "is_active": row.is_active,
            }
            for row in result.all()
        ],
    }


@router.post("/{project_id}/members")
async def add_member(
    project_id: int,
    req: AddMemberRequest,
    manager: User = Depends(require_permission("member_manage")),
    db: AsyncSession = Depends(get_db),
):
    """把用户加入项目并指定项目内岗位（Step 2：支持主岗 + 附加岗）。

    - 公司级管理员可跨项目；总监限本项目；
    - **不得把操作者本人加为成员**（防自我提权，设计说明 §7.4 第 11 条）；
    - 排他岗位（总监/总监代表）每项目最多 1 名。
    """
    await _get_project_or_404(db, project_id)
    _require_project_scope(manager, project_id)

    # 防自我提权：本人不得通过本接口进入/改动自己的项目成员行
    if req.user_id == manager.id:
        raise HTTPException(
            status_code=403, detail="不能把自己加入项目或修改自己的项目岗位"
        )

    target = await db.get(User, req.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    role, extras = _validate_member_roles(req.role, req.extra_roles)
    # 未指定岗位时沿用其主岗（兼容旧调用，保持原行为）
    if role is None:
        role = target.role
    await _assert_exclusive_ok(db, project_id, role)

    existing = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == req.user_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # 曾在场（left_at 非空）→ 复职；已在场 → 幂等更新岗位
        existing.left_at = None
        existing.role = role
        existing.extra_roles = extras or None
        detail = "已更新项目岗位"
    else:
        db.add(ProjectMember(
            project_id=project_id, user_id=req.user_id,
            role=role, extra_roles=extras or None,
        ))
        detail = "已加入项目"

    _write_audit(
        db, manager, "project_member_add",
        f"项目 #{project_id} += 用户 #{req.user_id}({target.username}) 岗位={role}"
        + (f" 附加={extras}" if extras else ""),
    )
    if existing is not None:
        from app.services.permissions import permissions_for_roles
        from app.services.collaboration import cancel_action_items

        if 'document_approve' not in permissions_for_roles([role, *(extras or [])]):
            await cancel_action_items(
                db,
                project_id=project_id,
                recipient_id=req.user_id,
                action_type='approve',
                actor_id=manager.id,
                detail='项目岗位调整后不再具备审批权限',
            )
    from app.services.project_archive import sync_project_archive_members

    await db.flush()
    await sync_project_archive_members(db, project_id, manager.id)
    await db.commit()
    return {
        "detail": detail, "project_id": project_id,
        "user_id": req.user_id, "role": role, "extra_roles": extras,
    }


@router.delete("/{project_id}/members/{user_id}")
async def remove_member(
    project_id: int,
    user_id: int,
    manager: User = Depends(require_permission("member_manage")),
    db: AsyncSession = Depends(get_db),
):
    """把用户移出项目成员（**软删除**：写 ``left_at``，不再物理删除）。

    Step 5 改动（设计说明 §7.7④）：转场必须留痕 —— 物理删除会让
    "某人某段时间在哪个项目"彻底查不出来，与监理资料追溯、穗建通上报冲突。
    改为写 ``left_at`` 后，成员行保留完整在场区间（``joined_at`` ~ ``left_at``），
    有效成员 = ``left_at IS NULL``；重新加入时由 ``add_member`` 复职（清 ``left_at``）。

    副作用（保持不变）：若其当前上下文正停在本项目，一并清空上下文
    （下次需重新选项目）。
    """
    await _get_project_or_404(db, project_id)
    _require_project_scope(manager, project_id)
    # 防自我提权/自伤：本人不得移除自己（公司级管理员亦不例外，
    # 避免"管理员把自己移出后仍持跨项目权限"这类不一致；确需如此由超管操作）
    if user_id == manager.id:
        raise HTTPException(status_code=403, detail="不能移除自己")
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            # 只有**在岗**成员可被移出；已离场的重复移除直接 404，避免覆盖原离场时间
            ProjectMember.left_at.is_(None),
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="该用户不是项目在岗成员")
    target = await db.get(User, user_id)
    # Step 5：软删除 —— 保留成员行与在场区间
    member.left_at = datetime.now()
    if target is not None and target.project_id == project_id:
        target.project_id = None
    from app.services.collaboration import cancel_action_items
    from app.services.project_archive import sync_project_archive_members

    await cancel_action_items(
        db,
        project_id=project_id,
        recipient_id=user_id,
        actor_id=manager.id,
        detail="责任人已离开项目",
    )
    await sync_project_archive_members(db, project_id, manager.id)
    _write_audit(
        db, manager, "project_member_remove",
        f"项目 #{project_id} -= 用户 #{user_id}({getattr(target, 'username', '')}) "
        f"（软删除，离场时间 {member.left_at:%Y-%m-%d %H:%M}）",
    )
    await db.commit()
    return {
        "detail": "已移出项目（历史仍在场记录保留）",
        "project_id": project_id,
        "user_id": user_id,
        "left_at": member.left_at.isoformat(),
    }


# ============ 项目生命周期（Step 5：结项 / 恢复） ============


@router.post("/{project_id}/complete")
async def complete_project(
    project_id: int,
    request: Request,
    user: User = Depends(require_permission("project_complete")),
    db: AsyncSession = Depends(get_db),
):
    """项目**结项**（Step 5，设计说明 §7.7⑤）。

    结项是一个"批量收口"动作，一次做三件事：

    1. 置 ``projects.status='completed'`` 并写 ``completed_at``；
    2. 把本项目**全部在岗成员**置离场（写 ``left_at``）—— 转场留痕，不物理删除；
    3. 清空仍停留在本项目的用户上下文（``users.project_id``），避免其登录后
       落在已结项项目上。

    权限：``project_complete``（公司级，默认仅超管）。已结项项目重复调用幂等。
    """
    project = await _get_project_or_404(db, project_id)
    _require_same_org(user, project)
    current_status = project.status or PROJECT_STATUS_ACTIVE
    if current_status == PROJECT_STATUS_COMPLETED:
        return {
            "detail": "项目已是已完工状态，无需重复结项",
            "project_id": project_id,
            "status": current_status,
            "removed_members": 0,
            "cleared_contexts": 0,
            "completed_at": project.completed_at.isoformat() if project.completed_at else None,
            "changed": False,
        }

    now = datetime.now()
    # ① 批量置离场：所有 left_at IS NULL 的成员行
    members = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.left_at.is_(None),
            )
        )
    ).scalars().all()
    member_ids = [m.user_id for m in members]
    for member in members:
        member.left_at = now

    # ② 项目状态与结项时间
    project.status = PROJECT_STATUS_COMPLETED
    project.completed_at = now
    from app.services.collaboration import cancel_action_items
    from app.services.project_archive import sync_project_archive_members

    await cancel_action_items(
        db,
        project_id=project_id,
        actor_id=user.id,
        detail="项目已结项",
    )
    await sync_project_archive_members(db, project_id, user.id)

    # ③ 清空停留本项目的上下文（下次需重新选项目）
    cleared = 0
    if member_ids:
        staying = (
            await db.execute(
                select(User).where(
                    User.id.in_(member_ids), User.project_id == project_id
                )
            )
        ).scalars().all()
        for item in staying:
            item.project_id = None
            cleared += 1

    ip = await _client_ip(request)
    _write_audit(
        db, user, "project_complete",
        f"项目 #{project_id} {project.name} 结项："
        f"{len(members)} 名在岗成员置离场，{cleared} 人上下文清空",
        ip,
    )
    await db.commit()
    return {
        "detail": f"项目已结项，{len(members)} 名在岗成员已置离场",
        "project_id": project_id,
        "status": PROJECT_STATUS_COMPLETED,
        "removed_members": len(members),
        "cleared_contexts": cleared,
        "completed_at": now.isoformat(),
        "changed": True,
    }


@router.post("/{project_id}/reopen")
async def reopen_project(
    project_id: int,
    request: Request,
    user: User = Depends(require_permission("project_complete")),
    db: AsyncSession = Depends(get_db),
):
    """把已结项项目**恢复为进行中**（Step 5 误操作安全网）。

    只改项目状态、清空 ``completed_at``；**不自动恢复成员** —— 结项时成员已
    被置离场（``left_at`` 已写、历史已留痕），恢复后需在成员管理中重新任命。
    这样"恢复"不会抹掉结项这段历史，也不会猜错该给谁复职。

    权限同结项：``project_complete``。已在进行中的项目重复调用幂等。
    """
    project = await _get_project_or_404(db, project_id)
    _require_same_org(user, project)
    current_status = project.status or PROJECT_STATUS_ACTIVE
    if current_status == PROJECT_STATUS_ACTIVE:
        return {
            "detail": "项目已是进行中状态",
            "project_id": project_id,
            "status": PROJECT_STATUS_ACTIVE,
            "changed": False,
        }

    project.status = PROJECT_STATUS_ACTIVE
    project.completed_at = None
    from app.services.project_archive import sync_project_archive_members

    await sync_project_archive_members(db, project_id, user.id)
    ip = await _client_ip(request)
    _write_audit(
        db, user, "project_reopen",
        f"项目 #{project_id} {project.name} 恢复为进行中（原状态 {current_status}；"
        f"成员需重新任命）",
        ip,
    )
    await db.commit()
    return {
        "detail": "项目已恢复为进行中，请在成员管理中重新任命在岗人员",
        "project_id": project_id,
        "status": PROJECT_STATUS_ACTIVE,
        "changed": True,
    }


# ============ 空壳项目物理删除（2026-10-22） ============

# 这些表在项目删除时由接口**主动清理**，不计入"有业务数据"的拦截项：
#   project_members     —— 创建项目时自动把创建者加为成员，空壳项目必然有 1 条
#   users               —— users.project_id 只是"当前上下文"单值，置空即可
#   agent_*             —— 智能体记忆/会话/草稿属于派生数据，随项目删除
# 注：project_view_grants（跨项目查看授权）按「管理员」粒度存储，不绑定具体项目，
#     故项目删除时无需、也不能按 project_id 清理，已从本集合移除。
# 注：agent_user_preferences（个人记忆）**不**列入：若用户把个人记忆绑定到本项目，
#     删除项目不应静默删除用户的个人记忆，宁可拒绝删除并提示先解绑。
_CLEANUP_TABLES = {
    "project_members", "users",
    "agent_project_memories",
    "agent_project_memory_audit",
    "agent_conversation_messages",
    "agent_conversation_memory",
    "agent_conversation_drafts",
    "agent_drafts",
}

# fail-closed 阈值：生产 information_schema 应返回 25+ 张带 project_id 的表。
# 若扫到的表数少于此，说明判定依据本身失效（库连错/metadata 未加载），
# 此时**必须拒绝删除**，绝不能"没查到引用"就放行。
_MIN_REFERENCE_TABLES = 5


class DeleteProjectRequest(BaseModel):
    """删除请求体：必须回显项目编号，防止在长列表里点错行。"""
    confirm_code: str


async def _tables_with_project_id(db: AsyncSession) -> list[str]:
    """返回库里所有带 ``project_id`` 列的表名（**数据库真源优先**）。

    🔴 不能只依赖 ``Base.metadata``：ORM 元数据的内容取决于**哪些模型模块被
    import 过**。实测 ``from app.main import app`` 得到 53 张表，但**缺
    ``kb_chunks``**（``kb_chunk`` 模块只在 ``init_db()`` 里导入）—— 用元数据
    扫描会漏掉这类表，而"漏表"在本接口里等价于"放行删除"，留下指向不存在
    项目的孤儿数据（记忆里就有"kb_chunks 逻辑引用不清会 RAG 幻觉"的前车之鉴）。
    所以优先查 ``information_schema``（MySQL 真源），失败再回退元数据。
    """
    try:
        rows = (
            await db.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE column_name = 'project_id' AND table_schema = DATABASE()"
                )
            )
        ).all()
        names = sorted({r[0] for r in rows if r[0]})
        if names:
            return names
    except Exception:  # noqa: BLE001  SQLite 等无 information_schema 时回退
        pass

    from app.db import Base

    return sorted(t.name for t in Base.metadata.sorted_tables if "project_id" in t.columns)


def _project_file_refs(project_id: int, *subdirs: str) -> dict[str, int]:
    """统计**只落文件不入库**的目录里属于该项目的文件数。

    图纸 / 档案走的是「文件 + .meta.json」而非数据库表，FK 扫描看不见它们。
    若不清点就删项目，这些文件会变成指向不存在项目的孤儿。
    """
    import json

    hits: dict[str, int] = {}
    for sub in subdirs:
        d = settings.upload_dir / sub
        if not d.is_dir():
            continue
        count = 0
        for f in d.iterdir():
            if not f.is_file() or not f.name.endswith(".meta.json"):
                continue
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001  坏 meta 文件不参与判定
                continue
            if meta.get("project_id") == project_id:
                count += 1
        if count:
            hits[sub] = count
    return hits


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    req: DeleteProjectRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """**物理删除**空壳项目（建错/手滑场景，不走结项流程）。

    为什么不提供通用的项目删除：监理项目一旦产生巡视记录、验收资料、成员在场
    历史，物理删除会破坏责任追溯链，也和对外的穗建通上报口径对不上。有数据的
    项目应当走「结项」（软收口，可恢复）。本接口只收拾**从未产生业务数据**的
    空壳，因此门槛设得极严：

    1. 仅超级管理员（``_require_super``），不认 ``project_complete`` 之类的权限键；
    2. 请求体必须回显项目编号（``confirm_code``），大小写不敏感但内容必须一致；
    3. 不许删自己当前所在的项目（否则下面清空上下文会把操作者自己锁在项目外）；
    4. 以 **information_schema 为真源**枚举所有带 ``project_id`` 列的表，任一表
       存在该项目的记录即 409 拒绝 —— 用列名而非外键判定（部分表无 FK 约束），
       用库真源而非 ORM 元数据（元数据取决于 import 过哪些模型，会漏表）；
       枚举结果少于安全阈值时 **fail-closed**（500 中止），绝不"没查到就放行"；
    5. 图纸 / 档案目录（文件 + meta，不入库）单独清点，有文件即 409 拒绝，
       要求先删文件，避免留下指向不存在项目的孤儿文件。

    通过全部检查后才清理成员行、查看授权、用户当前上下文，最后删项目行，
    并写一条 ``project_delete`` 安全审计（含被清掉的成员名单，便于追责）。
    被拒的场景同样落 ``project_delete_denied`` 审计——抵赖不了。
    """
    _require_super(user)
    project = await _get_project_or_404(db, project_id)

    # 不许删自己当前所在的项目：下面会把 users.project_id 置空，操作者自己的
    # 上下文会跟着消失，之后所有写接口都会 400（require_project_id 拿不到项目）。
    # 先让他切走，避免"删完自己出不去"。
    if user.project_id == project_id:
        raise HTTPException(
            status_code=400,
            detail="不能删除自己当前所在的项目，请先切换到其他项目再删除",
        )

    if (req.confirm_code or "").strip().upper() != (project.code or "").strip().upper():
        _write_audit(
            db, user, "project_delete_denied",
            f"项目 #{project_id} {project.name} 删除失败：确认编号不匹配",
            await _client_ip(request),
        )
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"项目编号不匹配，请输入「{project.code}」以确认删除",
        )

    # ---- ③ 业务数据拦截：扫描所有指向本项目的表 ----
    # 判定口径用**列名 project_id** 而非"是否有 FK"：agent_draft.AgentFlowInstance、
    # kb_chunks 等表带 project_id 但**没有**外键约束，只扫 FK 会漏掉它们，
    # 删完留下指向不存在项目的孤儿数据。宁严勿松。
    ref_tables = [t for t in await _tables_with_project_id(db) if t not in _CLEANUP_TABLES]

    # 🔴 fail-closed：判定依据本身失效（库连错 / 元数据未加载 / information_schema
    # 不可用）时，"没扫到引用"不等于"没有引用"。宁可拒绝删除，不可误删。
    if len(ref_tables) < _MIN_REFERENCE_TABLES:
        _write_audit(
            db, user, "project_delete_denied",
            f"项目 #{project_id} {project.name} 删除被拒（引用表枚举异常，仅 {len(ref_tables)} 张，"
            f"低于安全阈值 {_MIN_REFERENCE_TABLES}）",
            await _client_ip(request),
        )
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail="无法完成引用检查（业务表枚举异常），为避免误删已中止。请联系管理员排查。",
        )

    blockers: list[str] = []
    for name in ref_tables:
        # 表名来自 information_schema，仍按标识符白名单校验后再拼接，杜绝注入
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            blockers.append(f"{name}（表名非法，无法检查）")
            continue
        count = (
            await db.execute(
                text(f'SELECT COUNT(*) FROM `{name}` WHERE project_id = :pid'),
                {"pid": project_id},
            )
        ).scalar() or 0
        if count:
            blockers.append(f"{name} {count} 条")

    # ---- ④ 图纸 / 档案文件拦截（不入库，FK 扫描看不见）----
    file_hits = _project_file_refs(project_id, "drawings", "archives")
    for sub, count in file_hits.items():
        blockers.append(f"{sub} 文件 {count} 个")

    if blockers:
        detail = "该项目已有业务数据，不能物理删除，请改用「结项」：" + "；".join(blockers)
        _write_audit(
            db, user, "project_delete_denied",
            f"项目 #{project_id} {project.name} 删除被拒（存在业务数据：{detail[:300]}）",
            await _client_ip(request),
        )
        await db.commit()
        raise HTTPException(status_code=409, detail=detail)

    # ---- 通过全部检查：清理归属关系后删除项目行 ----
    members = (
        await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id))
    ).scalars().all()
    member_names = []
    for m in members:
        mu = await db.get(User, m.user_id)
        if mu:
            member_names.append(f"{mu.real_name}({mu.username})")
    await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
    # 智能体派生数据（项目规约 / 会话 / 草稿）随项目一起清理；只删库里真实存在
    # 且列入 cleanup 的表，避免老库缺表时把删除流程打 500。
    cleanup_tables = [
        name for name in await _tables_with_project_id(db)
        if name in _CLEANUP_TABLES - {"project_members", "users"}
    ]
    for name in cleanup_tables:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        await db.execute(
            text(f"DELETE FROM `{name}` WHERE project_id = :pid"), {"pid": project_id},
        )
    cleared = (
        await db.execute(
            update(User).where(User.project_id == project_id).values(project_id=None)
        )
    ).rowcount or 0

    name, code = project.name, project.code
    await db.delete(project)
    _write_audit(
        db, user, "project_delete",
        f"项目 #{project_id} {name}（{code}）已物理删除："
        f"清理成员 {len(members)} 人[{','.join(member_names)[:200]}]、上下文 {cleared} 人",
        await _client_ip(request),
    )
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        # 兜底：上面的扫描依赖 ORM 元数据，若某张带 FK 的表未被任何模块 import
        # （不在 metadata 里）就会漏检。此时数据库的外键约束会拒绝删除并抛
        # IntegrityError —— 绝不能让它变成 500，也不能留下半删状态。
        await db.rollback()
        _write_audit(
            db, user, "project_delete_denied",
            f"项目 #{project_id} {name} 删除被数据库外键约束拒绝：{str(exc)[:300]}",
            await _client_ip(request),
        )
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail="该项目仍被其他数据引用，无法删除，请改用「结项」",
        ) from exc
    return {
        "detail": f"项目「{name}」已删除",
        "project_id": project_id,
        "removed_members": len(members),
        "cleared_contexts": cleared,
    }


# ============ 图纸管理 ============

ALLOWED_DRAWING_EXTS = {".pdf", ".dwg", ".dxf", ".jpg", ".jpeg", ".png"}


def _drawing_dir() -> Path:
    d = settings.upload_dir / "drawings"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/drawings/list")
async def list_drawings(
    keyword: Optional[str] = None,
    category: Optional[str] = None,
    user: User = Depends(get_current_user),
):
    """列出图纸文件"""
    d = _drawing_dir()
    items = []
    for f in d.iterdir():
        if not f.is_file() or f.name.endswith(".meta.json"):
            continue
        meta_path = f.with_suffix(f.suffix + ".meta.json")
        meta = {}
        if meta_path.exists():
            import json
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not user.is_super_admin() and meta.get("project_id") != user.project_id:
            continue
        name = meta.get("name", f.stem)
        cat = meta.get("category", "其他")
        if keyword and keyword not in name:
            continue
        if category and cat != category:
            continue
        items.append({
            "id": meta.get("id", f.stem),
            "name": name,
            "category": cat,
            "version": meta.get("version", ""),
            "file_size": f.stat().st_size,
            "file_url": f"/uploads/drawings/{f.name}",
            "created_at": meta.get("created_at", ""),
        })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return {"total": len(items), "items": items}


@router.post("/drawings/upload")
async def upload_drawing(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    category: str = Form("其他"),
    version: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    """上传图纸"""
    project_id = require_project_id(user)
    if not file.filename:
        raise HTTPException(400, "未选择文件")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_DRAWING_EXTS:
        raise HTTPException(400, f"不支持的文件类型: {ext}")
    data = await _read_upload_limited(file)

    d = _drawing_dir()
    base = _safe_base_name(name or Path(file.filename).stem)
    saved_name = f"{base}{ext}"
    counter = 1
    while (d / saved_name).exists():
        saved_name = f"{base}_{counter}{ext}"
        counter += 1
    target = d / saved_name
    target.write_bytes(data)

    # 保存元信息
    import json
    meta = {
        "id": f"dwg_{int(time.time())}",
        "name": name or Path(file.filename).stem,
        "category": category,
        "version": version or "",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project_id": project_id,
    }
    target.with_suffix(target.suffix + ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return {"success": True, "name": meta["name"], "file_url": f"/uploads/drawings/{saved_name}"}


@router.delete("/drawings/{drawing_id}")
async def delete_drawing(
    drawing_id: str,
    user: User = Depends(require_role("总监理工程师", "总监代表")),
):
    """删除图纸"""
    d = _drawing_dir()
    deleted = False
    for f in d.iterdir():
        if not f.is_file() or f.name.endswith(".meta.json"):
            continue
        meta = f.with_suffix(f.suffix + ".meta.json")
        data = {}
        if meta.exists():
            import json
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if not (
            f.stem == drawing_id
            or f.name == drawing_id
            or data.get("id") == drawing_id
        ):
            continue
        if not user.is_super_admin() and data.get("project_id") != user.project_id:
            raise HTTPException(404, "图纸不存在")
        f.unlink()
        if meta.exists():
            meta.unlink()
        deleted = True
        break
    if not deleted:
        raise HTTPException(404, "图纸不存在")
    return {"detail": "已删除"}


# ============ 档案管理 ============

ALLOWED_ARCHIVE_EXTS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".jpg", ".jpeg", ".png"}


def _archive_dir() -> Path:
    d = settings.upload_dir / "archives"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/archives/list")
async def list_archives(
    keyword: Optional[str] = None,
    archive_type: Optional[str] = None,
    user: User = Depends(get_current_user),
):
    """列出档案文件"""
    d = _archive_dir()
    items = []
    for f in d.iterdir():
        if not f.is_file() or f.name.endswith(".meta.json"):
            continue
        meta_path = f.with_suffix(f.suffix + ".meta.json")
        meta = {}
        if meta_path.exists():
            import json
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not user.is_super_admin() and meta.get("project_id") != user.project_id:
            continue
        name = meta.get("name", f.stem)
        atype = meta.get("archive_type", "其他")
        if keyword and keyword not in name:
            continue
        if archive_type and atype != archive_type:
            continue
        items.append({
            "id": meta.get("id", f.stem),
            "name": name,
            "archive_type": atype,
            "archive_no": meta.get("archive_no", ""),
            "file_size": f.stat().st_size,
            "file_url": f"/uploads/archives/{f.name}",
            "archived_at": meta.get("archived_at", ""),
        })
    items.sort(key=lambda x: x["archived_at"], reverse=True)
    return {"total": len(items), "items": items}


@router.post("/archives/upload")
async def upload_archive(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    archive_type: str = Form("其他"),
    archive_no: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    """归档文件"""
    project_id = require_project_id(user)
    if not file.filename:
        raise HTTPException(400, "未选择文件")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_ARCHIVE_EXTS:
        raise HTTPException(400, f"不支持的文件类型: {ext}")
    data = await _read_upload_limited(file)

    d = _archive_dir()
    base = _safe_base_name(name or Path(file.filename).stem)
    saved_name = f"{base}{ext}"
    counter = 1
    while (d / saved_name).exists():
        saved_name = f"{base}_{counter}{ext}"
        counter += 1
    target = d / saved_name
    target.write_bytes(data)

    import json
    meta = {
        "id": f"arc_{int(time.time())}",
        "name": name or Path(file.filename).stem,
        "archive_type": archive_type,
        "archive_no": archive_no or "",
        "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project_id": project_id,
    }
    target.with_suffix(target.suffix + ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return {"success": True, "name": meta["name"], "file_url": f"/uploads/archives/{saved_name}"}


@router.delete("/archives/{archive_id}")
async def delete_archive(
    archive_id: str,
    user: User = Depends(require_role("总监理工程师", "总监代表")),
):
    """删除档案"""
    d = _archive_dir()
    deleted = False
    for f in d.iterdir():
        if not f.is_file() or f.name.endswith(".meta.json"):
            continue
        meta = f.with_suffix(f.suffix + ".meta.json")
        data = {}
        if meta.exists():
            import json
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if not (
            f.stem == archive_id
            or f.name == archive_id
            or data.get("id") == archive_id
        ):
            continue
        if not user.is_super_admin() and data.get("project_id") != user.project_id:
            raise HTTPException(404, "档案不存在")
        f.unlink()
        if meta.exists():
            meta.unlink()
        deleted = True
        break
    if not deleted:
        raise HTTPException(404, "档案不存在")
    return {"detail": "已删除"}
