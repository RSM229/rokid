"""周期文书自动生成 —— 监理日志 / 周报 / 月报。

这三类文书原本只能由登录用户在客户端手动触发：

- ``POST /api/document/generate-daily-log``  监理日志（PDF，GD-B-220 版式）
- ``POST /api/form-docs/aggregate``          日/周/月省统表（docx）

两个接口都依赖 ``get_current_user``：落款取自当前用户、项目取自当前用户
所属项目。定时任务没有"当前用户"，所以这里抽出**系统态**的生成入口：

- 落款人按项目成员角色自动推断（总监 → 总监代表 → 专监 → 监理员）；
- 日期以东八区为准（固定 UTC+8 偏移），不依赖容器 TZ 是否配置正确；
- ``force=False`` 时同一项目同一周期的文书只保留一份，重复触发不会堆重复件。

手动接口与定时任务共用这里的实现，避免"两套口径"日后各自漂移。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session
from app.models.document import DocStatus, DocType, Document
from app.models.hazard import Hazard, HazardStatus
from app.models.rectification import RectificationRecord
from app.models.project_member import ProjectMember
from app.models.user import Project, User, UserRole
from app.models.work_record import WorkRecord, WorkRecordType
from app.models.parallel import ParallelInspection
from app.models.meeting import MeetingMinute
from app.services.record_fields import problem_text, project_fields
from app.services.doc_generator import generate_daily_log_pdf
from app.services.form_registry import FORM_BY_CODE
from app.services.form_renderer import render_form
from app.services.form_routing import (
    daily_log_form_code,
    is_municipal,
    safety_log_form_code,
)

logger = logging.getLogger(__name__)

# 用固定 +08:00 偏移而不是 ZoneInfo("Asia/Shanghai")：容器里可能没装 tzdata，
# ZoneInfo 会抛 ZoneInfoNotFoundError；而中国自 1991 年起无夏令时，固定偏移与
# 真实时区完全等价。
CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

PERIOD_FORM_CODES = {
    "day": "gd_b_220_rizhi",
    "week": "gd_b_223_zhoubao",
    "month": "gd_b_221_yuebao",
}

# 自动落款时的人员优先级：监理日志/周报/月报在实务上由总监签发。
# Step 2：改用本地岗位 key（接口出参不转中文），与 project_members.role 同源。
ROLE_PRIORITY = (
    UserRole.CHIEF_SUPERVISOR.value,
    UserRole.DEPUTY_CHIEF.value,
    UserRole.SPECIAL_SUPERVISOR.value,
    UserRole.INSPECTOR.value,
)

WEEKDAY_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def now_cn() -> datetime:
    """当前东八区时间。"""
    return datetime.now(CN_TZ)


def doc_type_for(form_code: str, record_type: str | None = None) -> str:
    """表式 code -> 现有 DocType 枚举值（尽量复用历史类型）。"""
    if record_type == "验收" or form_code == "acceptance_record":
        return DocType.ACCEPTANCE.value
    if record_type == "见证":
        return DocType.WITNESS_SAMPLING.value
    mapping = {
        "gd_b_212_pangzhan": DocType.SIDE_STATION.value,
        "gd_b_214_xunshi": DocType.INSPECTION.value,
        "gd_b_213_pingxing": "平行检查记录",
        "gd_b_220_rizhi": DocType.SUPERVISION_LOG.value,
        "jianli_riji": DocType.SUPERVISION_DIARY.value,
        "gd_b_221_yuebao": DocType.MONTHLY.value,
        "gd_b_223_zhoubao": DocType.WEEKLY.value,
        "gd_b_215_tongzhi": DocType.NOTICE_QUALITY.value,
        "gdaq_30308_yinhuan": DocType.RECTIFICATION.value,
        "gdaq_30309_huifu": DocType.RECTIFY_REPLY.value,
        "acceptance_record": DocType.ACCEPTANCE.value,
        "gd_b_219_cailiao": "材料台账",
        "gdaq_30306_weida": "危大工程巡视记录",
        "anquan_jianli_rizhi": "安全监理日志",
        # 市政统表（市政监 / AQSZ 系列）→ 复用同义 DocType，便于前端旧逻辑检索
        "shijian_24_rizhi": DocType.SUPERVISION_LOG.value,
        "shijian_9_pangzhan": DocType.SIDE_STATION.value,
        "shijian_11_xunshi": DocType.INSPECTION.value,
        "aqsz_2_2_anquan_rizhi": "安全监理日志",
        "aqsz_2_5_1_weida": "危大工程巡视记录",
        "aqsz_2_5_2_weida": "危大工程巡视记录",
        "aqsz_2_5_3_weida": "危大工程巡视记录",
        "aqsz_2_5_4_weida": "危大工程巡视记录",
        "aqsz_2_5_5_weida": "危大工程巡视记录",
        "aqsz_2_5_6_weida": "危大工程巡视记录",
        "aqsz_2_5_7_weida": "危大工程巡视记录",
    }
    return mapping.get(form_code, DocType.INSPECTION.value)


def parse_period_range(
    period: str, ref: str | None = None
) -> tuple[date, date, str]:
    """解析周期 -> (起, 止, 中文标签)。周期口径与手动接口保持一致。

    day: ref 为 YYYY-MM-DD；week: ref 为周内任意一天，按周一至周日取整周；
    month: ref 为 YYYY-MM。
    """
    today = now_cn().date()
    if period == "day":
        target = datetime.strptime(ref, "%Y-%m-%d").date() if ref else today
        return target, target, f"{target.year}年{target.month}月{target.day}日"
    if period == "month":
        ym = ref or today.strftime("%Y-%m")
        year, month = int(ym[:4]), int(ym[5:7])
        start = date(year, month, 1)
        nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return start, nxt - timedelta(days=1), f"{year}年{month}月"
    anchor = datetime.strptime(ref, "%Y-%m-%d").date() if ref else today
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    return start, end, f"{start.year}年{start.month}月{start.day}日—{end.month}月{end.day}日"


async def aggregate_records(
    db: AsyncSession, project_id: int, start: date, end: date
) -> dict:
    """聚合时间段内的工作记录/隐患/整改，输出各分节文本。"""
    day_start = datetime.combine(start, datetime.min.time())
    day_end = datetime.combine(end + timedelta(days=1), datetime.min.time())

    records_result = await db.execute(
        select(WorkRecord)
        .where(
            and_(
                WorkRecord.project_id == project_id,
                or_(and_(WorkRecord.record_date >= start, WorkRecord.record_date <= end), and_(WorkRecord.record_date.is_(None), WorkRecord.created_at >= day_start, WorkRecord.created_at < day_end)),
            )
        )
        .order_by(WorkRecord.created_at)
    )
    records = records_result.scalars().all()
    hazards_result = await db.execute(
        select(Hazard)
        .where(
            and_(
                Hazard.project_id == project_id,
                Hazard.created_at >= day_start,
                Hazard.created_at < day_end,
            )
        )
        .order_by(Hazard.created_at)
    )
    hazards = hazards_result.scalars().all()
    rects_result = await db.execute(
        select(RectificationRecord).join(Hazard, Hazard.id == RectificationRecord.hazard_id).where(
            Hazard.project_id == project_id,
            RectificationRecord.created_at >= day_start,
            RectificationRecord.created_at < day_end,
        )
    )
    rects = rects_result.scalars().all()
    parallel_result = await db.execute(
        select(ParallelInspection)
        .where(
            ParallelInspection.project_id == project_id,
            ParallelInspection.inspect_date >= start.isoformat(),
            ParallelInspection.inspect_date <= end.isoformat(),
        )
        .order_by(ParallelInspection.inspect_date, ParallelInspection.created_at)
    )
    parallels = parallel_result.scalars().all()

    # 进度
    progress_lines = []
    by_type: dict[str, list[str]] = {"巡视": [], "验收": [], "旁站": [], "见证": []}
    other_lines = []
    for r in records:
        loc = f"{r.section or ''} {r.location or ''}".strip()
        if r.record_type == "巡视":
            by_type["巡视"].append(f"{loc}：{r.work_content or r.inspection_remark or ''}".strip("："))
            progress_lines.append(f"巡视{loc}，{r.work_content or '现场巡查'}")
        elif r.record_type == "验收":
            by_type["验收"].append(
                f"{loc}：{(r.content_json or {}).get('acceptance_type') or '验收'}，"
                f"结果{r.acceptance_result or ''}"
            )
            progress_lines.append(f"对{loc}进行{(r.content_json or {}).get('acceptance_type') or '验收'}")
        elif r.record_type == "旁站":
            by_type["旁站"].append(f"{loc}：{r.station_content or ''}")
            progress_lines.append(f"旁站{loc}（{r.station_project_name or ''}）")
        elif r.record_type == "见证":
            by_type["见证"].append(
                f"{r.witness_material_name or ''} {r.witness_material_spec or ''}，{loc}"
            )
            progress_lines.append(f"见证取样{r.witness_material_name or '材料'}送检")
        else:
            other_lines.append(r.work_content or r.remark or "")
    progress = "；".join(ln for ln in progress_lines if ln) or "无施工记录。"

    # 问题
    problems = [f"{r.record_type} {r.section or ''}：{problem_text(r)}" for r in records if r.problems or r.treatment]
    closed_ids = {rect.hazard_id for rect in rects if rect.recheck_passed}
    for h in hazards:
        status_text = "已闭环" if h.status == "已闭环" or h.id in closed_ids else "已通知整改"
        problems.append(
            f"{status_text}：{h.section or ''} {h.location or ''} "
            f"{h.top1_risk or '安全隐患'}（{h.risk_category or '安全'}类）"
        )
    problems_text = "\n".join(problems) if problems else "无安全隐患及质量问题。"

    material_sec = "；".join(by_type["见证"]) or "无材料进场见证取样。"
    sup_sec = "；".join([*by_type["巡视"], *by_type["旁站"]]) or "无旁站及巡视记录。"
    acc_sec = "；".join(by_type["验收"]) or "无验收记录。"
    other_sec = "；".join(other_lines) or "无。"
    parallel_sec = "\n".join(
        f"{item.part} {item.item}：{item.result_detail}，结论：{item.conclusion}，"
        f"依据：{item.basis}，方法：{item.method or ''}"
        for item in parallels
    ) or "本期无平行检验记录。"

    return {
        "work_records": len(records),
        "hazards": len(hazards),
        "construction_progress": progress,
        "material_section": material_sec,
        "supervision_section": sup_sec,
        "acceptance_section": acc_sec,
        "other_work_section": other_sec,
        "problems_section": problems_text,
        "parallel_section": parallel_sec,
        "hazard_details": [
            {
                "top1_risk": h.top1_risk,
                "risk_category": h.risk_category,
                "section": h.section,
                "location": h.location,
                "status": h.status,
            }
            for h in hazards
        ],
    }


def _json_dumps_zh(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def _municipal_rizhi_content(content: dict) -> dict:
    """房建监-24 聚合产物 → 市政监-24 键映射。

    市政监-24（官方版式）栏目与 GD-B-220 不同：作业面明细三行 +
    七个整行大内容区（现场巡视发现问题/工程质量/材料试件/安全文明/
    人员往来/监理主要工作/设计变更）+ 其它重要事项。聚合内容按栏目语义
    分发，避免整段塞进单一格子。
    """
    # 聚合产物的「当日无…」占位语不属于业务事实：市政监-24 整行大内容区
    # 留空更符合实地填写习惯（模板标签后不追加占位文案）。
    _PLACEHOLDER = (
        "当日无施工记录。",
        "当日无材料进场及见证取样。",
        "当日无旁站及巡视记录。",
        "当日无验收记录。",
        "当日无安全隐患及质量问题。",
        "当日无平行检查记录。",
        "当日无危大工程相关安全状况。",
        "当日无需处理的安全问题。",
        "当日无隐患整改事项。",
        "无施工记录。",
        "无安全隐患及质量问题。",
        "无材料进场见证取样。",
        "无旁站及巡视记录。",
        "无验收记录。",
        "无。",
        "暂无。",
    )

    def _strip_placeholder(text: str) -> str:
        out = str(text or "")
        for ph in _PLACEHOLDER:
            out = out.replace(ph, "").replace(f"{ph}\n", "").replace(f"\n{ph}", "")
        return out.strip().strip("\n")

    def _clean(key: str) -> str:
        return _strip_placeholder(content.get(key))

    section = _clean("supervision_section")
    problems = _clean("problems_section")
    acceptance = _clean("acceptance_section")
    material = _clean("material_section")
    other = _clean("other_work_section")
    meeting = _clean("meeting_section")
    document = _clean("document_section")
    misc = _clean("misc_section")

    # 作业面明细：施工进展按物理行拆入「作业面/进展」列，至多三行
    surfaces = []
    for line in _strip_placeholder(content.get("construction_progress")).splitlines():
        line = line.strip()
        if line:
            surfaces.append({"surface": "", "progress": line, "workers": "", "machinery": ""})
        if len(surfaces) >= 3:
            break

    def _join(*parts: str) -> str:
        return "\n".join(p for p in parts if p and p.strip())

    return {
        **content,
        "work_surfaces": surfaces,
        "content_inspection": _join(section, problems),
        "content_quality": acceptance,
        "content_material": material,
        "content_safety": _join(problems, other) if problems and problems not in (section, "") else "",
        "content_personnel": meeting,
        "content_supervision": _join(section, document),
        "content_change": misc,
        "content_other": other if other and other not in (section, problems) else "",
        "weather": str(content.get("weather_morning") or content.get("weather") or ""),
        "chief_supervisor": str(content.get("supervisor") or ""),
        "recorder": str(content.get("recorder_name") or content.get("supervisor") or ""),
    }


async def llm_polish(agg: dict, period_label: str, form_name: str) -> dict:
    """把规则汇总内容交给 LLM 润色为规范文书行文。失败时原样返回。"""
    try:
        from app.services.ai_client import ai_client

        system_prompt = (
            "你是资深总监理工程师，负责撰写规范的监理文书。"
            "请基于提供的原始汇总素材，按监理文书规范行文润色，语言正式、客观、精炼。"
            "输出 JSON 对象，字段：construction_progress(当日/当期施工进展)、"
            "supervision_section(监理工作情况)、problems_section(存在问题及处理情况)、"
            "other_section(其他有关事项)。只输出 JSON。"
        )
        user_content = (
            f"文书类型：{form_name}（{period_label}）\n"
            f"原始素材：\n{_json_dumps_zh(agg)}"
        )
        from app.services.model_gateway import ModelGatewayError

        try:
            raw = await ai_client.chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.4,
                max_tokens=1024,
            )
        except (ModelGatewayError, RuntimeError) as exc:
            logger.warning("汇总 LLM 润色失败，使用规则汇总：%s", exc)
            return agg
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        parsed = json.loads(cleaned)
        allowed = {
            "construction_progress",
            "supervision_section",
            "problems_section",
            "other_section",
        }
        for key, value in parsed.items():
            if key in allowed and isinstance(value, str) and value.strip():
                agg[key] = value
        agg["llm_polished"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("汇总润色降级：%s", exc)
    return agg


async def pick_creator(db: AsyncSession, project_id: int) -> Optional[User]:
    """挑一个可作落款人与创建人的项目成员。

    定时任务没有登录用户，而 ``documents.creator_id`` 是非空列 —— 必须落到一个
    真实存在的用户，否则插入直接失败（曾以 creator_id=None 试过，报
    ``Column 'creator_id' cannot be null``）。监理日志/周报/月报在实务上由总监
    签发，因此按 总监理工程师 → 总监代表 → 专业监理工程师 → 监理员 次序取第一个
    在职成员；都不匹配则退一步取任意在职成员，再退到任意成员（含已停用）。

    项目下一个人都没有时返回 ``None`` —— 调用方应跳过该项目：这类项目多为自动化
    测试遗留，凭空指定创建人只会把文书台账搞乱。

    Step 2（设计说明 §7.8.4）：候选人与岗位都改从 ``project_members`` 取。
    旧写法按 ``User.project_id``（上下文）筛人、按全局 ``User.role``（单值）匹配，
    会漏掉跨项目兼职者、也会把"已转场但上下文还挂在本项目"的人当成在岗成员。
    现在按成员归属筛人、按主岗 ∪ 附加岗匹配（一人多岗也能命中）。
    """
    result = await db.execute(
        select(User, ProjectMember.role, ProjectMember.extra_roles)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.left_at.is_(None),
        )
    )
    rows = list(result.all())
    if not rows:
        return None

    def _held(primary: str | None, extra: list | None) -> set[str]:
        held: set[str] = set()
        if primary:
            held.add(primary)
        for item in extra or []:
            if isinstance(item, str) and item:
                held.add(item)
        return held

    # 第一轮：按岗位优先级匹配在职成员
    for role in ROLE_PRIORITY:
        for user, primary, extra in rows:
            if user.is_active and role in _held(primary, extra):
                return user
    # 兜底：任意在职成员 → 任意成员（含已停用）
    for user, _primary, _extra in rows:
        if user.is_active:
            return user
    return rows[0][0]


async def _find_existing(
    db: AsyncSession,
    project_id: int,
    *,
    title: str,
    doc_type: str | None = None,
    form_code: str | None = None,
) -> Document | None:
    conditions = [Document.project_id == project_id, Document.title == title]
    if doc_type:
        conditions.append(Document.doc_type == doc_type)
    if form_code:
        conditions.append(Document.form_code == form_code)
    result = await db.execute(
        select(Document).where(and_(*conditions)).order_by(Document.id.desc()).limit(1)
    )
    return result.scalars().first()


async def generate_supervision_log(
    db: AsyncSession,
    project_id: int,
    target_date: date,
    *,
    supervisor: Optional[User] = None,
    extras: dict | None = None,
    extra_fields: dict | None = None,
    force: bool = True,
    narrative: str | None = None,
    author_id: Optional[int] = None,
    record_ids: Optional[list[int]] = None,
) -> dict:
    """生成指定项目、指定日期的监理日志（PDF，GD-B-220 版式）或监理日记。

    ``author_id`` 为空 = 项目级监理日志（现状）：聚合当日全项目的工作记录、
    安全隐患、整改记录，按模板分区成文。
    ``author_id`` 有值 = 个人级监理日记：只聚合该作者当日的工作记录
    （WorkRecord.creator_id）、隐患（Hazard.reporter_id）、平行检查与会议
    （user_id），落款为作者本人；渲染复用 GD-B-220 版式。
    ``force=True``（手动接口）每次都新建；``force=False``（定时任务）在当天
    已有同名日志时直接返回 skipped —— 既避免多 worker 重复触发堆件，也避免
    覆盖客户端已复核/已编辑过的那一份。
    """
    is_diary = author_id is not None
    kind_label = "监理日记" if is_diary else "监理日志"
    title = f"{kind_label}_{target_date.isoformat()}"
    if not force:
        existing = await _find_existing(
            db, project_id, title=title,
            doc_type=DocType.SUPERVISION_DIARY.value if is_diary else DocType.SUPERVISION_LOG.value,
        )
        if existing is not None:
            return {
                "doc_id": existing.id,
                "doc_number": (existing.content or {}).get("doc_number", ""),
                "status": existing.status,
                "pdf_url": existing.pdf_path,
                "skipped": True,
                "summary": {"reason": "当日日志已存在"},
            }

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

    # 记录筛选（2026-11-18 修正勾选语义）：勾选生成时以勾选为准——
    # 勾了哪些就汇总哪些，不再叠加「record_date=目标日」窗口，否则补录/跨日
    # 勾选记录会被日期条件悄悄滤掉，生成出一份全空日记（生产实证）。
    # 未勾选（按当前全部记录生成）时仍按「业务日期=目标日 or 创建时间在当日」聚合。
    if is_diary and record_ids:
        record_filters = [
            WorkRecord.project_id == project_id,
            WorkRecord.creator_id == author_id,
            WorkRecord.id.in_(record_ids),
        ]
    else:
        record_filters = [
            WorkRecord.project_id == project_id,
            or_(WorkRecord.record_date == target_date, and_(WorkRecord.record_date.is_(None), WorkRecord.created_at >= day_start, WorkRecord.created_at < day_end)),
            *((WorkRecord.creator_id == author_id,) if is_diary else ()),
        ]
    records_result = await db.execute(
        select(WorkRecord)
        .where(and_(*record_filters))
        .order_by(WorkRecord.created_at)
    )
    work_records = records_result.scalars().all()

    hazards_result = await db.execute(
        select(Hazard)
        .where(
            and_(
                Hazard.project_id == project_id,
                Hazard.created_at >= day_start,
                Hazard.created_at < day_end,
                *((Hazard.reporter_id == author_id,) if is_diary else ()),
            )
        )
        .order_by(Hazard.created_at)
    )
    hazards = hazards_result.scalars().all()

    rectifications_result = await db.execute(
        select(RectificationRecord).join(Hazard, Hazard.id == RectificationRecord.hazard_id).where(
            Hazard.project_id == project_id,
            or_(and_(RectificationRecord.created_at >= day_start, RectificationRecord.created_at < day_end),
                and_(RectificationRecord.rectified_at >= day_start, RectificationRecord.rectified_at < day_end),
                and_(RectificationRecord.recheck_at >= day_start, RectificationRecord.recheck_at < day_end)),
            *((Hazard.reporter_id == author_id,) if is_diary else ()),
        )
    )
    rectifications = rectifications_result.scalars().all()

    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    project_name = project.name if project else ""
    parallels = list((await db.execute(
        select(ParallelInspection).where(
            ParallelInspection.project_id == project_id,
            ParallelInspection.inspect_date == target_date.isoformat(),
            *((ParallelInspection.user_id == author_id,) if is_diary else ()),
        )
    )).scalars().all())
    meetings = list((await db.execute(
        select(MeetingMinute).where(
            MeetingMinute.project_id == project_id,
            MeetingMinute.meeting_date == target_date.isoformat(),
            *((MeetingMinute.user_id == author_id,) if is_diary else ()),
        )
    )).scalars().all())

    weekday = WEEKDAY_CN[target_date.weekday()]

    # ===== 1. 当日施工进展情况 =====
    progress_items = []
    for r in work_records:
        # 「待定」记录类型未定，不进正式文书（用户贴标签后重新生成）
        if r.record_type in ("待定", "other") or not r.work_content:
            continue
        place = f"{r.section or ''} {r.location or ''}".strip()
        progress_items.append(f"{place}：{r.work_content}" if place else str(r.work_content))
    # 🔴 叙述性监理日报/日记：用户粘贴/口述的当日叙述是「用户认定的事实」，优先级最高。
    # 有叙述时**只用叙述**，不再把系统结构化记录聚合成「（系统记录补充：…）」追加进去——
    # 那是正式文书，出现「系统记录补充」字样不合理，且与用户原文内容重复
    # （2026-11-03 用户明确要求去掉）。
    # 历史背景：10-27 曾因 `narrative and not progress_items` 条件把用户原文整段忽略，
    # 生成的文书与用户所述完全不符；现在保留「叙述优先」，只去掉追加块。
    if narrative:
        construction_progress = str(narrative).strip()
    else:
        construction_progress = (
            "\n".join(progress_items) if progress_items else "当日无施工记录。"
        )

    # ===== 2. 当日监理工作情况 =====
    witness_records = [
        r for r in work_records if r.record_type == WorkRecordType.WITNESS.value
    ]
    if witness_records:
        material_lines = []
        for r in witness_records:
            material_lines.append(
                f"{r.witness_material_name or ''} {r.witness_material_spec or ''}，"
                f"部位：{r.section or ''} {r.location or ''}"
            )
        material_section = "\n".join(material_lines)
    else:
        material_section = "当日无材料进场及见证取样。"

    inspection_records = [
        r for r in work_records if r.record_type == WorkRecordType.INSPECTION.value
    ]
    station_records = [
        r for r in work_records if r.record_type == WorkRecordType.STATION.value
    ]
    sup_lines = []
    for r in inspection_records:
        sup_lines.append(f"巡视{r.section or ''}：{r.inspection_remark or r.work_content or ''}")
    for r in station_records:
        duration = ""
        if r.station_start_time and r.station_end_time:
            duration = (
                f"（{r.station_start_time.strftime('%H:%M')}-"
                f"{r.station_end_time.strftime('%H:%M')}）"
            )
        place = f"{r.station_project_name or ''} {r.station_location or ''}".strip()
        sup_lines.append(f"旁站{place}{duration}：{r.station_content or ''}".replace("：：", "："))
    supervision_section = "\n".join(sup_lines) if sup_lines else "当日无旁站及巡视记录。"

    acceptance_records = [
        r for r in work_records if r.record_type == WorkRecordType.ACCEPTANCE.value
    ]
    if acceptance_records:
        acc_lines = []
        for r in acceptance_records:
            acc_lines.append(
                f"{r.acceptance_guide_ref or '验收'}：{r.section or ''} "
                f"{r.location or ''}，结果：{r.acceptance_result or ''}"
            )
        acceptance_section = "\n".join(acc_lines)
    else:
        acceptance_section = "当日无验收记录。"

    other_records = [
        r
        for r in work_records
        if r.record_type
        not in (
            WorkRecordType.INSPECTION.value,
            WorkRecordType.ACCEPTANCE.value,
            WorkRecordType.WITNESS.value,
            WorkRecordType.STATION.value,
            "待定",  # 类型未定的记录不进正式文书
            "other",
        )
    ]
    if other_records:
        other_lines = [
            f"{r.section or ''} {r.location or ''}：{r.work_content or r.remark or ''}"
            for r in other_records
        ]
        other_section = "\n".join(other_lines)
    else:
        other_section = "无。"

    # ===== 3. 当日存在问题及处理情况 =====
    problem_items = [
        f"{r.section or ''}：{problem_text(r)}"
        for r in work_records
        if (r.problems or r.treatment) and r.record_type not in ("待定", "other")
    ]
    for p in parallels:
        if p.conclusion == "不合格" or p.treatment:
            problem_items.append(f"平行检查 {p.part} {p.item}：{p.result_detail}；处理情况：{p.treatment or '待补填'}")
    for rectification in rectifications:
        hazard = await db.get(Hazard, rectification.hazard_id)
        problem_items.append(f"整改/复查 {getattr(hazard, 'top1_risk', '') or ''}：{rectification.recheck_remark or '尚未填写复查处理意见'}；整改时间：{rectification.rectified_at or '待补填'}")
    for h in hazards:
        if h.status == HazardStatus.CLOSED.value:
            status_text = "已完成"
        else:
            status_text = "已通知"
        risk_desc = h.top1_risk or "安全隐患"
        location = f"{h.section or ''} {h.location or ''}".strip()
        problem_items.append(
            f"{status_text} {location}：{risk_desc}（{h.risk_category or '安全'}类）"
        )
    problems_section = (
        "\n".join(problem_items) if problem_items else "当日无安全隐患及质量问题。"
    )

    # ===== 4. 其他有关事项 =====
    # 注意 "发文" -> "重要发文" 的映射：content_json 里的 category 值与
    # GD-B-220 版面的栏目名并不逐字相同，按键名直接匹配会把发文落到"其他"。
    category_bucket = {
        "会议": "会议",
        "发文": "重要发文",
        "闭合记录": "闭合记录",
        "资料上报": "资料上报",
    }
    buckets: dict[str, list[str]] = {
        "会议": [],
        "重要发文": [],
        "闭合记录": [],
        "资料上报": [],
        "其他": [],
    }
    for r in other_records:
        cat = (r.content_json or {}).get("category", "其他") if r.content_json else "其他"
        text = str(r.work_content or r.remark or "")
        buckets[category_bucket.get(cat, "其他")].append(text)

    # ===== 当日发出的监理文书（通知单/整改单等「发的单」）进「重要发文」栏 =====
    # 项目日志收全项目当日新发的单；个人日记只收作者本人当日创建的。
    try:
        issued_docs = (
            await db.execute(
                select(Document)
                .where(
                    Document.project_id == project_id,
                    Document.created_at >= day_start,
                    Document.created_at < day_end,
                    Document.doc_type.in_((
                        DocType.NOTICE_QUALITY.value,
                        DocType.RECTIFICATION.value,
                        DocType.RECTIFY_REPLY.value,
                        "安全监理日志",
                        "危大工程巡视记录",
                    )),
                    *((Document.creator_id == author_id,) if is_diary else ()),
                )
                .order_by(Document.created_at)
            )
        ).scalars().all()
        for dd in issued_docs:
            dd_no = (dd.content or {}).get("doc_number") or f"#{dd.id}"
            dd_title = dd.title or dd.doc_type
            buckets["重要发文"].append(f"{dd.doc_type} {dd_title}（{dd_no}）")
    except Exception as exc:  # noqa: BLE001 - 发文归集失败不阻断生成
        logger.warning("监理日志/日记发文归集失败，跳过：%s", exc)

    from app.services.weather import daily_weather
    ex = await daily_weather(project, target_date, extras)
    parallel_section = "\n".join(f"{p.part} {p.item}：{p.result_detail}，结论：{p.conclusion}，依据：{p.basis}，方法：{p.method or ''}" for p in parallels) or "当日无平行检查记录。"
    buckets["会议"].extend(f"{m.title}：{m.content}" for m in meetings)
    signer = supervisor if supervisor is not None else await pick_creator(db, project_id)
    if signer is None:
        raise ValueError(
            f"项目 {project_id} 下没有成员，无法确定监理日志的创建人"
        )
    # 日记：落款必须回到作者本人（supervisor 可能是定时任务推断的他人，
    # 或生成人不是作者本人——作者是过滤维度，落款与过滤必须同一个人）。
    if is_diary:
        if author_id:
            me = await db.get(User, author_id)
            if me is not None:
                signer = me
    supervisor_name = signer.real_name or ""
    supervisor_level = signer.supervisor_level or ""

    # 🔴 叙述模式收口：用户粘贴/口述了完整日志正文时，文书只呈现用户所述，其余栏目一律
    # 回到模板默认语，不再把系统结构化记录（含照片水印 OCR 文本「现场水印显示…」）混进去
    # ——2026-11-03 用户两次指出「还是有其他不是我输入的文字」。
    # 需要合并系统记录时走「不贴正文、直接生成今天的监理日志」的聚合路径。
    if narrative:
        material_section = "当日无材料进场及见证取样。"
        supervision_section = "当日无旁站及巡视记录。"
        acceptance_section = "当日无验收记录。"
        other_section = "无。"
        problems_section = "当日无安全隐患及质量问题。"
        parallel_section = "当日无平行检查记录。"
        for _bucket in buckets:
            buckets[_bucket] = []

    content = {
        "project_name": project_name,
        # 日记按「项目+日期+作者」编号：同一天多人各写各的日记，不能重号。
        "doc_number": (
            f"GD-JL-RJ-{project_id}-{target_date.strftime('%Y%m%d')}-{author_id}"
            if is_diary else f"GD-JL-{project_id}-{target_date.strftime('%Y%m%d')}"
        ),
        "date": target_date.isoformat(),
        "weekday": weekday,
        "temperature_high": ex.get("temp_high", ""),
        "temperature_low": ex.get("temp_low", ""),
        "weather_morning": ex.get("weather", ""),
        "weather_afternoon": ex.get("weather", ""),
        "weather_night": ex.get("weather", ""),
        **project_fields(project),
        "weather_source": ex.get("weather_source", ""),
        "weather_missing": ex.get("weather_missing", []),
        "parallel_section": parallel_section,
        "supervisor": supervisor_name,
        "supervisor_level": supervisor_level,
        # 仅日记带：渲染器据此在「记录人（签名）」格回填本人姓名。
        **({"recorder_name": supervisor_name} if is_diary else {}),
        "construction_progress": construction_progress,
        "material_section": material_section,
        "supervision_section": supervision_section,
        "acceptance_section": acceptance_section,
        "other_work_section": other_section,
        "problems_section": problems_section,
        "meeting_section": "\n".join(buckets["会议"]) if buckets["会议"] else "  无。",
        "document_section": "\n".join(buckets["重要发文"]) if buckets["重要发文"] else "  无。",
        "closure_section": "\n".join(buckets["闭合记录"]) if buckets["闭合记录"] else "  无。",
        "report_section": "\n".join(buckets["资料上报"]) if buckets["资料上报"] else "  无。",
        "misc_section": "\n".join(buckets["其他"]) if buckets["其他"] else "  无。",
    }

    # D02：透传 extra_fields（用户在生成日志时显式补充的字段，归一后已是 canonical 键）
    # weather -> 覆盖当日三时段天气格；supervision_check(7_12 上级/质监站检查) 与
    # other_section(7_13 其他有关事项) 本就是渲染器 _rizhi_other_lines 读取的键，直接落。
    extra_fields = _canon_extra_fields(extra_fields, "gd_b_220_rizhi")
    if extra_fields:
        w = extra_fields.get("weather")
        if w:
            content["weather_morning"] = w
            content["weather_afternoon"] = w
            content["weather_night"] = w
            content["weather_source"] = "人工补充"
        sc = extra_fields.get("supervision_check")
        if sc:
            content["supervision_check"] = str(sc)
        os_ = extra_fields.get("other_section")
        if os_:
            existing = content.get("misc_section") or ""
            content["misc_section"] = (
                existing + "\n" + str(os_) if existing and existing != "  无。" else str(os_)
            )

    doc = Document(
        doc_type=DocType.SUPERVISION_DIARY.value if is_diary else DocType.SUPERVISION_LOG.value,
        title=title,
        content=content,
        form_code="jianli_riji" if is_diary else daily_log_form_code(project),
        project_id=project_id,
        creator_id=signer.id,
        # 日记是个人当日汇总：生成即生效，不进审批/签认流转。
        status=(DocStatus.ARCHIVED.value if is_diary else DocStatus.DRAFT.value),
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # 产物分叉：日记走 Markdown（个人汇总、文本形态）；日志走省统表 docx→PDF
    # （GD-B-220 / 市政监-24 正式版式）。任一步失败不影响主流程（文书均已入库）。
    diary_suffix = "监理日记" if is_diary else "监理日志"
    if is_diary:
        # 监理日记（个人工作记录）正式产物为 Markdown，不走 docx/PDF 省统表：
        # 归档、全文检索与 AI 复用都用文本；需要打印时再按需转 PDF。
        try:
            md_text = build_supervision_log_markdown(
                content, target_date, doc_title=diary_suffix
            )
            md_filename = f"doc_{doc.id}_{diary_suffix}_{target_date.isoformat()}.md"
            md_path = settings.export_dir / md_filename
            md_path.write_text(md_text, encoding="utf-8")
            doc.markdown_path = f"/exports/{md_filename}"
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - 文书已入库，产物失败不阻断
            logger.warning("%s Markdown 生成失败（文书已入库）：%s", diary_suffix, exc)
    else:
        # 项目级监理日志：保持省统表 docx→PDF 产物（原型 N 表式要求）。
        try:
            from app.services.form_renderer import render_form
            municipal = is_municipal(project)
            # 市政项目日志渲染市政监-24（官方版式）：内容按市政栏目重新分发。
            render_code = daily_log_form_code(project) if municipal else "gd_b_220_rizhi"
            render_content = _municipal_rizhi_content(content) if municipal else content
            docx_out = await asyncio.to_thread(render_form, render_code, render_content)
            doc.docx_path = f"/exports/{docx_out.name}"
            await db.commit()
            from app.services.docx2pdf import convert_docx_to_pdf
            pdf_filename = f"doc_{doc.id}_{diary_suffix}_{target_date.isoformat()}.pdf"
            pdf_path = settings.export_dir / pdf_filename
            await asyncio.to_thread(convert_docx_to_pdf, str(docx_out), str(pdf_path))
            docx_out.unlink(missing_ok=True)
            doc.docx_path = None  # docx 已删（中间产物），清空悬空引用，防 Word 下载 404
            doc.pdf_path = f"/exports/{pdf_filename}"
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("监理日志省统表 docx/PDF 生成失败，回退自绘日报版式（文书已入库）：%s", exc)
            try:
                from app.services.doc_generator import generate_daily_log_pdf
                pdf_filename = f"doc_{doc.id}_{diary_suffix}_{target_date.isoformat()}.pdf"
                pdf_path = settings.export_dir / pdf_filename
                generate_daily_log_pdf(doc, str(pdf_path), target_date)
                doc.pdf_path = f"/exports/{pdf_filename}"
                await db.commit()
            except Exception as exc2:  # noqa: BLE001
                logger.warning("监理日志 PDF 生成失败（文书已入库）：%s", exc2)

    from app.services.project_archive import sync_document_to_project_archive
    await sync_document_to_project_archive(db, doc, signer)
    await db.commit()

    summary = {
        "work_records": len(work_records),
        "hazards": len(hazards),
        "rectifications": len(rectifications),
        "inspections": len(inspection_records),
        "acceptances": len(acceptance_records),
        "stations": len(station_records),
        "witnesses": len(witness_records),
    }
    # 顶层别名：小程序/H5 等客户端直接读 work_count / hazard_count 展示
    return {
        "doc_id": doc.id,
        "doc_number": content["doc_number"],
        "status": doc.status,
        "pdf_url": doc.pdf_path,
        "markdown_url": doc.markdown_path,
        "skipped": False,
        "work_count": summary["work_records"],
        "hazard_count": summary["hazards"],
        "summary": summary,
    }

def _md_section(text: object, empty_hint: str = "") -> str:
    """把一栏文字转成 Markdown：多行按条目输出，单行原样保留。"""
    raw = str(text or "").strip()
    if not raw:
        return empty_hint or "无。"
    lines = [line.strip(" 　\t") for line in raw.splitlines() if line.strip()]
    if len(lines) <= 1:
        return lines[0] if lines else (empty_hint or "无。")
    return "\n".join(f"- {line}" for line in lines)


def build_supervision_log_markdown(
    content: dict, target_date: date, doc_title: str = "监理日志"
) -> str:
    """把监理日志/监理日记 content 渲染成 Markdown 正文（正式产物）。

    章节与 GD-B-220 / 市政监-24 的八栏口径一致，但用纯文本表达，
    便于资料库归档、全文检索与 AI 二次加工。
    """
    date_text = f"{target_date.isoformat()}（{content.get('weekday') or ''}）".replace("（）", "")
    weather_parts = [
        str(content.get("weather_morning") or "").strip(),
        str(content.get("weather_afternoon") or "").strip(),
        str(content.get("weather_night") or "").strip(),
    ]
    weather = weather_parts[0] if len(set(weather_parts)) == 1 else " / ".join(
        f"{label}{value}" for label, value in zip(("上午", "下午", "夜间"), weather_parts) if value
    )
    low = str(content.get("temperature_low") or "").strip()
    high = str(content.get("temperature_high") or "").strip()
    if low or high:
        weather = f"{weather}（{low or '-'}℃ ~ {high or '-'}℃）" if weather else f"{low or '-'}℃ ~ {high or '-'}℃"
    if content.get("weather_source"):
        weather = f"{weather}（来源：{content['weather_source']}）" if weather else str(content["weather_source"])

    head_rows = [
        ("工程名称", content.get("project_name") or ""),
        ("文书编号", content.get("doc_number") or ""),
        ("日期", date_text),
        ("天气", weather),
    ]
    for label, key in (("建设单位", "owner_unit"), ("施工单位", "construction_unit"), ("监理单位", "supervision_unit")):
        value = str(content.get(key) or "").strip()
        if value:
            head_rows.append((label, value))

    lines = [f"# {doc_title}", ""]
    lines.extend(f"- {label}：{value}" for label, value in head_rows if value)
    lines.extend([
        "",
        "## 一、施工进展情况",
        _md_section(content.get("construction_progress"), "当日无施工进展记录。"),
        "",
        "## 二、材料进场及见证取样",
        _md_section(content.get("material_section"), "当日无材料进场及见证取样。"),
        "",
        "## 三、旁站及巡视",
        _md_section(content.get("supervision_section"), "当日无旁站及巡视记录。"),
        "",
        "## 四、验收",
        _md_section(content.get("acceptance_section"), "当日无验收记录。"),
        "",
        "## 五、平行检查",
        _md_section(content.get("parallel_section"), "当日无平行检查记录。"),
        "",
        "## 六、存在问题及处理情况",
        _md_section(content.get("problems_section"), "当日无安全隐患及质量问题。"),
        "",
        "## 七、其他有关事项",
        "### 会议",
        _md_section(content.get("meeting_section")),
        "",
        "### 重要发文",
        _md_section(content.get("document_section")),
        "",
        "### 闭合记录",
        _md_section(content.get("closure_section")),
        "",
        "### 资料上报",
        _md_section(content.get("report_section")),
        "",
        "### 其他",
        _md_section(content.get("misc_section")),
    ])
    check = str(content.get("supervision_check") or "").strip()
    if check:
        lines.extend(["", "### 上级/质监站检查", _md_section(check)])
    lines.extend(["", "## 八、签署"])
    signer = str(content.get("supervisor") or "").strip()
    level = str(content.get("supervisor_level") or "").strip()
    if doc_title == "监理日记":
        recorder = str(content.get("recorder_name") or signer).strip()
        if recorder:
            lines.append(f"- 记录人（签字）：{recorder}{f'（{level}）' if level else ''}")
        else:
            lines.append("- 记录人（签字）：")
    if signer:
        lines.append(f"- 监理工程师（签字）：{signer}{f'（{level}）' if level else ''}")
    else:
        lines.append("- 监理工程师（签字）：")
    lines.append("")
    return "\n".join(lines)


def _canon_extra_fields(extra: dict | None, form_code: str) -> dict:
    """把 extra_fields 的键归一到 canonical（如 7_12 → supervision_check）。

    🔴 两条入口口径要一致：Agent 工具在 agent_tools 里已归一，但 HTTP
    /api/form-docs/aggregate 的调用方（web-admin）可能直接传表字段 key（7_12/7_13）。
    不归一会静默不生效（用户填了却不进文书），所以这里统一兜一道。
    懒加载 import，避免与 agent_tools 的循环依赖。
    """
    if not isinstance(extra, dict) or not extra:
        return {}
    try:
        from app.services.record_field_spec import FORMS
        fields = FORMS.get(form_code, {}).get("fields") or []
        mapping = {
            str(f.get("key")): str(f.get("canonical"))
            for f in fields
            if f.get("key") and f.get("canonical")
        }
    except Exception:  # noqa: BLE001 - 归一失败不该阻断生成，按原键继续
        mapping = {}
    out: dict = {}
    for k, v in extra.items():
        if not str(v if v is not None else "").strip():
            continue
        canon = mapping.get(str(k), str(k))
        out.setdefault(canon, v)
    return out


async def generate_safety_log(
    db: AsyncSession,
    project_id: int,
    target_date: date,
    *,
    supervisor: Optional[User] = None,
    force: bool = True,
    extra_fields: dict | None = None,
    narrative: str | None = None,
) -> dict:
    """生成指定项目、指定日期的安全监理日志（anquan_jianli_rizhi 省统表）。

    D06：日聚合此前把「安全监理日志」硬编码成 gd_b_220_rizhi（普通监理日志），
    根本没有它自己的聚合入口。这里按安全日志的栏目（施工内容 / 安全检查情况 /
    处理措施 / 施工单位隐患整改情况 / 危险性较大的分部分项工程安全状况 / 其他）
    聚合当日数据，并复用 fill_anquan_rizhi 渲染。
    """
    title = f"安全监理日志_{target_date.isoformat()}"
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    project_name = project.name if project else ""
    # 市政项目渲染 AQSZ-J-2.2（官方版式），其余渲染房建 anquan_jianli_rizhi。
    code = safety_log_form_code(project)
    if not force:
        existing = await _find_existing(
            db, project_id, title=title, doc_type=doc_type_for(code),
        )
        if existing is not None:
            return {
                "doc_id": existing.id,
                "doc_number": (existing.content or {}).get("doc_number", ""),
                "status": existing.status,
                "pdf_url": existing.pdf_path,
                "docx_url": existing.docx_path,
                "form_code": code,
                "form_name": "安全监理日志",
                "skipped": True,
                "summary": {"reason": "当日安全日志已存在"},
            }

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

    records_result = await db.execute(
        select(WorkRecord).where(
            and_(
                WorkRecord.project_id == project_id,
                or_(WorkRecord.record_date == target_date,
                    and_(WorkRecord.record_date.is_(None), WorkRecord.created_at >= day_start, WorkRecord.created_at < day_end)),
            )
        ).order_by(WorkRecord.created_at)
    )
    work_records = records_result.scalars().all()

    hazards_result = await db.execute(
        select(Hazard).where(
            and_(Hazard.project_id == project_id, Hazard.created_at >= day_start, Hazard.created_at < day_end)
        )
    )
    hazards = hazards_result.scalars().all()

    rectifications_result = await db.execute(
        select(RectificationRecord).join(Hazard, Hazard.id == RectificationRecord.hazard_id).where(
            Hazard.project_id == project_id,
            or_(and_(RectificationRecord.created_at >= day_start, RectificationRecord.created_at < day_end),
                and_(RectificationRecord.rectified_at >= day_start, RectificationRecord.rectified_at < day_end),
                and_(RectificationRecord.recheck_at >= day_start, RectificationRecord.recheck_at < day_end)),
        )
    )
    rectifications = rectifications_result.scalars().all()

    weekday = WEEKDAY_CN[target_date.weekday()]

    signer = supervisor if supervisor is not None else await pick_creator(db, project_id)
    if signer is None:
        raise ValueError(f"项目 {project_id} 下没有成员，无法确定安全监理日志的创建人")
    supervisor_name = signer.real_name or ""

    # ===== 施工内容 =====
    content_items = [
        f"{r.section or ''} {r.location or ''}：{r.work_content}".strip()
        for r in work_records if r.work_content
    ]
    work_content = "\n".join(content_items) if content_items else "当日无施工内容记录。"

    # ===== 安全检查情况（巡视/旁站 + 隐患）=====
    safety_items = []
    for r in work_records:
        if r.record_type in (WorkRecordType.INSPECTION.value, WorkRecordType.STATION.value):
            place = f"{r.section or ''} {r.location or ''}".strip()
            safety_items.append(f"{place}：{r.inspection_remark or r.station_content or r.work_content or ''}")
    for h in hazards:
        loc = f"{h.section or ''} {h.location or ''}".strip()
        safety_items.append(f"{loc}：发现{h.top1_risk or '安全隐患'}（{h.risk_category or '安全'}类）")
    # 🔴 叙述性安全监理日志：用户粘贴/口述的安全监理文字优先，且**只用叙述**——
    # 与监理日志同一策略，不再追加「（系统记录补充：…）」（2026-11-03 用户要求去掉）。
    if narrative:
        safety_check = str(narrative).strip()
    else:
        safety_check = "\n".join(safety_items) if safety_items else "当日无安全检查记录。"

    # ===== 处理措施（限期整改/暂时停工/上报建设单位）=====
    # 🔴 treatment/problems 是 WorkRecord 的字段，Hazard 上没有（Hazard 只有 status/
    # risk_status/recheck_suggestion）—— 写 h.treatment 会 AttributeError。
    treatment_items = []
    for r in work_records:
        if r.treatment or r.problems:
            place = f"{r.section or ''} {r.location or ''}".strip()
            treatment_items.append(f"{place}：{problem_text(r)}".replace("：：", "："))
    for h in hazards:
        loc = f"{h.section or ''} {h.location or ''}".strip()
        desc = h.top1_risk or "安全隐患"
        suggest = ""
        for item in (h.recheck_suggestion or []):
            if isinstance(item, dict):
                suggest = str(item.get("content") or item.get("text") or "")
            elif item:
                suggest = str(item)
            if suggest:
                break
        treatment_items.append(
            f"{loc}：{desc}，当前状态 {h.status or HazardStatus.REPORTED.value}"
            + (f"；整改要求：{suggest}" if suggest else "")
        )
    for rc in rectifications:
        hazard = await db.get(Hazard, rc.hazard_id)
        parts = []
        if rc.deadline:
            parts.append(f"限期整改（{rc.deadline.date().isoformat()} 前）")
        if rc.recheck_remark:
            parts.append(f"复查意见：{rc.recheck_remark}")
        if parts:
            treatment_items.append(
                f"{getattr(hazard, 'top1_risk', '') or '隐患'}：" + "；".join(parts)
            )
    treatment = "\n".join(treatment_items) if treatment_items else "当日无需处理的安全问题。"

    # ===== 施工单位隐患整改情况 =====
    rectify_items = []
    for rc in rectifications:
        hazard = await db.get(Hazard, rc.hazard_id)
        if rc.recheck_passed is True:
            status = "复查合格"
        elif rc.recheck_passed is False:
            status = "复查不合格"
        elif rc.rectified_at:
            status = "已整改待复查"
        else:
            status = "整改中"
        when = rc.rectified_at.date().isoformat() if rc.rectified_at else "未回填"
        rectify_items.append(
            f"{getattr(hazard, 'top1_risk', '') or '隐患'}（{getattr(hazard, 'location', '') or ''}）："
            f"{status}，整改时间 {when}"
        )
    for h in hazards:
        if any(rc.hazard_id == h.id for rc in rectifications):
            continue
        rectify_items.append(
            f"{h.top1_risk or '安全隐患'}（{h.location or ''}）："
            f"{h.status or HazardStatus.REPORTED.value}，尚未整改"
        )
    rectify_status = "\n".join(rectify_items) if rectify_items else "当日无隐患整改事项。"

    # ===== 危险性较大的分部分项工程安全状况 =====
    weida = [h for h in hazards
             if "危大" in (h.risk_category or "") or "危大" in (h.top1_risk or "")]
    hazard_status = (
        "\n".join(f"{h.location or ''}：{h.top1_risk or ''}" for h in weida)
        if weida else "当日无危大工程相关安全状况。"
    )

    # ===== 其他 =====
    other_items = [
        f"{r.section or ''} {r.location or ''}：{r.work_content or r.remark or ''}"
        for r in work_records
        if r.record_type in ("other", "待定") and (r.work_content or r.remark)
    ]
    other = "\n".join(other_items) if other_items else "暂无。"

    from app.services.weather import daily_weather
    ex = await daily_weather(project, target_date, None)
    weather = ex.get("weather", "")

    content = {
        "project_name": project_name,
        "doc_number": f"GD-AQ-{project_id}-{target_date.strftime('%Y%m%d')}",
        "date": target_date.isoformat(),
        "weekday": weekday,
        "weather": weather,
        "weather_am": weather,
        "weather_pm": weather,
        "weather_night": weather,
        "content": work_content,
        "safety_check": safety_check,
        "treatment": treatment,
        "rectify_status": rectify_status,
        "hazard_status": hazard_status,
        "other": other,
        "supervisor": supervisor_name,
        "safety_supervisor": supervisor_name,
    }

    # extra_fields 透传（安全日志也可用归一后的 canonical 键覆盖，如 weather）
    extra_fields = _canon_extra_fields(extra_fields, code)
    if extra_fields:
        w = extra_fields.get("weather")
        if w:
            content["weather"] = w
            content["weather_am"] = w
            content["weather_pm"] = w
            content["weather_night"] = w

    doc = Document(
        doc_type=doc_type_for(code),
        title=title,
        content=content,
        form_code=code,
        project_id=project_id,
        creator_id=signer.id,
        status=DocStatus.DRAFT.value,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    try:
        docx_out = await asyncio.to_thread(render_form, code, content)
        doc.docx_path = f"/exports/{docx_out.name}"
        await db.commit()
        try:
            from app.services.docx2pdf import convert_docx_to_pdf
            pdf_filename = f"doc_{doc.id}_安全监理日志_{target_date.isoformat()}.pdf"
            pdf_path = settings.export_dir / pdf_filename
            await asyncio.to_thread(convert_docx_to_pdf, str(docx_out), str(pdf_path))
            docx_out.unlink(missing_ok=True)
            doc.pdf_path = f"/exports/{pdf_filename}"
            await db.commit()
        except Exception as exc2:  # noqa: BLE001
            logger.warning("安全监理日志 PDF 生成失败（文书已入库）：%s", exc2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("安全监理日志 docx 生成失败（文书已入库）：%s", exc)

    from app.services.project_archive import sync_document_to_project_archive
    await sync_document_to_project_archive(db, doc, signer)
    await db.commit()

    summary = {
        "work_records": len(work_records),
        "hazards": len(hazards),
        "rectifications": len(rectifications),
    }
    return {
        "doc_id": doc.id,
        "doc_number": content["doc_number"],
        "status": doc.status,
        "pdf_url": doc.pdf_path,
        "docx_url": doc.docx_path,
        "form_code": code,
        "form_name": "安全监理日志",
        "skipped": False,
        "summary": summary,
    }


async def generate_aggregate_doc(
    db: AsyncSession,
    project_id: int,
    period: str,
    ref: str | None = None,
    *,
    form_code: str | None = None,
    use_llm: bool = True,
    supervisor: Optional[User] = None,
    force: bool = True,
    extra_fields: dict | None = None,
    narrative: str | None = None,
) -> dict:
    """按 日/周/月 聚合生成省统表文书（监理日志/周报/月报 docx）。

    与 ``generate_supervision_log`` 同理：``force=False`` 时同周期已有文书则跳过。
    """
    start, end, period_label = parse_period_range(period, ref)
    project = await db.get(Project, project_id)
    municipal = is_municipal(project)
    # 市政项目：显式房建 code 翻译成对应市政表式；未显式指定时按工程类型路由。
    if period == "day":
        if form_code == "gd_b_220_rizhi" and municipal:
            form_code = daily_log_form_code(project)
        if form_code == "anquan_jianli_rizhi" and municipal:
            form_code = safety_log_form_code(project)
        if not form_code:
            # 手动接口不传 form_code（默认监理日志）→ 市政项目出市政监-24
            form_code = daily_log_form_code(project) if municipal else PERIOD_FORM_CODES["day"]
    code = form_code or PERIOD_FORM_CODES[period]
    spec = FORM_BY_CODE.get(code)
    if spec is None:
        raise ValueError(f"未知表式 {code}")
    if spec.source != "aggregate":
        raise ValueError(f"{spec.name} 不是聚合生成类文书")

    if period == "day":
        if code in ("anquan_jianli_rizhi", "aqsz_2_2_anquan_rizhi"):
            return await generate_safety_log(
                db, project_id, start, supervisor=supervisor, force=force,
                extra_fields=extra_fields, narrative=narrative,
            )
        daily = await generate_supervision_log(
            db, project_id, start, supervisor=supervisor, force=force, extra_fields=extra_fields,
            narrative=narrative,
        )
        daily_doc = await db.get(Document, daily["doc_id"])
        # 监理日志保持省统表 docx/PDF 产物（generate_supervision_log 已渲染并归档），
        # 这里不再额外挂 docx 附件（历史二次渲染已并入主流程）。
        return {
            **daily,
            "docx_url": daily_doc.docx_path if daily_doc else None,
            "form_code": daily_doc.form_code if daily_doc else None,
            "form_name": "监理日志",
        }

    title = spec.default_title.format(date=start.isoformat(), period=period_label) or (
        f"{spec.name}_{period_label}"
    )
    if not force:
        existing = await _find_existing(
            db, project_id, title=title, form_code=spec.form_code
        )
        if existing is not None:
            return {
                "doc_id": existing.id,
                "title": existing.title,
                "form_code": spec.form_code,
                "form_name": spec.name,
                "doc_number": (existing.content or {}).get("doc_number"),
                "docx_url": existing.docx_path,
                "period": period_label,
                "skipped": True,
                "summary": {},
            }

    agg = await aggregate_records(db, project_id, start, end)
    signer = supervisor if supervisor is not None else await pick_creator(db, project_id)
    if signer is None:
        raise ValueError(f"项目 {project_id} 下没有成员，无法确定文书的创建人")
    supervisor_name = signer.real_name or ""
    supervisor_level = signer.supervisor_level or ""

    # 🔴 平行检验汇总直接复用 aggregate_records 的结果（同一区间、同一次查询）。
    # 这里曾重复查一遍 ParallelInspection 并按区间格式化，与 aggregate_records
    # 内的实现完全同义 —— 两份格式化逻辑必然漂移，且多打一次数据库。
    # 保留 aggregate_records 版本：它的 order_by(inspect_date, created_at)
    # 对「同一天多条平行检验」排序稳定。
    parallel_section = agg["parallel_section"]

    content = {
        "project_name": project.name if project else "",
        "doc_number": "",
        "date": start.isoformat(),
        "period": period_label,
        "weekday": "一二三四五六日"[start.weekday()],
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        **project_fields(project),
        # 周报/月报跨多日，天气无单一取值 —— 与下方 temperature/weather_* 字段
        # 一致留空（此前误引用日报函数的 ex 变量，必然 NameError）。
        "weather_source": "",
        "weather_missing": [],
        "parallel_section": parallel_section,
        "supervisor": supervisor_name,
        "supervisor_level": supervisor_level,
        "construction_progress": agg["construction_progress"],
        "material_section": agg["material_section"],
        "supervision_section": agg["supervision_section"],
        "acceptance_section": agg["acceptance_section"],
        "other_work_section": agg["other_work_section"],
        "problems_section": agg["problems_section"],
        "other_section": agg["problems_section"],
        "temperature_high": "",
        "temperature_low": "",
        "weather_morning": "",
        "weather_afternoon": "",
        "weather_night": "",
        "_agg": agg,
    }
    if use_llm:
        content = await llm_polish(content, period_label, spec.name)

    doc = Document(
        doc_type=doc_type_for(spec.form_code),
        title=title,
        content={k: v for k, v in content.items() if not k.startswith("_")},
        form_code=spec.form_code,
        project_id=project_id,
        creator_id=signer.id,
        status=DocStatus.DRAFT.value,
    )
    db.add(doc)
    await db.flush()

    # 渲染（先补编号）
    doc.content = {**doc.content, "doc_number": f"{spec.form_number}-{doc.id:04d}"}
    try:
        docx_file = await asyncio.to_thread(render_form, spec.form_code, doc.content)
    except Exception as exc:
        await db.rollback()
        logger.exception("聚合文书渲染失败")
        raise RuntimeError(f"文书渲染失败：{exc}") from exc
    doc.docx_path = f"/exports/{docx_file.name}"
    doc.pdf_path = None
    try:
        await db.commit()
    except Exception:
        # commit 失败：回滚并清理已渲染的 docx，防孤儿文件
        await db.rollback()
        docx_file.unlink(missing_ok=True)
        raise

    from app.services.project_archive import sync_document_to_project_archive
    await sync_document_to_project_archive(db, doc, signer)
    await db.commit()

    return {
        "doc_id": doc.id,
        "title": doc.title,
        "form_code": spec.form_code,
        "form_name": spec.name,
        "doc_number": doc.content.get("doc_number"),
        "docx_url": doc.docx_path,
        "period": period_label,
        "skipped": False,
        "summary": {k: v for k, v in agg.items() if isinstance(v, int)},
    }


async def generate_daily_batch(
    target_date: date | None = None, *, force: bool = False,
    projects: list[tuple[int, str]] | None = None,
) -> dict:
    """遍历项目生成指定日期的监理日志（该日期若为一周/一月之末，顺带出周报/月报）。

    定时任务的入口，也可用于人工补跑。单个项目失败不影响其它项目。

    ``target_date`` 缺省取「运行日 − ``settings.daily_doc_offset_days``」：守护在
    早上 8 点触发，此时要写的是**昨天**整天的日志，而不是刚开始的今天。

    ``projects`` 传入 ``(id, name)`` 列表时只跑这些项目（定时守护按各项目设定的
    时点筛选）；缺省跑全部项目。
    """
    day = target_date or (
        now_cn().date() - timedelta(days=settings.daily_doc_offset_days)
    )
    report: dict = {
        "date": day.isoformat(),
        "projects": 0,
        "created": [],
        "skipped": [],
        "no_member": [],
        "errors": [],
        "weekly": [],
        "monthly": [],
    }
    async with async_session() as db:
        if projects is None:
            result = await db.execute(select(Project).order_by(Project.id))
            projects = [(row.id, row.name) for row in result.scalars().all()]
        report["projects"] = len(projects)

        for pid, pname in projects:
            creator = await pick_creator(db, pid)
            if creator is None:
                # 无成员项目（多为自动化测试遗留）不生成：既无人落款，也无人
                # 在客户端复核签字，每天出一份空白日志只会污染文书台账。
                report["no_member"].append(f"{pid}:{pname}")
                continue

            try:
                res = await generate_supervision_log(
                    db, pid, day, supervisor=creator, force=force
                )
                key = f"{pid}:{pname}"
                if res.get("skipped"):
                    report["skipped"].append(key)
                else:
                    report["created"].append(f"{key}#{res.get('doc_id')}")
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.exception("项目 %s 监理日志生成失败", pid)
                report["errors"].append(f"日志[{pid}] {str(exc)[:200]}")
                continue

            # 目标日若是一周的最后一天（周日），顺带把这一周的周报出了。
            # 判定挂在"目标日"而非"运行日"上，这样早上补跑昨天的日志时，
            # 周一 08:00（目标日=周日）自然就把上周的周报一并结了。
            if day.weekday() == 6:
                try:
                    res = await generate_aggregate_doc(
                        db,
                        pid,
                        "week",
                        day.isoformat(),
                        use_llm=settings.daily_doc_use_llm,
                        supervisor=creator,
                        force=force,
                    )
                    report["weekly"].append(
                        f"{pid}#{res.get('doc_id')}"
                        f"{'（已存在）' if res.get('skipped') else ''}"
                    )
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    logger.exception("项目 %s 周报生成失败", pid)
                    report["errors"].append(f"周报[{pid}] {str(exc)[:200]}")

            # 目标日若是当月最后一天，顺带把当月月报出了（同理挂在"目标日"上：
            # 11 月 1 日 08:00 补跑 10-31 的日志时，10 月月报同时结掉）
            if (day + timedelta(days=1)).day == 1:
                try:
                    res = await generate_aggregate_doc(
                        db,
                        pid,
                        "month",
                        day.strftime("%Y-%m"),
                        use_llm=settings.daily_doc_use_llm,
                        supervisor=creator,
                        force=force,
                    )
                    report["monthly"].append(
                        f"{pid}#{res.get('doc_id')}"
                        f"{'（已存在）' if res.get('skipped') else ''}"
                    )
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    logger.exception("项目 %s 月报生成失败", pid)
                    report["errors"].append(f"月报[{pid}] {str(exc)[:200]}")

    return report


# 周期文书守护的失败退避：同日第 N 次失败 → 300 × 2^(N-1) 秒，封顶 1 小时。
# 🔴 上限的存在是为了终结「每分钟重试到天亮」的风暴（生产实证约 480 次刷屏）。
_BACKOFF_BASE = 300
_BACKOFF_CAP = 3600


def _parse_hhmm(value: str | None) -> tuple[int, int] | None:
    """``"HH:MM"`` → ``(时, 分)``；空/非法返回 None（由调用方回退全局默认）。"""
    if not value:
        return None
    try:
        hour, minute = str(value).strip().split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
    except (ValueError, AttributeError):
        return None
    if 0 <= hour_i < 24 and 0 <= minute_i < 60:
        return hour_i, minute_i
    return None


def project_daily_time(project) -> tuple[int, int]:
    """项目设定的日志生成时点；未设或非法时回退全局默认（DAILY_DOC_HOUR/MINUTE）。"""
    parsed = _parse_hhmm(getattr(project, "log_daily_time", None))
    if parsed is None:
        return settings.daily_doc_hour, settings.daily_doc_minute
    return parsed


def _due_for_project(now: datetime, log_daily_time: str | None) -> bool:
    """守护判定：当前时间是否已到该项目的日志生成时点。"""
    hour, minute = _parse_hhmm(log_daily_time) or (
        settings.daily_doc_hour, settings.daily_doc_minute,
    )
    return (now.hour, now.minute) >= (hour, minute)


def _next_backoff(today_key: str, prev_date: str | None,
                  prev_count: int) -> tuple[str, int, float]:
    """返回 (统计日期, 当日累计失败次数, 本次应 sleep 的秒数)。

    跨天则重置计数（第二天是新的目标日，重新给机会）；
    同日连续失败则指数退避到 _BACKOFF_CAP 封顶。
    """
    count = 1 if prev_date != today_key else prev_count + 1
    sleep_for = min(_BACKOFF_BASE * (2 ** (count - 1)), _BACKOFF_CAP)
    return today_key, count, float(sleep_for)


async def daily_report_worker() -> None:
    """后台守护：每天设定时间点自动生成**前一天**的监理日志（及周报/月报）。

    设计取舍：
    - **到点触发 + 内存标记**：每分钟醒来一次判时间，触发过就置 ``last_run``，
      当天不再重复。进程重启会重新判定一次，但生成端 ``force=False`` 幂等，
      最坏只是多几次查询。
    - **早上出昨天的稿**：默认 08:00 触发、目标日 = 运行日 − 1。放在次日早上是
      因为下班前才补录的记录要被收进去；若改成当天晚间触发，当日录入的内容
      会赶不上出稿。
    - **失败退避不置标记**：只要 report["errors"] 非空或整批抛异常，就按
      ``_next_backoff`` 指数退避重试（300s 起、封顶 1h），跨天重置；
      既避免"数据库抖动就整天不补"，又不会退化成每分钟重试风暴。
    - **不覆盖人工成果**：``force=False`` 只在"当天还没有这份文书"时新建，
      客户端复核/编辑过的草稿不会被定时任务冲掉。
    """
    # 启动即打一条：否则守护在"未到点"时完全静默，运维无法区分
    # 「没启动」和「已启动但还没到时间」。
    logger.info(
        "[周期文书] 守护启动：每日 %02d:%02d（东八区，各项目可在设置中改时间点）"
        "自动生成前 %s 天的监理日志，目标日为周日时补该周周报、为月末时补该月月报"
        "（DAILY_DOC_AUTO=%s）",
        settings.daily_doc_hour,
        settings.daily_doc_minute,
        settings.daily_doc_offset_days,
        settings.daily_doc_auto,
    )
    await asyncio.sleep(settings.daily_doc_initial_delay)
    last_run: str | None = None
    # 🔴 失败退避状态：同一天连续失败时递增退避并设上限。
    # 原实现里外层 except **没有设 sleep_for**，所以异常路径永远走默认 60 秒，
    # 且 last_run 从不置位 → 生产实证从 23:48 每分钟刷一条到次日 08:00
    # （约 480 次「周报生成失败」+ 480 条 MissingGreenlet 刷屏）。
    fail_date: str | None = None
    fail_count = 0
    while True:
        sleep_for = 60
        try:
            now = now_cn()
            today_key = now.isoformat()[:10]
            if settings.daily_doc_auto and last_run != today_key:
                async with async_session() as db:
                    projects = (await db.execute(select(Project).order_by(Project.id))).scalars().all()
                    due_projects = [
                        (p.id, p.name) for p in projects
                        if _due_for_project(now, getattr(p, "log_daily_time", None))
                    ]
                if due_projects:
                    report = await generate_daily_batch(projects=due_projects)
                    if report["errors"]:
                        fail_date, fail_count, sleep_for = _next_backoff(
                            today_key, fail_date, fail_count
                        )
                        logger.warning(
                            "[周期文书] %s 自动生成存在失败（当日第 %s 次），%s 秒后重试：%s",
                            report["date"], fail_count, int(sleep_for),
                            report["errors"][:5],
                        )
                    else:
                        last_run = today_key
                        fail_date, fail_count = None, 0
                        logger.info(
                            "[周期文书] %s 自动生成完成：新建 %s 份 / 已存在 %s 份 / "
                            "无成员跳过 %s 个项目 / 周报 %s / 月报 %s",
                            report["date"],
                            len(report["created"]),
                            len(report["skipped"]),
                            len(report["no_member"]),
                            report["weekly"],
                            report["monthly"],
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # 🔴 异常路径同样要退避：否则整批崩溃会退化成每分钟重试风暴。
            day_key = now_cn().isoformat()[:10]
            fail_date, fail_count, sleep_for = _next_backoff(
                day_key, fail_date, fail_count
            )
            logger.warning(
                "[周期文书] 自动生成异常（当日第 %s 次，%s 秒后重试）：%s",
                fail_count, int(sleep_for), exc,
            )
        await asyncio.sleep(sleep_for)
