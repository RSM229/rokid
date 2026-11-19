"""穗建通数据采集推送入口（管理端手动推送，SUP-001/002/003/005/006/007/012/021）。

职责：
- 从 WorkRecord/ParallelInspection/Document 取记录 → 组装 mapper 入参 dict →
  调 szcj_push_client 推送 → 逐条回执（ok/status/error）+ 失败清单回显。
- 凭证未配置（PushNotConfiguredError）→ 503 明确提示「找穗建通管理员要凭证」，
  不静默空跑。
- 权限：settings（总监/公司级）。推送是运营动作，落到具体项目时仍要求
  require_project_id（全部项目视角下不推，防止推错项目编码）。

数据源对照（2026-11-19 核查确认）：
    SUP-001 巡视  ← WorkRecord record_type=巡视（inspection_photos）
    SUP-002 旁站  ← WorkRecord record_type=旁站（station_* 列 + inspection_photos）
    SUP-003 平检  ← ParallelInspection（独立表，无照片列 → photoUrls 必填会拒收，
                    拿 ParallelInspection 生成的文书照片补，见 _parallel_photos）
    SUP-005 验收  ← WorkRecord record_type=验收（acceptance_* + photos）
    SUP-006 日志  ← Document doc_type=监理日志（content 键 + attached_images）
    SUP-007 月报  ← Document doc_type=监理月报（content 键 + attached_images）
    SUP-012 材料  ← WorkRecord record_type=见证（witness_* 列，进场台账口径）
    SUP-021 见证  ← WorkRecord record_type=见证（witness_* 列）
    （012/021 同源不同视角：一条见证记录可同时满足两个接口的必填，各自独立推送。）
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.document import Document, DocType
from app.models.parallel import ParallelInspection
from app.models.user import Project, User
from app.models.work_record import WorkRecord
from app.routers.auth import get_current_user, require_project_id, can_read_project
from app.services.szcj_push_client import (
    PushNotConfiguredError,
    PushResult,
    project_code_for,
    push_payload,
    push_sup001,
)
from app.services.szcj_batch_mappers import (
    map_sup002,
    map_sup003,
    map_sup005,
    map_sup006,
    map_sup007,
    map_sup012,
    map_sup021,
)
from app.services.szcj_sup001_mapper import MissingRequiredError

logger = logging.getLogger(__name__)
router = APIRouter()

# record_type → SUP 接口（一次推送会话里按类型分桶，逐桶调各自 mapper）
SUP_ENDPOINTS = {
    "001": "patrol_record",
    "002": "station_record",
    "003": "parallel_inspect",
    "005": "acceptance_record",
    "006": "daily_log",
    "007": "monthly_report",
    "012": "material_check",
    "021": "witness_sampling",
}

SUP_LABELS = {
    "001": "巡视记录", "002": "旁站记录", "003": "平行检验", "005": "验收记录",
    "006": "监理日志", "007": "监理月报", "012": "材料进场", "021": "见证取样",
}


class PushReplyItem(BaseModel):
    sup_code: str
    sup_label: str
    record_id: int
    record_title: str = ""
    ok: bool
    status: str = ""
    error: str = ""


class PushReply(BaseModel):
    total: int
    succeeded: int
    failed: int
    not_configured: bool = False
    detail: str = ""
    items: list[PushReplyItem] = []
    # 失败清单（前端弹层回显用；成功项不进此清单）
    failures: list[PushReplyItem] = []


class PushOneRequest(BaseModel):
    """按记录推送：sup_code + 记录主键（WorkRecord/Parallel/Document 的 id）。"""
    sup_code: str = Field(pattern="^(001|002|003|005|006|007|012|021)$")
    record_id: int


class PushRangeRequest(BaseModel):
    """按日期范围批量推（当前项目）。date_from/to 含端点，默认近 7 天。"""
    sup_codes: list[str] = Field(default_factory=lambda: list(SUP_ENDPOINTS))
    date_from: Optional[str] = None   # yyyy-MM-dd，缺省 = today-7
    date_to: Optional[str] = None     # yyyy-MM-dd，缺省 = today


def _public_file_url(path: str) -> str:
    """本地 /uploads/… → 公网 URL（穗建通要公网可达的照片）。

    相对路径拼 PUBLIC_API_BASE_URL；已是 http(s) 原样返回。注意生产
    /uploads 是登录态受控端点——凭证联调时若穗建通拉不到图，需要另开
    只读白名单路由或对象存储直链，这里留钩子集中改一处。
    """
    text = str(path or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/uploads/"):
        return f"{settings.public_api_base_url.rstrip('/')}{text}"
    return text


def _workrecord_dict(r: WorkRecord) -> dict[str, Any]:
    """WorkRecord ORM → mapper 入参 dict（键名对齐列名）。"""
    return {
        "id": r.id, "status": r.status, "record_date": r.record_date,
        "section": r.section, "location": r.location, "weather": r.weather,
        "work_content": r.work_content, "problems": r.problems,
        "treatment": r.treatment, "remark": r.remark,
        "inspection_start_time": r.inspection_start_time,
        "inspection_end_time": r.inspection_end_time,
        "station_process": r.station_process, "station_location": r.station_location,
        "station_start_time": r.station_start_time, "station_end_time": r.station_end_time,
        "station_content": r.station_content,
        "station_supervision_status": r.station_supervision_status,
        "station_conclusion": r.station_conclusion,
        "construction_unit": None,  # 由调用方按 business fields 注入
        "acceptance_result": r.acceptance_result,
        "acceptance_basis": r.acceptance_basis,
        "acceptance_participants": r.acceptance_participants,
        "acceptance_guide_ref": r.acceptance_guide_ref,
        "witness_material_name": r.witness_material_name,
        "witness_material_spec": r.witness_material_spec,
        "witness_quantity": r.witness_quantity,
        "witness_unit": r.witness_unit,
        "witness_cert_no": r.witness_cert_no,
        "witness_accept_result": r.witness_accept_result,
        "doc_number": (r.generated_doc_id and f"WR-{r.id}") or None,
        "content_json": r.content_json or {},
    }


async def _record_user_name(db: AsyncSession, user_id: int) -> str:
    row = (await db.execute(select(User.real_name).where(User.id == user_id))).scalar_one_or_none()
    return str(row or "")


async def _parallel_photos(db: AsyncSession, p: ParallelInspection) -> list[str]:
    """平行检验无照片列：取其生成文书的 attached_images。"""
    if not p.generated_doc_id:
        return []
    doc = await db.get(Document, p.generated_doc_id)
    return list(doc.attached_images or []) if doc else []


async def _push_workrecord(
    db: AsyncSession, r: WorkRecord, sup_code: str, project_code: str, project_name: str,
) -> PushResult:
    """WorkRecord → mapper → push（002/005/012/021 + 001 巡视）。"""
    user_name = await _record_user_name(db, r.creator_id)
    # 施工单位/项目名走 business 口径（与文书渲染同源，勿另起炉灶）
    from app.services.record_fields import business_record_fields
    project = await db.get(Project, r.project_id) if r.project_id else None
    business = business_record_fields(r, project)
    rec = _workrecord_dict(r)
    rec["construction_unit"] = business.get("construction_unit") or None
    rec["project_name"] = project_name
    photos = list(r.inspection_photos or [])
    if not photos and r.record_type == "见证":
        photos = list(r.witness_photos or [])
    urls = [_public_file_url(u) for u in photos]
    if sup_code == "001":
        return await push_sup001(
            rec, project_code=project_code, user_name=user_name, photo_urls=urls,
            created_time=r.created_at, updated_time=r.updated_at,
        )
    if sup_code == "002":
        payload = map_sup002(
            rec, project_code=project_code, user_name=user_name, photo_urls=urls,
            created_time=r.created_at, updated_time=r.updated_at,
        )
        return await push_payload(SUP_ENDPOINTS["002"], payload)
    if sup_code == "005":
        payload = map_sup005(
            rec, project_code=project_code, photo_urls=urls,
            created_time=r.created_at, updated_time=r.updated_at,
            acceptance_type=str(r.acceptance_guide_ref
                                or (r.content_json or {}).get("acceptance_type") or ""),
        )
        return await push_payload(SUP_ENDPOINTS["005"], payload)
    if sup_code == "012":
        payload = map_sup012(
            rec, project_code=project_code, checker_name=user_name, photo_urls=urls,
            created_time=r.created_at, updated_time=r.updated_at,
        )
        return await push_payload(SUP_ENDPOINTS["012"], payload)
    if sup_code == "021":
        payload = map_sup021(
            rec, project_code=project_code, witness_person=user_name, photo_urls=urls,
            created_time=r.created_at, updated_time=r.updated_at,
        )
        return await push_payload(SUP_ENDPOINTS["021"], payload)
    raise HTTPException(400, f"SUP-{sup_code} 不支持从工作记录推送")


async def _push_parallel(
    db: AsyncSession, p: ParallelInspection, project_code: str,
) -> PushResult:
    user_name = await _record_user_name(db, p.user_id)
    photos = [_public_file_url(u) for u in (await _parallel_photos(db, p))]
    rec = {
        "id": p.id, "status": "已提交", "record_date": p.inspect_date,
        "section": p.part, "work_content": p.result_detail,
        "acceptance_result": p.conclusion, "acceptance_basis": p.basis,
        "doc_number": f"GD-B-213-{p.id:05d}",
        "content_json": {
            "method": p.method or "", "instrument": "", "measure_data": "",
            "acceptance_content": p.item, "conclusion": p.conclusion,
        },
    }
    payload = map_sup003(
        rec, project_code=project_code, user_name=user_name, photo_urls=photos,
        created_time=p.created_at, updated_time=p.updated_at,
    )
    return await push_payload(SUP_ENDPOINTS["003"], payload)


def _doc_record_dict(doc: Document) -> dict[str, Any]:
    """Document（日志/月报）→ mapper 入参 dict（content 键名对齐）。

    状态映射：Document 状态（草稿/待审批/已审批/已归档/已驳回）→ 穗建通
    1/2/3（用 _doc_status_map 转成 WorkRecord 风格的状态词，供 _status_of 读）。
    006 的状态枚举语义错位（1-生成中 2-预览中 3-已归档），数值上复用同一映射。
    """
    content = doc.content or {}
    status_word = {
        "草稿": "草稿", "待审批": "已提交", "已审批": "已提交",
        "已归档": "已归档", "已驳回": "已驳回",
    }.get(str(doc.status or ""), "草稿")
    return {
        "id": doc.id, "status": status_word, "record_date": content.get("date"),
        "section": doc.section, "weather": doc.weather or content.get("weather_morning"),
        "work_content": content.get("construction_progress"),
        "problems": content.get("problems_section"), "treatment": None, "remark": None,
        "doc_number": doc.doc_number,
        "content_json": content,
        "created_at": doc.created_at,
    }


async def _push_document(
    db: AsyncSession, doc: Document, sup_code: str, project_code: str, project_name: str,
) -> PushResult:
    rec = _doc_record_dict(doc)
    urls = [_public_file_url(u) for u in (doc.attached_images or [])]
    if sup_code == "006":
        payload = map_sup006(
            rec, project_code=project_code, project_name=project_name,
            created_time=doc.created_at, updated_time=doc.updated_at, photo_urls=urls,
        )
        return await push_payload(SUP_ENDPOINTS["006"], payload)
    if sup_code == "007":
        content = doc.content or {}
        payload = map_sup007(
            rec, project_code=project_code, project_name=project_name,
            report_month=str(content.get("period") or content.get("report_period") or ""),
            created_time=doc.created_at, updated_time=doc.updated_at,
            attachment_urls=urls,
        )
        return await push_payload(SUP_ENDPOINTS["007"], payload)
    raise HTTPException(400, f"SUP-{sup_code} 不支持从文书推送")


def _wrap_push_error(exc: Exception, sup_code: str, rid: int, title: str) -> PushReplyItem:
    if isinstance(exc, MissingRequiredError):
        error = f"必填字段缺失（记录数据不齐，补齐后再推）：{exc}"
    elif isinstance(exc, PushNotConfiguredError):
        error = f"凭证未配置：{exc}"
    else:
        error = f"推送异常：{exc}"
    return PushReplyItem(sup_code=sup_code, sup_label=SUP_LABELS.get(sup_code, sup_code),
                         record_id=rid, record_title=title, ok=False, error=error)


def _ok_item(result: PushResult, sup_code: str, rid: int, title: str) -> PushReplyItem:
    return PushReplyItem(sup_code=sup_code, sup_label=SUP_LABELS.get(sup_code, sup_code),
                         record_id=rid, record_title=title, ok=True, status=result.status)


@router.post("/one", response_model=PushReply)
async def push_one(
    req: PushOneRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按记录推送（管理端「推送到穗建通」按钮）。

    权限口径：settings（总监/公司级）。推送要落到具体项目的编码映射上，
    公司级「全部项目」视角下先拒绝——先切到具体项目再推。
    """
    if not user.has_permission("settings"):
        raise HTTPException(403, "推送穗建通需要系统设置权限（总监/管理员）")
    project_id = require_project_id(user)
    code = project_code_for(project_id)
    if not code:
        raise HTTPException(400, "本项目未配置穗建通项目编码（SZCJ_PROJECT_CODES）")
    project = await db.get(Project, project_id)
    project_name = project.name if project else ""

    sup, rid = req.sup_code, req.record_id
    title = f"{SUP_LABELS.get(sup)}#{rid}"
    try:
        if sup in ("001", "002", "005", "012", "021"):
            r = await db.get(WorkRecord, rid)
            if not r or not can_read_project(r.project_id, user):
                raise HTTPException(404, "工作记录不存在")
            result = await _push_workrecord(db, r, sup, code, project_name)
        elif sup == "003":
            p = await db.get(ParallelInspection, rid)
            if not p or not can_read_project(p.project_id, user):
                raise HTTPException(404, "平行检验记录不存在")
            result = await _push_parallel(db, p, code)
        elif sup in ("006", "007"):
            doc = await db.get(Document, rid)
            if not doc or not can_read_project(doc.project_id, user):
                raise HTTPException(404, "文书不存在")
            result = await _push_document(db, doc, sup, code, project_name)
        else:
            raise HTTPException(400, f"未知 SUP 接口：{sup}")
    except HTTPException:
        raise
    except MissingRequiredError as exc:
        return PushReply(total=1, succeeded=0, failed=1, detail=str(exc),
                         items=[_wrap_push_error(exc, sup, rid, title)],
                         failures=[_wrap_push_error(exc, sup, rid, title)])
    except PushNotConfiguredError as exc:
        return PushReply(total=1, succeeded=0, failed=1, not_configured=True, detail=str(exc),
                         items=[_wrap_push_error(exc, sup, rid, title)],
                         failures=[_wrap_push_error(exc, sup, rid, title)])
    except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批回执
        logger.exception("SUP-%s 推送记录 %s 失败", sup, rid)
        return PushReply(total=1, succeeded=0, failed=1, detail=str(exc),
                         items=[_wrap_push_error(exc, sup, rid, title)],
                         failures=[_wrap_push_error(exc, sup, rid, title)])
    item = _ok_item(result, sup, rid, title)
    return PushReply(total=1, succeeded=1, failed=0, items=[item])


@router.post("/range", response_model=PushReply)
async def push_range(
    req: PushRangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按日期范围批量推当前项目（近 7 天缺省）。

    分桶取数：巡视/旁站/验收/见证走 WorkRecord，平行检验走 ParallelInspection，
    日志/月报走 Document。逐条推送逐条回执，单条失败不断整批。
    """
    if not user.has_permission("settings"):
        raise HTTPException(403, "推送穗建通需要系统设置权限（总监/管理员）")
    project_id = require_project_id(user)
    code = project_code_for(project_id)
    if not code:
        raise HTTPException(400, "本项目未配置穗建通项目编码（SZCJ_PROJECT_CODES）")
    project = await db.get(Project, project_id)
    project_name = project.name if project else ""

    today = date.today()
    try:
        d_from = date.fromisoformat(req.date_from) if req.date_from else today.fromordinal(today.toordinal() - 6)
        d_to = date.fromisoformat(req.date_to) if req.date_to else today
    except ValueError as exc:
        raise HTTPException(400, f"日期格式错误（需 yyyy-MM-dd）：{exc}") from exc
    if d_from > d_to:
        raise HTTPException(400, "date_from 晚于 date_to")

    sups = [s for s in req.sup_codes if s in SUP_ENDPOINTS]
    if not sups:
        raise HTTPException(400, "sup_codes 为空或全非法")

    not_configured = False
    items: list[PushReplyItem] = []
    failures: list[PushReplyItem] = []

    def emit(result: PushResult | None, sup: str, rid: int, title: str, exc: Exception | None = None):
        nonlocal not_configured
        if exc is not None:
            if isinstance(exc, PushNotConfiguredError):
                not_configured = True
            failures.append(_wrap_push_error(exc, sup, rid, title))
            items.append(failures[-1])
            return
        ok = _ok_item(result, sup, rid, title)
        items.append(ok)

    # ---- WorkRecord 桶（001/002/005/012/021）----
    wr_types = {"001": "巡视", "002": "旁站", "005": "验收", "012": "见证", "021": "见证"}
    wanted_types = {t for s, t in wr_types.items() if s in sups}
    if wanted_types:
        rows = (await db.execute(
            select(WorkRecord).where(
                WorkRecord.project_id == project_id,
                WorkRecord.record_type.in_(wanted_types),
                WorkRecord.created_at >= datetime.combine(d_from, datetime.min.time()),
                WorkRecord.created_at <= datetime.combine(d_to, datetime.max.time()),
            ).order_by(WorkRecord.id)
        )).scalars().all()
        # 一条见证记录可能同时进 012 与 021 两个接口（各自必填不同）
        sups_for_type = {}
        for s, t in wr_types.items():
            if s in sups:
                sups_for_type.setdefault(t, []).append(s)
        for r in rows:
            for sup in sups_for_type.get(r.record_type, []):
                title = f"{SUP_LABELS[sup]}#{r.id}"
                try:
                    result = await _push_workrecord(db, r, sup, code, project_name)
                    emit(result, sup, r.id, title)
                except PushNotConfiguredError as exc:
                    emit(None, sup, r.id, title, exc)
                except MissingRequiredError as exc:
                    emit(None, sup, r.id, title, exc)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("SUP-%s 批量推送记录 %s 失败", sup, r.id)
                    emit(None, sup, r.id, title, exc)

    # ---- ParallelInspection 桶（003）----
    if "003" in sups:
        rows = (await db.execute(
            select(ParallelInspection).where(
                ParallelInspection.project_id == project_id,
                ParallelInspection.created_at >= datetime.combine(d_from, datetime.min.time()),
                ParallelInspection.created_at <= datetime.combine(d_to, datetime.max.time()),
            ).order_by(ParallelInspection.id)
        )).scalars().all()
        for p in rows:
            title = f"平行检验#{p.id}"
            try:
                result = await _push_parallel(db, p, code)
                emit(result, "003", p.id, title)
            except PushNotConfiguredError as exc:
                emit(None, "003", p.id, title, exc)
            except MissingRequiredError as exc:
                emit(None, "003", p.id, title, exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("SUP-003 批量推送 %s 失败", p.id)
                emit(None, "003", p.id, title, exc)

    # ---- Document 桶（006/007）----
    doc_types = {"006": DocType.SUPERVISION_LOG.value, "007": DocType.MONTHLY.value}
    wanted_docs = {s: t for s, t in doc_types.items() if s in sups}
    if wanted_docs:
        rows = (await db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type.in_(wanted_docs.values()),
                Document.created_at >= datetime.combine(d_from, datetime.min.time()),
                Document.created_at <= datetime.combine(d_to, datetime.max.time()),
            ).order_by(Document.id)
        )).scalars().all()
        for doc in rows:
            sup = next(s for s, t in wanted_docs.items() if t == doc.doc_type)
            title = f"{doc.title}"
            try:
                result = await _push_document(db, doc, sup, code, project_name)
                emit(result, sup, doc.id, title)
            except PushNotConfiguredError as exc:
                emit(None, sup, doc.id, title, exc)
            except MissingRequiredError as exc:
                emit(None, sup, doc.id, title, exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("SUP-%s 批量推送文书 %s 失败", sup, doc.id)
                emit(None, sup, doc.id, title, exc)

    succeeded = sum(1 for i in items if i.ok)
    return PushReply(
        total=len(items), succeeded=succeeded, failed=len(items) - succeeded,
        not_configured=not_configured,
        detail=(f"已推送 {succeeded}/{len(items)} 条到穗建通（{d_from} ~ {d_to}）"
                if items else f"该日期范围没有可推送的记录（{d_from} ~ {d_to}）"),
        items=items, failures=failures,
    )


@router.get("/status")
async def push_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """推送就绪状态（前端按钮门控）：凭证是否配齐 + 当前项目编码映射。"""
    configured = bool(
        settings.szcj_push_base_url and settings.szcj_app_code and settings.szcj_bearer_token
    )
    project_id = getattr(user, "project_id", None)
    return {
        "configured": configured,
        "sup_codes": [
            {"code": c, "label": SUP_LABELS[c], "endpoint": SUP_ENDPOINTS[c]}
            for c in SUP_ENDPOINTS
        ],
        "project_code": project_code_for(project_id) if project_id else "",
        "hint": "" if configured else "凭证未配置：联系穗建通管理员下发 appCode/token/地址后填 env",
    }
