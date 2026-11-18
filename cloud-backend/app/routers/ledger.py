"""台账路由 - 7类台账查询与 Excel/PDF 导出，批量导出/归档/打印"""
import io
import re
import shutil
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from typing import Optional
from pathlib import Path
from pydantic import BaseModel
from typing import List

from app.db import get_db
from app.config import settings
from app.models.user import User
from app.models.archive import Archive
from app.models.hazard import Hazard
from app.models.document import Document, DocType, DocStatus
from app.models.image import ImageRecord
from app.routers.auth import (
    get_current_user,
    require_project_id,
    project_scope_id,
    can_read_project,
    _can_manage_all_projects,
)
from app.services.ledger_exporter import (
    export_ledger_excel,
    export_ledger_pdf,
    export_multiple,
    create_zip,
)

router = APIRouter()


# 台账类型映射
LEDGER_TYPES = {
    "image": "影像台账",
    "旁站记录": "旁站台账",
    "监理日志": "监理日志台账",
    "安全隐患整改通知单": "整改通知单台账",
    "hazard": "AI安全巡检台账",
    "监理周报": "周报台账",
    "监理月报": "月报台账",
    "见证取样记录": "见证取样台账",
}


@router.get("/types")
async def get_ledger_types(user: dict = Depends(get_current_user)):
    """获取台账类型列表"""
    return {"types": LEDGER_TYPES}


async def _count_rows(db: AsyncSession, model, conditions: list) -> int:
    """统计满足条件的记录总数（分页 total 应为总数，而非当前页条数）"""
    result = await db.execute(
        select(func.count()).select_from(model).where(*conditions)
    )
    return int(result.scalar() or 0)


@router.get("/list")
async def list_ledger(
    ledger_type: str = Query(..., description="台账类型"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    section: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询台账数据"""
    scope = project_scope_id(user, all_projects)
    offset = (page - 1) * page_size

    if ledger_type == "image":
        # 影像台账
        conditions = [] if scope is None else [ImageRecord.project_id == scope]
        if start_date:
            conditions.append(ImageRecord.taken_at >= datetime.fromisoformat(start_date))
        if end_date:
            conditions.append(ImageRecord.taken_at <= datetime.fromisoformat(end_date))
        if section:
            conditions.append(ImageRecord.section.contains(section))
        if keyword:
            conditions.append(
                or_(
                    ImageRecord.remark.contains(keyword),
                    ImageRecord.section.contains(keyword),
                    ImageRecord.location.contains(keyword),
                )
            )
        total = await _count_rows(db, ImageRecord, conditions)
        query = (
            select(ImageRecord)
            .where(*conditions)
            .order_by(ImageRecord.taken_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(query)
        records = result.scalars().all()
        return {
            "ledger_type": ledger_type,
            "ledger_name": LEDGER_TYPES["image"],
            "total": total,
            "page": page,
            "page_size": page_size,
            "records": [
                {
                    "id": r.id,
                    "title": r.remark or r.section or r.location or f"影像记录 #{r.id}",
                    "source": r.source,
                    "original_path": r.original_path,
                    "watermarked_path": r.watermarked_path,
                    "taken_at": r.taken_at.isoformat(),
                    "location": r.location,
                    "section": r.section,
                    "weather": r.weather,
                    "hazard_id": r.hazard_id,
                    "remark": r.remark,
                }
                for r in records
            ],
        }

    elif ledger_type == "hazard":
        # AI安全巡检台账
        conditions = [] if scope is None else [Hazard.project_id == scope]
        if start_date:
            conditions.append(Hazard.created_at >= datetime.fromisoformat(start_date))
        if end_date:
            conditions.append(Hazard.created_at <= datetime.fromisoformat(end_date))
        if section:
            conditions.append(Hazard.section.contains(section))
        if keyword:
            conditions.append(
                or_(
                    Hazard.top1_risk.contains(keyword),
                    Hazard.risk_category.contains(keyword),
                    Hazard.remark.contains(keyword),
                )
            )
        total = await _count_rows(db, Hazard, conditions)
        query = (
            select(Hazard)
            .where(*conditions)
            .order_by(Hazard.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(query)
        records = result.scalars().all()
        return {
            "ledger_type": ledger_type,
            "ledger_name": LEDGER_TYPES["hazard"],
            "total": total,
            "page": page,
            "page_size": page_size,
            "records": [
                {
                    "id": r.id,
                    # 与 /{ledger_type}/{record_id} 详情接口口径一致；
                    # 缺失时前端「无配图隐患」会退化成「台账记录 #N」
                    "title": r.top1_risk or r.risk_category or f"隐患记录 #{r.id}",
                    "status": r.status,
                    "risk_status": r.risk_status,
                    "risk_category": r.risk_category,
                    "top1_risk": r.top1_risk,
                    "section": r.section,
                    "location": r.location,
                    "image_path": r.image_path,
                    "rectification_image_path": r.rectification_image_path,
                    "created_at": r.created_at.isoformat(),
                }
                for r in records
            ],
        }

    else:
        # 文书类台账（旁站、监理日志、整改单、周报、月报、见证取样）
        conditions = [Document.doc_type == ledger_type]
        if scope is not None:
            conditions.insert(0, Document.project_id == scope)
        if start_date:
            conditions.append(Document.created_at >= datetime.fromisoformat(start_date))
        if end_date:
            conditions.append(Document.created_at <= datetime.fromisoformat(end_date))
        if section:
            conditions.append(Document.section.contains(section))
        if keyword:
            conditions.append(Document.title.contains(keyword))
        total = await _count_rows(db, Document, conditions)
        query = (
            select(Document)
            .where(*conditions)
            .order_by(Document.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(query)
        records = result.scalars().all()
        return {
            "ledger_type": ledger_type,
            "ledger_name": LEDGER_TYPES.get(ledger_type, ledger_type),
            "total": total,
            "page": page,
            "page_size": page_size,
            "records": [
                {
                    "id": r.id,
                    "title": r.title or f"台账记录 #{r.id}",
                    "status": r.status,
                    "section": r.section,
                    "location": r.location,
                    "doc_number": r.content.get("doc_number", "") if r.content else "",
                    "pdf_path": r.pdf_path,
                    "markdown_path": r.markdown_path,
                    "created_at": r.created_at.isoformat(),
                }
                for r in records
            ],
        }


@router.get("/export")
async def export_ledger(
    ledger_type: str = Query(..., description="台账类型"),
    format: str = Query("excel", description="导出格式: excel / pdf"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    section: Optional[str] = None,
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出台账（Excel / PDF）"""
    scope = project_scope_id(user, all_projects)

    # 查询数据
    records = await _query_ledger_data(
        db, scope, ledger_type, start_date, end_date, section
    )

    if not records:
        raise HTTPException(status_code=404, detail="无数据可导出")

    ledger_name = LEDGER_TYPES.get(ledger_type, ledger_type)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    # 把项目归属编码进文件名，供下载端点做归属校验（防跨项目越权下载）。
    # scope=None 表示「全部项目」汇总导出（仅公司级账号），标记为 all。
    proj_tag = "all" if scope is None else f"p{scope}"

    if format == "excel":
        filename = f"{ledger_name}_{proj_tag}_{timestamp}.xlsx"
        file_path = settings.export_dir / filename
        export_ledger_excel(records, ledger_type, ledger_name, str(file_path))
    elif format == "pdf":
        filename = f"{ledger_name}_{proj_tag}_{timestamp}.pdf"
        file_path = settings.export_dir / filename
        export_ledger_pdf(records, ledger_type, ledger_name, str(file_path))
    else:
        raise HTTPException(status_code=400, detail="不支持的格式，请使用 excel 或 pdf")

    return {
        "ledger_type": ledger_type,
        "ledger_name": ledger_name,
        "format": format,
        "filename": filename,
        "download_url": f"/exports/{filename}",
        "total": len(records),
    }


@router.get("/download/{filename}")
async def download_ledger(
    filename: str,
    user: User = Depends(get_current_user),
):
    """下载导出的台账文件（按文件名中的项目归属做权限校验）。

    文件名形如 ``安全监理日志_p5_20261030....xlsx``（单项目导出，含 ``_p<id>_``）
    或 ``文书_all_20261030....pdf``（全部项目导出，含 ``_all_``）。
    无项目编码的遗留文件名无法判定归属，按纵深防御策略仅要求登录即可下载
    （与 ``/exports/`` 受控端点一致），不再强制公司级账号——否则合法用户无法
    重新下载自己此前（部署归属编码前）导出的文件。
    """
    # 防目录穿越：StaticFiles 已移除，这里显式拒绝路径字符
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="非法文件名")
    file_path = settings.export_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    leaf = filename
    if "_all_" in leaf and not _can_manage_all_projects(user):
        raise HTTPException(status_code=403, detail="全部项目导出仅公司级账号可下载")
    pids = [int(m) for m in re.findall(r"_p(\d+)_", leaf)]
    if pids:
        if not any(can_read_project(pid, user) for pid in pids):
            raise HTTPException(status_code=403, detail="无权访问该项目的导出文件")
    # 无 _p<id>_ / _all_ 标记的遗留文件名无法判定归属：按纵深防御仅要求登录
    # （与 /exports/ 受控端点一致），不再强制公司级账号，避免合法用户无法重新
    # 下载自己此前导出的文件。
    return FileResponse(path=str(file_path), filename=filename)


async def _query_ledger_data(
    db: AsyncSession,
    project_id: int | None,
    ledger_type: str,
    start_date: Optional[str],
    end_date: Optional[str],
    section: Optional[str],
) -> list:
    """查询台账数据。``project_id=None`` 表示「全部项目」只读视角。"""

    def _scoped(stmt, model):
        return stmt if project_id is None else stmt.where(model.project_id == project_id)

    if ledger_type == "image":
        query = _scoped(select(ImageRecord), ImageRecord)
        if start_date:
            query = query.where(ImageRecord.taken_at >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.where(ImageRecord.taken_at <= datetime.fromisoformat(end_date))
        if section:
            query = query.where(ImageRecord.section.contains(section))
        query = query.order_by(ImageRecord.taken_at.desc())
        result = await db.execute(query)
        return result.scalars().all()

    elif ledger_type == "hazard":
        query = _scoped(select(Hazard), Hazard)
        if start_date:
            query = query.where(Hazard.created_at >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.where(Hazard.created_at <= datetime.fromisoformat(end_date))
        if section:
            query = query.where(Hazard.section.contains(section))
        query = query.order_by(Hazard.created_at.desc())
        result = await db.execute(query)
        return result.scalars().all()

    else:
        query = _scoped(select(Document), Document).where(
            Document.doc_type == ledger_type
        )
        if start_date:
            query = query.where(Document.created_at >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.where(Document.created_at <= datetime.fromisoformat(end_date))
        if section:
            query = query.where(Document.section.contains(section))
        query = query.order_by(Document.created_at.desc())
        result = await db.execute(query)
        return result.scalars().all()


# ---- P1-4 批量台账导出/归档/打印 ----


class BatchExportRequest(BaseModel):
    ledger_types: List[str]
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    format: str = "excel"  # excel / pdf / zip


class BatchArchiveRequest(BaseModel):
    ledger_types: List[str]
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    archive_name: str
    format: str = "excel"


class BatchPrintRequest(BaseModel):
    ledger_types: List[str]
    date_from: Optional[str] = None
    date_to: Optional[str] = None


# 内存打印任务队列（生产环境应替换为持久化任务表/消息队列）
_print_jobs: dict[str, dict] = {}


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"日期格式错误: {s}")


async def _build_export_items(
    db: AsyncSession,
    project_id: int,
    ledger_types: List[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> list[tuple]:
    """构造批量导出的 (records, ledger_type, ledger_name) 列表"""
    dt_from = _parse_dt(date_from)
    dt_to = _parse_dt(date_to)
    items = []
    for lt in ledger_types:
        if lt not in LEDGER_TYPES:
            raise HTTPException(status_code=400, detail=f"台账类型非法: {lt}")
        records = await _query_ledger_data_range(db, project_id, lt, dt_from, dt_to)
        items.append((records, lt, LEDGER_TYPES[lt]))
    return items


async def _query_ledger_data_range(
    db: AsyncSession,
    project_id: int,
    ledger_type: str,
    dt_from: Optional[datetime],
    dt_to: Optional[datetime],
) -> list:
    """按日期范围查询台账数据"""
    if ledger_type == "image":
        query = select(ImageRecord).where(ImageRecord.project_id == project_id)
        if dt_from:
            query = query.where(ImageRecord.taken_at >= dt_from)
        if dt_to:
            query = query.where(ImageRecord.taken_at <= dt_to)
        query = query.order_by(ImageRecord.taken_at.desc())
    elif ledger_type == "hazard":
        query = select(Hazard).where(Hazard.project_id == project_id)
        if dt_from:
            query = query.where(Hazard.created_at >= dt_from)
        if dt_to:
            query = query.where(Hazard.created_at <= dt_to)
        query = query.order_by(Hazard.created_at.desc())
    else:
        query = select(Document).where(
            and_(Document.project_id == project_id, Document.doc_type == ledger_type)
        )
        if dt_from:
            query = query.where(Document.created_at >= dt_from)
        if dt_to:
            query = query.where(Document.created_at <= dt_to)
        query = query.order_by(Document.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/batch-export")
async def batch_export_ledger(
    req: BatchExportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量导出多份台账，打包成 zip 返回"""
    if not req.ledger_types:
        raise HTTPException(status_code=400, detail="ledger_types 不能为空")
    if req.format not in {"excel", "pdf", "zip"}:
        raise HTTPException(status_code=400, detail="format 仅支持 excel/pdf/zip")

    project_id = require_project_id(user)
    fmt = "excel" if req.format == "zip" else req.format
    items = await _build_export_items(db, project_id, req.ledger_types, req.date_from, req.date_to)

    files = export_multiple(items, fmt=fmt)
    if not files:
        raise HTTPException(status_code=404, detail="所选类型均无数据可导出")

    # 单份文件且非 zip：直接返回
    if len(files) == 1 and req.format != "zip":
        filename, data = files[0]
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # 多份文件或 zip：打包
    zip_bytes = create_zip(files)
    zip_name = f"台账批量导出_{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@router.post("/batch-archive")
async def batch_archive_ledger(
    req: BatchArchiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量归档：将多份台账导出文件归档到 archives/{project_id}/{archive_name}/ 目录"""
    if not req.ledger_types:
        raise HTTPException(status_code=400, detail="ledger_types 不能为空")
    if not req.archive_name.strip():
        raise HTTPException(status_code=400, detail="archive_name 不能为空")
    if req.format not in {"excel", "pdf"}:
        raise HTTPException(status_code=400, detail="归档格式仅支持 excel/pdf")

    project_id = require_project_id(user)
    items = await _build_export_items(db, project_id, req.ledger_types, req.date_from, req.date_to)
    files = export_multiple(items, fmt=req.format)
    if not files:
        raise HTTPException(status_code=404, detail="所选类型均无数据可归档")

    # 归档名安全化：仅取路径末段并过滤为白名单字符，防路径穿越
    raw_name = req.archive_name.strip()
    safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", Path(raw_name).name).strip("._")[:120]
    if not safe_name:
        raise HTTPException(status_code=400, detail="归档名称含无效字符")

    # 同名查重：同项目同名归档直接拒绝，避免覆盖写导致两条记录指向同一文件
    dup = await db.execute(
        select(Archive).where(Archive.project_id == project_id, Archive.name == safe_name)
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"已存在同名归档「{safe_name}」，请更换名称")

    # 归档目录
    archive_root = (settings.export_dir / "archives" / str(project_id)).resolve()
    archive_dir = (archive_root / safe_name).resolve()
    if not archive_dir.is_relative_to(archive_root):
        raise HTTPException(status_code=400, detail="归档名称非法")
    archive_dir.mkdir(parents=True, exist_ok=True)

    try:
        saved_files = []
        for filename, data in files:
            file_path = archive_dir / filename
            file_path.write_bytes(data)
            saved_files.append(filename)

        # 打包一份 zip 便于整体下载
        zip_bytes = create_zip(files)
        zip_filename = f"{safe_name}.zip"
        zip_path = archive_dir / zip_filename
        zip_path.write_bytes(zip_bytes)

        # 创建归档记录
        archive = Archive(
            project_id=project_id,
            name=safe_name,
            file_path=f"/exports/archives/{project_id}/{safe_name}/{zip_filename}",
            ledger_types_json=req.ledger_types,
            date_from=_parse_dt(req.date_from),
            date_to=_parse_dt(req.date_to),
            created_by=user.id,
        )
        db.add(archive)
        await db.commit()
    except Exception:
        # 落库失败：回滚并清掉已写盘的归档目录，避免孤儿文件
        await db.rollback()
        shutil.rmtree(archive_dir, ignore_errors=True)
        raise
    await db.refresh(archive)

    return {
        "detail": "批量归档成功",
        "archive_id": archive.id,
        "files": saved_files,
        "zip_url": archive.file_path,
        "count": len(saved_files),
    }


@router.get("/archives")
async def list_archives(
    all_projects: bool = Query(False, description="公司级账号：跨项目汇总（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """归档列表"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Archive)
    if scope is not None:
        stmt = stmt.where(Archive.project_id == scope)
    result = await db.execute(stmt.order_by(Archive.created_at.desc()))
    items = result.scalars().all()
    return {
        "total": len(items),
        "archives": [
            {
                "id": a.id,
                "name": a.name,
                "file_path": a.file_path,
                "ledger_types": a.ledger_types_json or [],
                "date_from": a.date_from.isoformat() if a.date_from else None,
                "date_to": a.date_to.isoformat() if a.date_to else None,
                "created_by": a.created_by,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in items
        ],
    }


@router.get("/archives/{archive_id}/download")
async def download_archive(
    archive_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载归档包（zip）"""
    scope = project_scope_id(user, all_projects)
    stmt = select(Archive).where(Archive.id == archive_id)
    if scope is not None:
        stmt = stmt.where(Archive.project_id == scope)
    result = await db.execute(stmt)
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(status_code=404, detail="归档记录不存在")

    file_path = archive.file_path.replace("/exports/", str(settings.export_dir) + "/")
    p = Path(file_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="归档包文件不存在")
    return FileResponse(
        path=str(p),
        media_type="application/zip",
        filename=f"{archive.name}.zip",
    )


@router.delete("/archives/{archive_id}")
async def delete_archive(
    archive_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除当前项目的归档记录及对应归档目录。"""
    project_id = require_project_id(user)
    result = await db.execute(
        select(Archive).where(
            Archive.id == archive_id,
            Archive.project_id == project_id,
        )
    )
    archive = result.scalar_one_or_none()
    if not archive:
        raise HTTPException(status_code=404, detail="归档记录不存在")

    archive_root = (settings.export_dir / "archives" / str(project_id)).resolve()
    relative_path = archive.file_path.removeprefix("/exports/").lstrip("/")
    stored_file = (settings.export_dir / relative_path).resolve()
    if stored_file.is_relative_to(archive_root):
        directory = stored_file.parent
        if directory.exists():
            # 若仍有其他归档记录指向同一目录（历史同名覆盖遗留），只删本记录的文件
            siblings = await db.execute(
                select(func.count(Archive.id)).where(
                    Archive.project_id == project_id,
                    Archive.id != archive.id,
                    Archive.file_path.like(f"%/{directory.name}/%"),
                )
            )
            sibling_count = siblings.scalar() or 0
            if sibling_count > 0:
                stored_file.unlink(missing_ok=True)
            else:
                shutil.rmtree(directory, ignore_errors=True)

    await db.delete(archive)
    await db.commit()
    return {"detail": "归档已删除", "archive_id": archive_id}


@router.post("/batch-print")
async def batch_print_ledger(
    req: BatchPrintRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量打印：生成打印队列，返回打印任务 ID

    实际打印由前端/客户端从打印队列拉取任务并渲染。本接口仅生成任务并返回 ID。
    """
    if not req.ledger_types:
        raise HTTPException(status_code=400, detail="ledger_types 不能为空")

    project_id = require_project_id(user)
    items = await _build_export_items(db, project_id, req.ledger_types, req.date_from, req.date_to)

    # 生成 excel 文件列表（打印用 excel 格式）
    files = export_multiple(items, fmt="excel")
    if not files:
        raise HTTPException(status_code=404, detail="所选类型均无数据可打印")

    # 持久化打印文件到 exports/print_jobs/
    job_id = uuid.uuid4().hex[:12]
    job_dir = settings.export_dir / "print_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    file_urls = []
    for filename, data in files:
        (job_dir / filename).write_bytes(data)
        file_urls.append(f"/exports/print_jobs/{job_id}/{filename}")

    _print_jobs[job_id] = {
        "job_id": job_id,
        "project_id": project_id,
        "created_by": user.id,
        "created_at": datetime.now().isoformat(),
        "files": file_urls,
        "status": "pending",
    }

    return {
        "detail": "打印任务已生成，请客户端拉取打印",
        "job_id": job_id,
        "files": file_urls,
        "count": len(file_urls),
    }


@router.get("/print-jobs/{job_id}")
async def get_print_job(
    job_id: str,
    user: User = Depends(get_current_user),
):
    """查询打印任务状态"""
    job = _print_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="打印任务不存在")
    return job


@router.get("/{ledger_type}/{record_id}")
async def ledger_record_detail(
    ledger_type: str,
    record_id: int,
    all_projects: bool = Query(False, description="公司级账号：可查看其他项目（仅读）"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """台账单条记录详情"""
    project_id = project_scope_id(user, all_projects)
    def _scope_of(model):
        """单项目视角下强制过滤；「全部项目」视角下不加项目条件。"""
        return [] if project_id is None else [model.project_id == project_id]

    if ledger_type == "image":
        result = await db.execute(
            select(ImageRecord).where(
                ImageRecord.id == record_id, *_scope_of(ImageRecord)
            )
        )
        r = result.scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=404, detail="影像台账记录不存在")
        return {
            "id": r.id,
            "source": r.source,
            "original_path": r.original_path,
            "watermarked_path": r.watermarked_path,
            "taken_at": r.taken_at.isoformat(),
            "created_at": r.taken_at.isoformat(),
            "location": r.location,
            "section": r.section,
            "weather": r.weather,
            "hazard_id": r.hazard_id,
            "remark": r.remark,
            "title": r.remark or r.section or r.location or f"影像记录 #{r.id}",
            "area": r.section,
            "creator_name": await _creator_name(db, r.uploader_id),
            "content": r.remark or "",
            "description": r.remark or "",
            "photos": [p for p in [r.watermarked_path, r.original_path] if p],
        }

    elif ledger_type == "hazard":
        result = await db.execute(
            select(Hazard).where(Hazard.id == record_id, *_scope_of(Hazard))
        )
        h = result.scalar_one_or_none()
        if not h:
            raise HTTPException(status_code=404, detail="隐患台账记录不存在")
        return {
            "id": h.id,
            "status": h.status,
            "risk_status": h.risk_status,
            "risk_category": h.risk_category,
            "top1_risk": h.top1_risk,
            "section": h.section,
            "location": h.location,
            "image_path": h.image_path,
            "rectification_image_path": h.rectification_image_path,
            "created_at": h.created_at.isoformat(),
            "title": h.top1_risk or h.risk_category or f"隐患记录 #{h.id}",
            "area": h.section,
            "creator_name": await _creator_name(db, h.reporter_id),
            "content": h.remark or "",
            "description": h.top1_risk or h.remark or "",
            "photos": [p for p in [h.image_path, h.rectification_image_path] if p],
            "hazards": [{"id": h.id, "title": h.top1_risk or f"隐患 #{h.id}"}],
        }

    else:
        result = await db.execute(
            select(Document).where(
                and_(
                    Document.id == record_id,
                    Document.doc_type == ledger_type,
                    *_scope_of(Document),
                )
            )
        )
        d = result.scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail="台账记录不存在")
        content = d.content or {}
        summary = (
            content.get("summary")
            or content.get("construction_progress")
            or content.get("problems_section")
            or ""
        ) if isinstance(content, dict) else ""
        return {
            "id": d.id,
            "title": d.title,
            "status": d.status,
            "doc_type": d.doc_type,
            "doc_number": content.get("doc_number", "") if isinstance(content, dict) else "",
            "section": d.section,
            "location": d.location,
            "pdf_path": d.pdf_path,
            "markdown_path": d.markdown_path,
            "created_at": d.created_at.isoformat(),
            "doc_no": content.get("doc_number", "") if isinstance(content, dict) else "",
            "area": d.section,
            "creator_name": await _creator_name(db, d.creator_id),
            "content": summary,
            "description": summary,
            "photos": [p for p in (d.attached_images or []) if p],
            "related_documents": [
                {"id": d.id, "title": d.title, "doc_type": d.doc_type},
            ],
        }


async def _creator_name(db: AsyncSession, user_id: Optional[int]) -> str:
    """查询用户真实姓名"""
    if not user_id:
        return ""
    from app.models.user import User as UserModel
    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    u = result.scalar_one_or_none()
    if u and getattr(u, "real_name", ""):
        return u.real_name
    return (u.username if u else "")
