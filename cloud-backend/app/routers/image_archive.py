"""工程影像资料归档 API（分类标准 → 预览 → 确认口径 → 落盘台账）。

链路：/api/media/images 上传（已存水印 OCR 字段）→ 本模块复用这些字段归类
→ preview 出「归档结果 + 影像台账 + 资料缺口 + 两份文档全文预览」
→ 用户按 overrides / standard_note / pangzhan_exclusions 确认口径
→ commit 四处联动：① 照片进标准目录、② 影像台账 CSV + ImageRecord 归类列、
③ 工程影像归档工作记录（PDF，改口径出 R2 新版）、④ 项目分类标准口径文件
→ 两份文书同步进项目资料库 KB（各类台账）。

安全：
- 写必落具体项目（require_project_id），MediaRecord 按 project_id 隔离；
- commit 的 dry_run 默认 True，只有显式 dry_run=false 才复制文件/写台账/建文档；
- 只复制不移动、不覆盖，原图与 MediaRecord 引用保持有效（见 image_archive_service）；
- 照片/台账主流程先提交，文档生成或 KB 同步失败只报告、不回滚已归档照片。
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
from app.models.user import Project, User
from app.routers.auth import get_current_user, require_project_id
from app.services import image_archive_document as docbuild
from app.services import image_archive_service as io
from app.services import image_archive_sync as docsync
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
    # 文档联动（截图「监理工作记录文档」+「分类标准」）
    generate_documents: bool = True
    standard_note: Optional[str] = None   # 本次口径修正说明，写入工作记录与分类标准
    pangzhan_exclusions: Optional[list[str]] = None  # 项目旁站清单排除项（确认后持久生效）
    project_name: Optional[str] = None    # 不传则从 Project 表读取


def _standard(rules: Optional[dict] = None) -> dict:
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
            "effective_excluded": (
                list(rules.get("pangzhan_excluded") or [])
                if rules and (rules.get("revision") or 0) > 0
                else list(PANGZHAN_EXCLUSION_KEYWORDS)
            ),
            "revision": (rules or {}).get("revision") or 0,
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
    """分类标准（8 类、旁站清单、项目已确认口径、命名与台账字段）。"""
    return _standard(io.load_project_rules(settings.upload_dir))


def _effective_exclusions(req: ArchiveRequest, rules: dict) -> list[str]:
    """当次生效的旁站清单排除项：请求显式给 > 项目已确认口径 > 标准默认。"""
    if req.pangzhan_exclusions is not None:
        return [str(x).strip() for x in req.pangzhan_exclusions if str(x).strip()]
    if (rules.get("revision") or 0) > 0:
        return list(rules.get("pangzhan_excluded") or [])
    return list(PANGZHAN_EXCLUSION_KEYWORDS)


async def _project_name(db: AsyncSession, project_id: int, fallback: str = "") -> str:
    """读工程项目名称；假 DB / 项目已删除时静默回退，不阻断归档。"""
    getter = getattr(db, "get", None)
    if getter is None:
        return fallback
    try:
        project = await getter(Project, project_id)
    except Exception:
        return fallback
    return getattr(project, "name", None) or fallback


async def _document_revision(db: AsyncSession, project_id: int,
                             doc_type: str, title: str) -> int:
    """预览用的当前版本号；查不到按 0 处理（不阻断）。"""
    try:
        from app.models.document import Document
        row = (await db.execute(
            select(Document.content_revision).where(
                Document.project_id == project_id,
                Document.doc_type == doc_type,
                Document.title == title,
            ).order_by(Document.id.desc())
        )).scalars().first()
        return int(row or 0)
    except Exception:
        return 0


def _document_specs(req: ArchiveRequest, batch: dict, *, project_id: int,
                    project_name: str, rules: dict, ledger_path: str = "",
                    actor_name: str = "") -> list[dict]:
    """构造两份文档的渲染闭包；revision 由落盘时的真实版本注入。"""
    day = (batch["ledger_rows"][0].get("拍摄日期") if batch.get("ledger_rows") else "") or ""
    day = docbuild.compact_date(str(day))
    business_date = (batch["ledger_rows"][0].get("拍摄日期") if batch.get("ledger_rows") else "") or day
    standard = _standard(rules)
    overrides = [ov.model_dump(by_alias=True, exclude_none=True) for ov in req.overrides]

    def render_work_record(revision: int) -> dict:
        return docbuild.build_work_record(
            batch, project_id=project_id, project_name=project_name,
            business_date=str(business_date), actor_name=actor_name, revision=revision,
            ledger_path=ledger_path, rules=rules, overrides=overrides,
        )

    def render_standard(revision: int) -> dict:
        return docbuild.build_standard_document(
            standard, rules, project_id=project_id, project_name=project_name,
            business_date=str(business_date), revision=revision,
        )

    return [
        {"doc_type": docbuild.WORK_RECORD_DOC_TYPE, "day": day,
         "title": f"工程影像归档工作记录_{day}", "render": render_work_record},
        {"doc_type": docbuild.STANDARD_DOC_TYPE, "day": day,
         "title": "工程影像分类标准_项目口径", "render": render_standard},
    ]


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
    rules = io.load_project_rules(settings.upload_dir)
    exclusions = _effective_exclusions(req, rules)
    batch = archive_batch(signals, overrides=overrides,
                          excluded_keywords=exclusions)
    # project_rules = 本次判定实际生效的口径；rules_file = 持久化文件状态（含修订历史）
    batch["rules_file"] = rules
    return batch, context


async def _preview_documents(db: AsyncSession, specs: list[dict], *,
                             project_id: int, status: str) -> list[dict]:
    """给前端看「将生成/将更新的文档」：标题、编号、下一版本号与全文预览。"""
    out = []
    for spec in specs:
        current = await _document_revision(db, project_id,
                                           spec["doc_type"], spec["title"])
        payload = spec["render"](current + 1)
        out.append({
            "title": spec["title"],
            "doc_type": spec["doc_type"],
            "doc_number": payload["doc_number"],
            "next_revision": current + 1,
            "status": status,
            "markdown": payload["markdown"],
        })
    return out


@router.post("/preview")
async def preview(
    req: ArchiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """只算不写：归档结果 + 台账行 + 缺口 + 待确认 + 两份文档全文预览。"""
    project_id = require_project_id(user)
    batch, _ = await _build_batch(req, db, project_id, user)
    project_name = req.project_name or await _project_name(db, project_id)
    specs = (_document_specs(req, batch, project_id=project_id,
                             project_name=project_name, rules=batch["rules_file"],
                             actor_name=user.real_name or "")
             if req.generate_documents else [])
    documents = await _preview_documents(db, specs, project_id=project_id,
                                         status="preview")
    return {**batch, "written": False, "documents": documents}


@router.post("/commit")
async def commit(
    req: ArchiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """确认归档。dry_run 默认 True 只出计划；false 才落盘并四处联动。"""
    project_id = require_project_id(user)
    batch, context = await _build_batch(req, db, project_id, user)
    rules = batch["rules_file"]
    project_name = req.project_name or await _project_name(db, project_id)
    actor_name = user.real_name or ""

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
    specs = (_document_specs(req, batch, project_id=project_id,
                             project_name=project_name, rules=rules,
                             actor_name=actor_name)
             if req.generate_documents else [])
    if req.dry_run:
        documents = await _preview_documents(db, specs, project_id=project_id,
                                             status="planned")
        return {**batch, "written": False, "plan": plan,
                "documents": documents,
                "hint": "确认无误后以 dry_run=false 再次提交才会复制文件、写台账并更新两份文档"}

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
    # 照片 / 台账 / ImageRecord 归类列先提交：文档生成或 KB 同步失败不影响主流程。
    # async_session 为 expire_on_commit=False，提交后 user/doc 对象仍可安全访问。
    await db.commit()

    documents: list[dict] = []
    rules_saved = None
    if req.generate_documents:
        # 第四处联动：把确认过的项目口径写进「分类标准_项目口径.json」，
        # 后续批次会自动按新口径判定，不必逐张手工 override。
        effective = _effective_exclusions(req, rules)
        should_update = (req.pangzhan_exclusions is not None
                         or bool(req.standard_note)
                         or not io.rules_path(settings.upload_dir).is_file())
        if should_update:
            rules = io.merge_project_rules(
                rules, exclusions=effective, note=req.standard_note or "",
                actor=actor_name, override_count=len(req.overrides),
            )
            rules_saved = io.save_project_rules(settings.upload_dir, rules)
            batch["rules_file"] = rules
            batch["project_rules"]["pangzhan_excluded"] = effective
        specs = _document_specs(req, batch, project_id=project_id,
                                project_name=project_name, rules=rules,
                                ledger_path=ledger["public_path"],
                                actor_name=actor_name)
        try:
            results = await docsync.upsert_documents(
                db, specs, project_id=project_id, user=user)
            await db.commit()
            documents = await docsync.sync_to_archive_kb(db, results, user)
            await db.commit()
        except Exception as exc:  # 文档是附加产物，失败只报告、不回滚已归档照片
            documents = [{"status": "failed", "error": str(exc)[:500]}]

    return {
        **batch,
        "written": True,
        "plan": plan,
        "copy_results": copy_results,
        "ledger": ledger,
        "archive_root": f"/uploads/{io.ARCHIVE_ROOT}/",
        "documents": documents,
        "standard_rules": rules_saved,
    }
