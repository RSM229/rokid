"""受控文件服务路由（取代原先无鉴权的 /uploads、/exports StaticFiles 挂载）。

历史根因：``app/main.py`` 把 ``/uploads``、``/exports`` 直接挂成 ``StaticFiles``，
包裹它们的只有 CORS 与「附加响应头」中间件，**没有任何身份认证**——未登录者
``GET /exports/<file>`` 直接 200 返回项目台账/归档/文书，``GET /uploads/<file>``
同理泄露用户上传的巡检照片、证件等。实测无 token 直取成功。

本模块在**原路径**上提供鉴权版本（DB 里存量的 ``/uploads/...``、``/exports/...``
URL 无需迁移），并：
- 强制 ``get_current_user``（关闭未登录访问，即前述严重漏洞）；
- 对 ``exports/archives/{project_id}/...`` 这类路径内编码了项目的文件，用
  ``can_read_project`` 校验归属（跨项目越权下载返回 403）；
- 平铺导出文件（台账/文书）在生成时把项目 id 编码进文件名（见 ``ledger.py``
  导出端点），下载时解析并校验；无编码的遗留文件仅要求登录（纵深防御，
  后续可接 per-category 清单做完整隔离）；
- 全程做路径穿越防护，``rest`` 解析后必须仍落在各自根目录内。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.config import settings
from app.models.user import User
from app.routers.auth import can_read_project, get_user_from_token
from app.db import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


# 🔴 小程序 <image>/wx.previewImage 只能带 query、带不了 Authorization 头。
# 生产 /uploads 是受控端点（强制登录），图片标签拿不到 token 就被 401 → 图片全空白
# （2026-11-04 用户反馈「又看不到图片」）。这里接受 query 里的 access_token，
# 与 header 二选一，都过 get_user_from_token + attach_effective_permissions（与原
# get_current_user 同权限口径，can_read_project 依赖 effective_permissions）。
async def _user_from_file_request(
    request: Request,
    access_token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    header = (request.headers.get("Authorization") or "").strip()
    if header.lower().startswith("bearer "):
        header = header[7:].strip()
    token = header or (access_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未提供访问凭证")
    user = await get_user_from_token(token, db)
    from app.services.permissions import attach_effective_permissions

    user = await attach_effective_permissions(user, db)
    return user


def _safe_resolve(base: Path, rest: str) -> Path:
    """把 ``rest`` 解析到 ``base`` 内，防路径穿越（../）。"""
    base_resolved = base.resolve()
    target = (base_resolved / rest).resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise HTTPException(status_code=400, detail="非法的文件路径")
    return target


@router.get("/uploads/{rest:path}")
async def serve_upload(
    rest: str,
    user: User = Depends(_user_from_file_request),
):
    """受控返回用户上传文件（巡检照片、图纸、签章、文书原图等）。

    要求登录（Authorization header 或 ?access_token= 二选一，供小程序
    <image> 标签使用）；具体项目的归属隔离为后续 per-category 清单增强项。
    """
    path = _safe_resolve(settings.upload_dir, rest)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@router.get("/exports/{rest:path}")
async def serve_export(
    rest: str,
    user: User = Depends(_user_from_file_request),
    db: AsyncSession = Depends(get_db),
):
    """受控返回导出/归档文件。

    - ``archives/{project_id}/...``：路径内编码项目，按归属校验（403 拦截越权）；
    - 平铺导出文件若文件名含 ``_p{project_id}_`` 标记，按标记校验归属；
      含 ``_all_`` 标记为「全部项目」导出，仅公司级账号可下载；
    - ``doc_{id}_`` 前缀的业务文书（日记/日志/省统表等）按文书归属校验：
      日记（个人维度）仅本人或 document_approve；其余按项目归属
      （2026-11-18 补漏：此前平铺日记文件名无项目标记，任何登录用户
      枚举 doc id 即可直读他人日记全文）；
    - 其余遗留平铺文件：仅要求登录（纵深防御）。
    """
    path = _safe_resolve(settings.export_dir, rest)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    parts = [p for p in rest.split("/") if p]
    # archives/{project_id}/...
    if parts[0] == "archives" and len(parts) >= 2:
        try:
            pid = int(parts[1])
            if not can_read_project(pid, user):
                raise HTTPException(status_code=403, detail="无权访问该项目文件")
        except ValueError:
            pass
        return FileResponse(path)

    leaf = parts[-1]
    # doc_{id}_… 业务文书：反查库校验归属（日记 404 防探测，其余项目归属）
    doc_match = re.match(r"^doc_(\d+)_", leaf)
    if doc_match:
        from app.models.document import Document

        doc = await db.get(Document, int(doc_match.group(1)))
        if doc is not None:
            if doc.doc_type == "监理日记" or doc.form_code == "jianli_riji":
                # 与 document.py 读口同口径：非本人且无审批权限 → 404
                if doc.creator_id != user.id and not user.has_permission(
                    "document_approve"
                ):
                    raise HTTPException(status_code=404, detail="文件不存在")
            else:
                if not can_read_project(doc.project_id, user):
                    raise HTTPException(status_code=403, detail="无权访问该项目文件")
        # doc 不存在（已删）的残留文件：继续走通用标记校验
    if "_all_" in leaf and not user.is_super_admin() and not user.is_org_admin():
        raise HTTPException(status_code=403, detail="全部项目导出仅公司级账号可下载")
    # 取文件名中所有 _p<数字>_ 片段，逐一校验归属
    for m in re.finditer(r"_p(\d+)_", leaf):
        pid = int(m.group(1))
        if not can_read_project(pid, user):
            raise HTTPException(status_code=403, detail="无权访问该项目文件")

    return FileResponse(path)
