"""项目管理路由 - 创建/查询/切换项目 + 图纸/档案管理"""
import os
import time
import shutil
import re
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, or_, select
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
    location: Optional[str] = None
    description: Optional[str] = None
    construction_unit: Optional[str] = None
    supervision_unit: Optional[str] = None
    supervision_org: Optional[str] = None
    acceptance_participants: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    seal_signature_id: Optional[int] = None



class UpdateProjectRequest(BaseModel):
    """修改项目基本信息（仅传需要改的字段）"""
    name: Optional[str] = None
    short_name: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    construction_unit: Optional[str] = None
    supervision_unit: Optional[str] = None
    supervision_org: Optional[str] = None
    acceptance_participants: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    seal_signature_id: Optional[int] = None



class ProjectInfo(BaseModel):
    id: int
    name: str
    code: str
    short_name: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    construction_unit: Optional[str] = None
    supervision_unit: Optional[str] = None
    supervision_org: Optional[str] = None
    acceptance_participants: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    seal_signature_id: Optional[int] = None

    # Step 5：项目生命周期状态（active=进行中 / completed=已完工 / archived=已归档）
    status: str = "active"
    # 项目结束时间（结项动作写入；NULL=未结项）
    completed_at: Optional[str] = None


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
        location=project.location,
        description=project.description,
        construction_unit=project.construction_unit,
        supervision_unit=project.supervision_unit,
        supervision_org=project.supervision_org,
        acceptance_participants=project.acceptance_participants,
        latitude=project.latitude,
        longitude=project.longitude,
        seal_signature_id=project.seal_signature_id,

        status=project.status or PROJECT_STATUS_ACTIVE,
        completed_at=project.completed_at.isoformat() if project.completed_at else None,
    )


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
    return [_project_info(p) for p in projects]


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
      - **范围闸**：公司级（超管 / 公司级管理员）可改**任意**项目；项目级只能
        改本人为**有效成员**的项目。归属真源是 ``project_members``（``left_at
        IS NULL``），**不是** ``users.project_id`` 单值上下文 —— 原实现用
        ``is_super_admin()``，既漏掉了公司级管理员跨项目，也把多项目负责人的
        可改范围错误地收窄成一个项目。
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
    for key in ("construction_unit", "supervision_unit", "supervision_org", "acceptance_participants", "latitude", "longitude", "seal_signature_id"):
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
            # 2026-10-20：超管授权的跨项目查看，同样允许切入
            grant = await db.execute(
                select(ProjectViewGrant.id).where(
                    ProjectViewGrant.grantee_user_id == user.id,
                    ProjectViewGrant.project_id == project_id,
                )
            )
            if grant.first() is None:
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
    """当前用户被超管授权查看的**额外**项目 id 列表（与成员项目解耦）。"""
    rows = (
        await db.execute(
            select(ProjectViewGrant.project_id).where(
                ProjectViewGrant.grantee_user_id == user_id
            )
        )
    ).scalars().all()
    return list(rows)


class ViewGrantRequest(BaseModel):
    """授权某管理员查看某项目。"""
    grantee_user_id: int
    project_id: int


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
    """授权总览：全部用户（筛管理员）、全部项目、全部授权记录。"""
    _require_super(user)
    users = (
        await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.id))
    ).scalars().all()
    projects = (await db.execute(select(Project).order_by(Project.id))).scalars().all()
    grants = (await db.execute(select(ProjectViewGrant))).scalars().all()
    return {
        "admins": [
            {
                "id": u.id,
                "real_name": u.real_name,
                "username": u.username,
                "org_role": u.org_role,
                "project_id": u.project_id,
            }
            for u in users
        ],
        "projects": [
            {"id": p.id, "name": p.name, "status": p.status or "active"}
            for p in projects
        ],
        "grants": [
            {
                "grantee_user_id": g.grantee_user_id,
                "project_id": g.project_id,
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
    """授权某管理员查看某项目（幂等）。"""
    _require_super(user)
    target = await db.get(User, req.grantee_user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.org_role != ORG_ROLE_ADMIN:
        raise HTTPException(status_code=400, detail="被授权人必须是管理员（项目负责人）")
    project = await db.get(Project, req.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    existing = (
        await db.execute(
            select(ProjectViewGrant).where(
                ProjectViewGrant.grantee_user_id == req.grantee_user_id,
                ProjectViewGrant.project_id == req.project_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            ProjectViewGrant(
                grantee_user_id=req.grantee_user_id,
                project_id=req.project_id,
                grantor_user_id=user.id,
            )
        )
        await db.commit()
    db.add(
        SecurityAudit(
            user_id=user.id,
            username=user.username,
            action="project_view_grant",
            detail=f"授权 用户#{req.grantee_user_id} 查看项目#{req.project_id}",
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
    """撤销某管理员的某项目查看授权。"""
    _require_super(user)
    existing = (
        await db.execute(
            select(ProjectViewGrant).where(
                ProjectViewGrant.grantee_user_id == req.grantee_user_id,
                ProjectViewGrant.project_id == req.project_id,
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
            detail=f"撤销 用户#{req.grantee_user_id} 查看项目#{req.project_id}",
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
