"""工程影像资料归档 API（分类标准 → 预览 → 确认口径 → 落盘台账）。

链路：/api/media/images 上传（已存水印 OCR 字段）→ 本模块复用这些字段归类
→ preview 出「归档结果 + 影像台账 + 资料缺口」→ 用户按 overrides 确认口径
→ commit 复制进标准目录、写台账 CSV、回填 ImageRecord 归类列。

安全：
- 写必落具体项目（require_project_id），MediaRecord 按 project_id 隔离；
- commit 的 dry_run 默认 True，只有显式 dry_run=false 才复制文件/写台账；
- 只复制不移动、不覆盖，原图与 MediaRecord 引用保持有效（见 image_archive_service）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.image import ImageRecord
from app.models.miniprogram import MediaRecord
from app.models.user import User
from app.routers.auth import get_current_user, require_project_id
from app.services import image_archive_service as io
from app.services.image_classification import (
    CATEGORIES,
    X_SUBCATEGORIES,
    PANGZHAN_LIST,
    PANGZHAN_EXCLUSION_KEYWORDS,
    CONFLICT_PRIORITY,
    LABELS,
    NAMING_TEMPLATE,
    LEDGER_FIELDS,
    GAP_SUGGESTIONS,
    signal_from_watermark,
    archive_batch,
)

router = APIRouter()


class ArchiveItem(BaseModel):
    """一张待归档照片。media_id 为 /api/media/images 上传返回的 id。"""

    media_id: int
    watermark: Optional[dict] = None       # 覆盖/补充上传时识别的水印字段
    signals: Optional[dict] = None          # vision 给出的行为判据（是否举牌/封样/危大等）
    unit: Optional[str] = None              # 单位工程（覆盖 OCR）
    section: Optional[str] = None           # 部位/桩号（覆盖 OCR）
    content: Optional[str] = None           # 工序/对象（覆盖 OCR 的「内容」）
    taken_at: Optional[str] = None
    inspector: Optional[str] = None
    related_doc_no: Optional[str] = None    # 关联书面资料编号（就近对应原则）


class OverrideItem(BaseModel):
    """人工确认口径，序号对应 preview 返回的「序号」。"""

    model_config = ConfigDict(populate_by_name=True)

    index: int = Field(..., alias="序号", ge=1, description="对应 preview 的序号")
    category_code: Optional[str] = None
    subcategory: Optional[str] = None
    labels: Optional[list[str]] = None
    related_doc_no: Optional[str] = None
    note: Optional[str] = None
    signals: Optional[dict] = None


class ArchiveRequest(BaseModel):
    items: list[ArchiveItem] = Field(..., min_length=1, max_length=200)
    overrides: list[OverrideItem] = Field(default_factory=list)
    dry_run: bool = True  # commit 默认只出计划，显式 false 才落盘


def _standard() -> dict:
    """把分类标准以结构化形式给前端（含本项目口径），避免前端硬编码。"""
    return {
        "categories": [
            {"code": code, "name": name, "archive_dir": directory, "definition": definition}
            for code, (name, directory, definition) in CATEGORIES.items()
        ],
        "x_subcategories": [
            {"code": code, "name": name, "typical": typical}
            for code, (name, typical) in X_SUBCATEGORIES.items()
        ],
        "pangzhan_list": [
            {"序号": idx, "工序": name, "主要控制点": points}
            for idx, name, points in PANGZHAN_LIST
        ],
        "project_rules": {
            "pangzhan_excluded": list(PANGZHAN_EXCLUSION_KEYWORDS),
            "note": "沟槽/深基坑开挖不实施旁站，按 X2 危大工程巡视归类（2026-11-03 项目监理部确认）；"
                    "只排除开挖，回填仍属旁站清单第 1 项；水泥搅拌桩成桩属清单第 13 项。",
        },
        "conflict_priority": CONFLICT_PRIORITY,
        "labels": list(LABELS),
        "naming_template": NAMING_TEMPLATE,
        "ledger_fields": LEDGER_FIELDS,
        "gap_suggestions": GAP_SUGGESTIONS,
    }


@router.get("/standard")
async def get_standard(user: User = Depends(get_current_user)):
    """分类标准（8 类、旁站清单、项目口径、命名与台账字段）。"""
    return _standard()


async def _build_batch(req: ArchiveRequest, db: AsyncSession, project_id: int, user: User):
    """按请求顺序构造 signals 并计算归档批次；顺带回传每条的源文件上下文。"""
    ids = [item.media_id for item in req.items]
    rows = (await db.execute(
        select(MediaRecord).where(
            MediaRecord.id.in_(ids),
            MediaRecord.project_id == project_id,
            MediaRecord.media_type == "image",
        )
    )).scalars().all()
    by_id = {row.id: row for row in rows}
    missing = [mid for mid in ids if mid not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"本项目中找不到这些影像：{missing}")

    signals = []
    context = []
    for item in req.items:
        media = by_id[item.media_id]
        metadata = dict(media.metadata_json or {})
        fields = dict(metadata.get("watermark") or {})
        if item.watermark:
            fields.update(item.watermark)
        # 上传链路把拍摄时间放在 metadata.capture_time；水印字段叫 taken_at
        if not fields.get("taken_at") and metadata.get("capture_time"):
            fields["taken_at"] = metadata["capture_time"]
        fields.setdefault("section", media.section or "")
        fields.setdefault("location", media.location or "")
        fields.setdefault("inspector", user.real_name or "")
        if item.unit:
            fields["unit"] = item.unit
        if item.section:
            fields["section"] = item.section
        if item.content:
            fields["content"] = item.content
        if item.taken_at:
            fields["taken_at"] = item.taken_at
        if item.inspector:
            fields["inspector"] = item.inspector
        if item.related_doc_no:
            fields["related_doc_no"] = item.related_doc_no
        signals.append(signal_from_watermark(fields,
                                             unit=item.unit or "",
                                             section=item.section or "",
                                             extra_signals=item.signals))
        context.append({
            "media_id": media.id,
            "source": media.processed_path or media.original_path,
            "file_hash": media.file_hash,
        })

    overrides = [
        ov.model_dump(by_alias=True, exclude_none=True) for ov in req.overrides
    ]
    batch = archive_batch(signals, overrides=overrides)
    return batch, context


@router.post("/preview")
async def preview(
    req: ArchiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """只算不写：归档结果表 + 影像台账行 + 资料缺口 + 待确认项。"""
    project_id = require_project_id(user)
    batch, _ = await _build_batch(req, db, project_id, user)
    return {**batch, "written": False}


@router.post("/commit")
async def commit(
    req: ArchiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """确认归档。dry_run 默认 True 只返回落盘计划；false 才复制文件、写台账、回填归类列。"""
    project_id = require_project_id(user)
    batch, context = await _build_batch(req, db, project_id, user)

    plan = []
    for index, (record, row, ctx) in enumerate(
        zip(batch["records"], batch["ledger_rows"], context), start=1
    ):
        plan.append({
            "序号": index,
            "media_id": ctx["media_id"],
            "源路径": ctx["source"],
            "目标目录": io.sanitize_component(record["归档目录"]) + "/"
                     + (io.X_SUBDIR_NAMES.get(row["子类"] or "") or ""),
            "文件名": record["文件名"],
            "状态": record["状态"],
        })
    if req.dry_run:
        return {**batch, "written": False, "plan": plan,
                "hint": "确认无误后以 dry_run=false 再次提交才会复制文件并写台账"}

    copy_results = []
    for record, row, ctx in zip(batch["records"], batch["ledger_rows"], context):
        result = io.archive_one(
            settings.upload_dir, ctx["source"], row, record["归档目录"],
            actual_filename=record["文件名"],
        )
        copy_results.append({"media_id": ctx["media_id"], **result})
        if result.get("public_path"):
            row["存储路径"] = result["public_path"]  # 台账写完整归档路径
            # 回填 ImageRecord 归类列（按 file_hash 定位上传时创建的那条）
            image = (await db.execute(
                select(ImageRecord)
                .where(ImageRecord.project_id == project_id,
                       ImageRecord.file_hash == ctx["file_hash"])
                .order_by(ImageRecord.id.desc())
            )).scalars().first()
            if image is not None:
                image.category_code = row["类别码"]
                image.subcategory = row["子类"] or None
                image.labels = "、".join(record["标签"]) or None
                image.archive_dir = record["归档目录"]
                image.archive_filename = record["文件名"]
                image.related_doc_no = (row["关联资料编号"]
                                        if row["关联资料编号"] != "待补" else None)
                image.classification_status = record["状态"]
                image.classification_note = row["备注"]

    date = (batch["ledger_rows"][0].get("拍摄日期") or "").replace("-", "") or "00000000"
    ledger = io.write_ledger_csv(settings.upload_dir, date,
                                 batch["ledger_rows"], LEDGER_FIELDS)
    await db.commit()
    return {
        **batch,
        "written": True,
        "plan": plan,
        "copy_results": copy_results,
        "ledger": ledger,
        "archive_root": f"/uploads/{io.ARCHIVE_ROOT}/",
    }
