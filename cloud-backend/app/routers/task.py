"""派单路由 — 总监/总代派单给工程师/监理员"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from pathlib import Path
import json
import uuid

from app.config import settings
from app.db import get_db
from app.models.user import User
from app.models.task_assignment import TaskAssignment, TaskStatus, TaskPriority, TaskCategory
from app.routers.auth import (
    get_current_user,
    require_permission,
    require_project_id,
    project_scope_id,
    can_read_project,
)
from app.services.media_validation import assert_valid_image

router = APIRouter()


class CreateTaskRequest(BaseModel):
    """创建派单请求"""
    title: str
    description: Optional[str] = None
    priority: str = "中"
    category: str = "其他事务"
    assignee_id: int  # 被派人ID
    project_id: Optional[int] = None
    deadline: Optional[datetime] = None
    location: Optional[str] = None
    section: Optional[str] = None
    attachments: Optional[list[str]] = None  # 附件 URL 列表
    notify_glasses: bool = True


class AcceptTaskRequest(BaseModel):
    """接单/拒单请求"""
    action: str  # accept / reject
    reject_reason: Optional[str] = None


class CompleteTaskRequest(BaseModel):
    """完成任务请求（含回执）"""
    completion_remark: Optional[str] = None
    result_files: Optional[list[str]] = None  # 任务成果文件 URL 列表


class ReassignTaskRequest(BaseModel):
    """转派任务请求"""
    new_assignee_id: int
    reason: Optional[str] = None


async def _finish_task(
    task: TaskAssignment,
    user: User,
    db: AsyncSession,
    *,
    completion_remark: str | None,
    result_files: list[str] | None,
) -> dict:
    if task.status != TaskStatus.IN_PROGRESS.value:
        raise HTTPException(400, f"任务当前状态为「{task.status}」，无法完成")
    task.status = TaskStatus.COMPLETED.value
    task.completed_at = datetime.now()
    if completion_remark:
        task.completion_remark = completion_remark
    if result_files:
        try:
            existing = json.loads(task.attachments or "[]")
        except (TypeError, json.JSONDecodeError):
            existing = []
        task.attachments = json.dumps(
            list(dict.fromkeys([*existing, *result_files])),
            ensure_ascii=False,
        )
    await db.commit()

    try:
        from app.services.notification import push_to_glasses, push_realtime
        await push_to_glasses(
            user_id=task.assigner_id,
            title=f"派单已完成：{task.title}",
            body=f"完成人：{user.real_name}",
            payload={"task_id": task.id, "type": "task_complete"},
        )
        await push_realtime(
            user_id=task.assigner_id,
            event_type="task_completed",
            data={
                "task_id": task.id,
                "title": task.title,
                "completed_by": user.real_name,
            },
        )
    except Exception:
        pass
    return {"detail": "任务已完成"}


@router.post("")
async def create_task(
    req: CreateTaskRequest,
    user: User = Depends(require_permission("task_assign")),
    db: AsyncSession = Depends(get_db),
):
    """派单：总监/总代 → 工程师/监理员"""
    project_id = require_project_id(user)
    if req.project_id is not None and req.project_id != project_id:
        raise HTTPException(403, "只能在当前项目内派单")
    # 验证被派人存在，且是**本项目**成员并持有 task_receive 权限
    # Step 2（设计说明 §7.8.4）：归属与岗位都改从成员表取 —— 旧写法用
    # ``assignee.project_id``（上下文）判归属、用全局 ``assignee.role``
    # （单值）判岗位，跨项目兼职者会被误判。
    result = await db.execute(select(User).where(User.id == req.assignee_id))
    assignee = result.scalar_one_or_none()
    if not assignee:
        raise HTTPException(404, "被指派用户不存在")

    from app.services.permissions import member_role_codes, permissions_for_roles

    assignee_roles = await member_role_codes(db, assignee.id, project_id)
    if not assignee_roles:
        raise HTTPException(400, "被指派用户不属于当前项目")
    if "task_receive" not in permissions_for_roles(assignee_roles):
        raise HTTPException(400, "被指派用户在该项目的岗位无法接收任务")

    # 校验类别
    valid_categories = {c.value for c in TaskCategory}
    if req.category not in valid_categories:
        raise HTTPException(400, f"派单类别非法：{req.category}")

    # 校验优先级
    valid_priorities = {p.value for p in TaskPriority}
    if req.priority not in valid_priorities:
        raise HTTPException(400, f"优先级非法：{req.priority}")

    import json as _json
    task = TaskAssignment(
        title=req.title,
        description=req.description,
        status=TaskStatus.PENDING.value,
        priority=req.priority,
        category=req.category,
        assigner_id=user.id,
        assignee_id=req.assignee_id,
        project_id=project_id,
        deadline=req.deadline,
        location=req.location,
        section=req.section,
        attachments=_json.dumps(req.attachments, ensure_ascii=False) if req.attachments else None,
        notify_glasses=req.notify_glasses,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 异步推送通知（眼镜端）
    if req.notify_glasses:
        try:
            from app.services.notification import push_to_glasses, push_realtime, push_to_wechat
            await push_to_glasses(
                user_id=req.assignee_id,
                title=f"新派单：{req.title}",
                body=f"发起人：{user.real_name}，请尽快接单",
                payload={"task_id": task.id, "type": "task_assign"},
            )
            await push_realtime(
                user_id=req.assignee_id,
                event_type="task_assigned",
                data={
                    "task_id": task.id,
                    "title": req.title,
                    "assigner": user.real_name,
                    "priority": req.priority,
                    "category": req.category,
                },
            )
            # 微信服务通知
            await push_to_wechat(
                db,
                user_id=req.assignee_id,
                template_key="task_assigned",
                data={
                    "thing1": {"value": req.title[:20]},
                    "thing2": {"value": user.real_name},
                    "phrase3": {"value": req.priority},
                },
                page=f"pages/task/detail?id={task.id}",
            )
        except Exception:
            pass  # 通知失败不影响派单

    return {"detail": "派单成功", "task_id": task.id}


@router.get("/assignable-users")
async def get_assignable_users(
    user: User = Depends(require_permission("task_assign")),
    db: AsyncSession = Depends(get_db),
):
    """获取可被指派用户列表（工程师/监理员）

    Step 2（设计说明 §7.8.4）：改按**本项目成员表的岗位集合**查人。
    旧写法 ``User.project_id == pid AND User.role IN (...)`` 有两个问题：
    ① 用的是全局单值角色，跨项目兼职者在别的项目仍会按总监身份出现；
    ② 岗位只看 ``users.role``，看不到附加岗位。现在按成员归属查、按
    主岗 ∪ 附加岗匹配，返回的 ``role`` 也改成该项目内实际持有的岗位。
    """
    project_id = require_project_id(user)
    from app.services.permissions import project_members_with_permission

    rows = await project_members_with_permission(db, project_id, "task_receive")
    return [
        {
            "id": u.id,
            "username": u.username,
            "real_name": u.real_name,
            "role": m.role,
            "roles": m.role_codes(),
            "phone": u.phone,
        }
        for m, u in rows
    ]


@router.get("/my-tasks")
async def get_my_tasks(
    status: Optional[str] = None,
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取我的任务列表（作为接收人）"""
    scope = project_scope_id(user, all_projects)
    query = select(TaskAssignment).where(TaskAssignment.assignee_id == user.id)
    if scope is not None:
        query = query.where(TaskAssignment.project_id == scope)
    if status:
        query = query.where(TaskAssignment.status == status)
    query = query.order_by(TaskAssignment.created_at.desc())
    result = await db.execute(query)
    tasks = result.scalars().all()

    # 解析 assignee/assigner 名字
    task_dicts = [_task_to_dict(t) for t in tasks]
    user_ids = {t["assigner_id"] for t in task_dicts}
    if user_ids:
        user_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u.real_name for u in user_result.scalars().all()}
        for td in task_dicts:
            td["assigner_name"] = users_map.get(td["assigner_id"], "未知")

    # 当前用户有派单权限时，附带给可派人列表（派单目标始终是「本项目」，不受全站视角影响）
    assignable_users = []
    if user.has_permission("task_assign") and user.project_id:
        # Step 2：同上，按本项目成员表的岗位集合查人
        from app.services.permissions import project_members_with_permission

        rows = await project_members_with_permission(db, user.project_id, "task_receive")
        assignable_users = [
            {"id": u.id, "real_name": u.real_name, "role": m.role, "roles": m.role_codes()}
            for m, u in rows
        ]

    return {
        "total": len(tasks),
        "tasks": task_dicts,
        "assignable_users": assignable_users,
    }


@router.get("/assigned-by-me")
async def get_assigned_by_me(
    status: Optional[str] = None,
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(require_permission("task_assign")),
    db: AsyncSession = Depends(get_db),
):
    """获取我派发的任务列表（作为派单人）"""
    scope = project_scope_id(user, all_projects)
    query = select(TaskAssignment).where(TaskAssignment.assigner_id == user.id)
    if scope is not None:
        query = query.where(TaskAssignment.project_id == scope)
    if status:
        query = query.where(TaskAssignment.status == status)
    query = query.order_by(TaskAssignment.created_at.desc())
    result = await db.execute(query)
    tasks = result.scalars().all()

    task_dicts = [_task_to_dict(t) for t in tasks]
    user_ids = {t["assignee_id"] for t in task_dicts if t.get("assignee_id")}
    if user_ids:
        user_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u.real_name for u in user_result.scalars().all()}
        for td in task_dicts:
            td["assignee_name"] = users_map.get(td["assignee_id"], "未指派")
    return {"total": len(tasks), "tasks": task_dicts}


@router.get("/todo-stats")
async def get_todo_stats(
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """待办中心统计（各状态数量）"""
    scope = project_scope_id(user, all_projects)
    # 我收到的待处理
    received_query = select(TaskAssignment).where(TaskAssignment.assignee_id == user.id)
    if scope is not None:
        received_query = received_query.where(TaskAssignment.project_id == scope)
    received_result = await db.execute(received_query)
    received_tasks = received_result.scalars().all()

    stats = {
        "pending_receive": sum(1 for t in received_tasks if t.status == TaskStatus.PENDING.value),
        "in_progress": sum(1 for t in received_tasks if t.status == TaskStatus.IN_PROGRESS.value),
        "completed": sum(1 for t in received_tasks if t.status == TaskStatus.COMPLETED.value),
        "rejected": sum(1 for t in received_tasks if t.status == TaskStatus.REJECTED.value),
        "total_received": len(received_tasks),
    }

    # 如果有派单权限，加上我派发的统计
    if user.has_permission("task_assign"):
        assigned_query = select(TaskAssignment).where(TaskAssignment.assigner_id == user.id)
        if scope is not None:
            assigned_query = assigned_query.where(TaskAssignment.project_id == scope)
        assigned_result = await db.execute(assigned_query)
        assigned_tasks = assigned_result.scalars().all()
        stats["total_assigned"] = len(assigned_tasks)

    return stats


@router.get("/notifications")
async def get_my_notifications(
    since_ts: Optional[float] = None,
    user: User = Depends(get_current_user),
):
    """拉取当前用户的实时通知（数据库持久化 + 内存缓存）"""
    from app.services.notification import get_user_notifications_db
    msgs = await get_user_notifications_db(user.id, since_ts)
    return {"total": len(msgs), "notifications": msgs}


@router.post("/notifications/clear")
async def clear_my_notifications(user: User = Depends(get_current_user)):
    """清空当前用户的通知"""
    from app.services.notification import clear_user_notifications_db, clear_user_notifications
    n_db = await clear_user_notifications_db(user.id)
    n_mem = clear_user_notifications(user.id)
    return {"detail": f"已清空 {max(n_db, n_mem)} 条通知"}


@router.get("/{task_id}")
async def get_task_detail(
    task_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取任务详情"""
    scope = project_scope_id(user, all_projects)
    stmt = select(TaskAssignment).where(TaskAssignment.id == task_id)
    if scope is not None:
        stmt = stmt.where(TaskAssignment.project_id == scope)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task or (task.assignee_id != user.id and task.assigner_id != user.id):
        raise HTTPException(404, "任务不存在或无权查看")
    return _task_to_dict(task)


@router.post("/{task_id}/respond")
async def respond_task(
    task_id: int,
    req: AcceptTaskRequest,
    user: User = Depends(require_permission("task_receive")),
    db: AsyncSession = Depends(get_db),
):
    """接单/拒单"""
    # 动作类接口按「这条任务是不是我的」判定，不再绑当前项目上下文：
    # 超管在「全部项目」视角下也要能处理其他项目派给自己的任务；
    # 写入归属由任务自身 project_id 决定，不存在歧义。
    result = await db.execute(
        select(TaskAssignment).where(TaskAssignment.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task or task.assignee_id != user.id:
        raise HTTPException(404, "任务不存在或无权操作")
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(400, "该任务已不在待接单状态")

    if req.action == "accept":
        task.status = TaskStatus.ACCEPTED.value
        task.accepted_at = datetime.now()
        detail = "已接单"
    elif req.action == "reject":
        task.status = TaskStatus.REJECTED.value
        task.reject_reason = req.reject_reason
        detail = "已拒单"
    else:
        raise HTTPException(400, "action 必须为 accept 或 reject")

    await db.commit()
    return {"detail": detail}


@router.post("/{task_id}/start")
async def start_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """开始执行任务"""
    # 动作类接口按「这条任务是不是我的」判定，不再绑当前项目上下文：
    # 超管在「全部项目」视角下也要能处理其他项目派给自己的任务；
    # 写入归属由任务自身 project_id 决定，不存在歧义。
    result = await db.execute(
        select(TaskAssignment).where(TaskAssignment.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task or task.assignee_id != user.id:
        raise HTTPException(404, "任务不存在或无权操作")
    if task.status not in (TaskStatus.ACCEPTED.value,):
        raise HTTPException(400, "请先接单再开始执行")
    task.status = TaskStatus.IN_PROGRESS.value
    task.started_at = datetime.now()
    await db.commit()
    return {"detail": "任务已开始"}


@router.post("/{task_id}/complete-with-files")
async def complete_task_with_files(
    task_id: int,
    completion_remark: str = Form(""),
    result_files: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """完成任务并原子上传成果附件，供移动端 multipart 流程使用。"""
    # 动作类接口按「这条任务是不是我的」判定，不再绑当前项目上下文：
    # 超管在「全部项目」视角下也要能处理其他项目派给自己的任务；
    # 写入归属由任务自身 project_id 决定，不存在歧义。
    result = await db.execute(
        select(TaskAssignment).where(TaskAssignment.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task or task.assignee_id != user.id:
        raise HTTPException(404, "任务不存在或无权操作")
    if task.status != TaskStatus.IN_PROGRESS.value:
        raise HTTPException(400, f"任务当前状态为「{task.status}」，无法完成")
    if len(result_files) > 10:
        raise HTTPException(400, "成果附件单次最多上传 10 个")

    allowed_exts = {
        ".jpg", ".jpeg", ".png", ".webp",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt",
    }
    directory = settings.upload_dir / "task-results" / str(task.id)
    directory.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    public_urls: list[str] = []
    try:
        for upload in result_files:
            ext = Path(upload.filename or "").suffix.lower()
            if ext not in allowed_exts:
                raise HTTPException(400, f"不支持的成果附件类型：{ext or '未知'}")
            data = await upload.read(settings.max_document_bytes + 1)
            await upload.close()
            if not data:
                raise HTTPException(400, "成果附件不能为空")
            if len(data) > settings.max_document_bytes:
                raise HTTPException(413, "成果附件超过大小限制")
            if ext in {".jpg", ".jpeg", ".png", ".webp"}:
                assert_valid_image(data)
            path = directory / f"{uuid.uuid4().hex}{ext}"
            path.write_bytes(data)
            saved_paths.append(path)
            public_urls.append(f"/uploads/task-results/{task.id}/{path.name}")
        return await _finish_task(
            task,
            user,
            db,
            completion_remark=completion_remark,
            result_files=public_urls,
        )
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise


@router.post("/{task_id}/complete")
async def complete_task(
    task_id: int,
    req: CompleteTaskRequest = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """完成任务（支持回执与成果文件）"""
    # 动作类接口按「这条任务是不是我的」判定，不再绑当前项目上下文：
    # 超管在「全部项目」视角下也要能处理其他项目派给自己的任务；
    # 写入归属由任务自身 project_id 决定，不存在歧义。
    result = await db.execute(
        select(TaskAssignment).where(TaskAssignment.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task or task.assignee_id != user.id:
        raise HTTPException(404, "任务不存在或无权操作")
    return await _finish_task(
        task,
        user,
        db,
        completion_remark=req.completion_remark if req else None,
        result_files=req.result_files if req else None,
    )


@router.post("/{task_id}/reassign")
async def reassign_task(
    task_id: int,
    req: ReassignTaskRequest,
    user: User = Depends(require_permission("task_assign")),
    db: AsyncSession = Depends(get_db),
):
    """转派任务（仅派单人可操作）"""
    # 动作类接口按「这条任务是不是我的」判定，不再绑当前项目上下文：
    # 超管在「全部项目」视角下也要能处理其他项目派给自己的任务；
    # 写入归属由任务自身 project_id 决定，不存在歧义。
    result = await db.execute(
        select(TaskAssignment).where(TaskAssignment.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task or task.assigner_id != user.id:
        raise HTTPException(404, "任务不存在或无权操作")
    if task.status in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
        raise HTTPException(400, "已结束的任务无法转派")

    new_user = await db.execute(select(User).where(User.id == req.new_assignee_id))
    new_assignee = new_user.scalar_one_or_none()
    if not new_assignee:
        raise HTTPException(404, "新被指派人不存在")
    # Step 2：同 create_task，判据改成员表
    from app.services.permissions import member_role_codes, permissions_for_roles

    new_roles = await member_role_codes(db, new_assignee.id, task.project_id)
    if not new_roles:
        raise HTTPException(400, "新被指派人不属于当前项目")
    if "task_receive" not in permissions_for_roles(new_roles):
        raise HTTPException(400, f"用户 {new_assignee.real_name} 在该项目的岗位无法接收任务")

    old_assignee_id = task.assignee_id
    task.assignee_id = req.new_assignee_id
    # 转派后状态回到 待接单
    task.status = TaskStatus.PENDING.value
    task.accepted_at = None
    task.reject_reason = (task.reject_reason or "") + (
        f"\n[转派 {datetime.now().strftime('%Y-%m-%d %H:%M')}] {req.reason or ''}"
    )
    await db.commit()

    # 通知新被指派人
    try:
        from app.services.notification import push_to_glasses, push_realtime
        await push_to_glasses(
            user_id=req.new_assignee_id,
            title=f"任务转派：{task.title}",
            body=f"{user.real_name} 将该任务转派给您",
            payload={"task_id": task.id, "type": "task_reassign"},
        )
        await push_realtime(
            user_id=req.new_assignee_id,
            event_type="task_reassigned",
            data={
                "task_id": task.id,
                "title": task.title,
                "reassigned_by": user.real_name,
                "old_assignee_id": old_assignee_id,
            },
        )
    except Exception:
        pass
    return {"detail": f"已转派给 {new_assignee.real_name}", "old_assignee_id": old_assignee_id}


@router.post("/{task_id}/urge")
async def urge_task(
    task_id: int,
    user: User = Depends(require_permission("task_assign")),
    db: AsyncSession = Depends(get_db),
):
    """催办任务（仅派单人可操作）"""
    # 动作类接口按「这条任务是不是我的」判定，不再绑当前项目上下文：
    # 超管在「全部项目」视角下也要能处理其他项目派给自己的任务；
    # 写入归属由任务自身 project_id 决定，不存在歧义。
    result = await db.execute(
        select(TaskAssignment).where(TaskAssignment.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task or task.assigner_id != user.id:
        raise HTTPException(404, "任务不存在或无权操作")
    if task.status not in (TaskStatus.PENDING.value, TaskStatus.ACCEPTED.value, TaskStatus.IN_PROGRESS.value):
        raise HTTPException(400, "该任务无需催办")

    if task.assignee_id:
        try:
            from app.services.notification import push_to_glasses, push_realtime, push_to_wechat
            await push_to_glasses(
                user_id=task.assignee_id,
                title=f"【催办】{task.title}",
                body=f"{user.real_name} 催办该任务，请尽快处理",
                payload={"task_id": task.id, "type": "task_urge"},
            )
            await push_realtime(
                user_id=task.assignee_id,
                event_type="task_urged",
                data={
                    "task_id": task.id,
                    "title": task.title,
                    "urged_by": user.real_name,
                },
            )
            # 微信服务通知
            await push_to_wechat(
                db,
                user_id=task.assignee_id,
                template_key="task_urged",
                data={
                    "thing1": {"value": task.title[:20]},
                    "thing2": {"value": user.real_name},
                },
                page=f"pages/task/detail?id={task.id}",
            )
        except Exception:
            pass
    return {"detail": "已发送催办通知"}


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    user: User = Depends(require_permission("task_assign")),
    db: AsyncSession = Depends(get_db),
):
    """取消派单（仅派单人可操作）"""
    # 动作类接口按「这条任务是不是我的」判定，不再绑当前项目上下文：
    # 超管在「全部项目」视角下也要能处理其他项目派给自己的任务；
    # 写入归属由任务自身 project_id 决定，不存在歧义。
    result = await db.execute(
        select(TaskAssignment).where(TaskAssignment.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task or task.assigner_id != user.id:
        raise HTTPException(404, "任务不存在或无权操作")
    if task.status in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
        raise HTTPException(400, "该任务无法取消")
    task.status = TaskStatus.CANCELLED.value
    await db.commit()

    # 通知被指派人
    if task.assignee_id:
        try:
            from app.services.notification import push_to_glasses, push_realtime
            await push_to_glasses(
                user_id=task.assignee_id,
                title=f"派单已取消：{task.title}",
                body=f"{user.real_name} 取消了该派单",
                payload={"task_id": task.id, "type": "task_cancel"},
            )
            await push_realtime(
                user_id=task.assignee_id,
                event_type="task_cancelled",
                data={
                    "task_id": task.id,
                    "title": task.title,
                    "cancelled_by": user.real_name,
                },
            )
        except Exception:
            pass
    return {"detail": "任务已取消"}


def _task_to_dict(task: TaskAssignment) -> dict:
    import json as _json
    attachments = None
    if task.attachments:
        try:
            attachments = _json.loads(task.attachments)
        except Exception:
            attachments = [task.attachments]
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "category": task.category,
        "assigner_id": task.assigner_id,
        "assignee_id": task.assignee_id,
        "project_id": task.project_id,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "location": task.location,
        "section": task.section,
        "attachments": attachments,
        "notify_glasses": task.notify_glasses,
        "accepted_at": task.accepted_at.isoformat() if task.accepted_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "reject_reason": task.reject_reason,
        "completion_remark": task.completion_remark,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
