"""服务侧质量守护：60s 任务清扫 + 每日 18:00 待补汇总 + 每周端到端冒烟。

参考同机 jl-service 的「服务侧定时任务」思路，但按监理通的技术栈做最小落地：

* 不引入 APScheduler/Celery/Redis；复用 ``daily_report_worker`` 那种
  「每分钟醒来判时间 + 内存去重标记」的守护，挂进 FastAPI lifespan。
* 汇总与冒烟都**只读**，结果登记成 ``agent_async_tasks``（kind=missing_summary /
  smoke_check），与第 3 项共用一张表，不新增表。
* 冒烟 13 项全是纯函数断言，不写数据库、不调模型、不发通知，主要价值是
  抓「补的字段被静默丢掉 / 占位词规则被改坏」这类退化。

开关：``QUALITY_GUARD_ENABLED``（默认 1）、``QUALITY_GUARD_HOUR``（默认 18）、
``SMOKE_CHECK_HOUR``（默认 9，周一跑）、``SMOKE_CHECK_WEEKDAY``（默认 0=周一）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select

from app.db import async_session
from app.models.work_record import WorkRecord
from app.services import agent_task_registry as registry
from app.services import doc_quality
from app.services import record_field_spec
from app.services.period_report_service import now_cn

logger = logging.getLogger(__name__)


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _record_values(row: WorkRecord) -> dict[str, Any]:
    """把 ORM 列映射成 doc_quality 的 values（含 content_json 里的完整表单值）。

    字段契约里不少 ask 字段 arg 为 None，只能靠 canonical/key 命中；这里多放几个
    常见别名，口径偏保守（宁可多报缺项，不漏报），汇总只用于提示，不拦签发。
    """
    extra = dict(row.content_json) if isinstance(row.content_json, dict) else {}
    values: dict[str, Any] = dict(extra)
    values.update({
        "record_type": row.record_type,
        "section": row.section or row.station_location,
        "station_part": row.station_location or row.section,
        "location": row.location,
        "content": row.work_content or row.station_content,
        "work_content": row.work_content or row.station_content,
        "description": row.station_content or row.work_content,
        "problems": row.problems,
        "treatment": row.treatment,
        "weather": row.weather,
        "worker_count": row.worker_count,
        "station_process": row.station_process,
        "station_start_time": row.station_start_time,
        "station_end_time": row.station_end_time,
        "station_conclusion": row.station_conclusion,
        "acceptance_result": row.acceptance_result,
        "inspection_start_time": row.inspection_start_time,
        "start_time": row.inspection_start_time or row.station_start_time,
    })
    return values


async def scan_missing_summary() -> dict[str, Any]:
    """汇总当天记录里仍待补的字段（只读，复用 doc_quality 的占位词规则）。"""
    today = now_cn().date()
    day_start = datetime.combine(today, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    report: dict[str, Any] = {
        "ok": True, "date": today.isoformat(), "scanned": 0,
        "with_missing": 0, "placeholder_hits": 0,
        "by_type": {}, "top_fields": [], "sample": [],
    }
    try:
        async with async_session() as db:
            rows = (await db.scalars(
                select(WorkRecord)
                .where(or_(
                    WorkRecord.record_date == today,
                    and_(WorkRecord.created_at >= day_start, WorkRecord.created_at < day_end),
                ))
                .order_by(WorkRecord.id.desc())
                .limit(500)
            )).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("缺项汇总查询失败: %s", type(exc).__name__)
        return {"ok": False, "error": type(exc).__name__, "date": today.isoformat()}

    field_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    for row in rows:
        report["scanned"] += 1
        missing = doc_quality.missing_fields(str(row.record_type or ""), _record_values(row))
        if not missing:
            continue
        report["with_missing"] += 1
        report["placeholder_hits"] += sum(
            1 for x in missing if x.get("reason") == "placeholder"
        )
        type_counter[str(row.record_type or "未分类")] += 1
        field_counter.update(str(x.get("label") or "") for x in missing)
        if len(report["sample"]) < 5:
            report["sample"].append({
                "record_id": int(row.id),
                "record_type": str(row.record_type or ""),
                "labels": [str(x.get("label") or "") for x in missing[:4]],
            })
    report["by_type"] = dict(type_counter.most_common())
    report["top_fields"] = [
        {"label": label, "count": count} for label, count in field_counter.most_common(8)
    ]
    return report


async def run_smoke_checks() -> dict[str, Any]:
    """13 项端到端冒烟（纯函数，不写库、不调模型）。"""
    checks: list[dict[str, Any]] = []
    uncovered: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:120]})

    try:
        check("占位词集合完整", {"待补", "待定", "未知"}.issubset(doc_quality.PLACEHOLDERS))
        check("空值算缺项", doc_quality._is_blank(None)[0])
        blank, reason = doc_quality._is_blank("待补")
        check("占位词算缺项", blank and reason == "placeholder", reason)
        check("无问题不算缺项", not doc_quality._is_blank("无问题")[0])
        check("括号占位词算缺项", doc_quality._is_blank("(暂无)")[0])
        items = doc_quality.missing_fields("巡视", {})
        check("巡视缺项可算出", len(items) > 0, f"{len(items)} 项")
        generic = doc_quality.missing_fields("待定", {})
        check("待定草稿不追问记录类型",
              all("记录类型" not in str(x.get("label") or "") for x in generic))
        check("无缺项不出提示", doc_quality.missing_summary([]) == "")
        summary = doc_quality.missing_summary([
            {"label": "部位"}, {"label": "时间"},
        ])
        check("缺项提示不拦保存", "还差 2 项" in summary and "不影响保存" in summary, summary)
        parsed = doc_quality._parse_review(
            '{"items":[{"level":"must_fix","text":"依据为空"},'
            '{"level":"suggest","text":"表述重复"}]}'
        )
        check("复核意见可解析", len(parsed) == 2 and parsed[0]["level"] == "must_fix")
        check("复核垃圾输出降级", doc_quality._parse_review("不是 JSON") == [])
        check("无实质内容跳过复核", doc_quality._facts_text({"record_type": "巡视"}, None) == "")
        core = ("巡视", "旁站", "验收", "见证")
        forms_ok = bool(record_field_spec.FORMS) and all(
            record_field_spec.RECORD_TYPE_TO_FORM.get(k) in record_field_spec.FORMS
            for k in core
        )
        uncovered = [
            k for k, code in record_field_spec.RECORD_TYPE_TO_FORM.items()
            if code not in record_field_spec.FORMS
        ]
        state_ok = (
            registry.can_transition("queued", "running")
            and registry.can_transition("failed", "queued")
            and not registry.can_transition("done", "running")
        )
        check("字段契约与任务状态机", forms_ok and state_ok,
              f"forms={len(record_field_spec.FORMS)} state={state_ok} 未覆盖={uncovered}")
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "冒烟执行异常", "ok": False, "detail": type(exc).__name__})

    passed = sum(1 for c in checks if c["ok"])
    warnings = [f"记录类型「{k}」无对应 FORMS 契约，缺项校验走通用兜底" for k in uncovered]
    return {
        "ok": passed == len(checks) and len(checks) == 13,
        "total": len(checks), "passed": passed,
        "checks": checks, "warnings": warnings,
        "ran_at": now_cn().isoformat(timespec="seconds"),
    }


async def _record_guard_result(kind: str, report: dict[str, Any]) -> None:
    key = await registry.create_task(
        user_id=0, kind=kind, request={"trigger": "quality_guard_worker"},
    )
    if key:
        await registry.finish_task(key, report)


async def quality_guard_worker() -> None:
    """单守护覆盖三件事：60s 超时清扫 / 每日待补汇总 / 每周冒烟。"""
    if not _flag("QUALITY_GUARD_ENABLED"):
        logger.info("[质量守护] 已关闭（QUALITY_GUARD_ENABLED=0）")
        return
    summary_hour = _int_env("QUALITY_GUARD_HOUR", 18)
    smoke_hour = _int_env("SMOKE_CHECK_HOUR", 9)
    smoke_weekday = _int_env("SMOKE_CHECK_WEEKDAY", 0)
    logger.info(
        "[质量守护] 启动：每 60s 清扫超时任务；每日 %02d:00 待补汇总；"
        "每周 %s %02d:00 冒烟 13 项",
        summary_hour, smoke_weekday, smoke_hour,
    )
    last_summary: str | None = None
    last_smoke: str | None = None
    while True:
        try:
            stale = await registry.sweep_stale()
            if stale:
                logger.warning("[质量守护] 判超时任务 %s 条：%s", len(stale), stale[:5])
            now = now_cn()
            day = now.isoformat()[:10]
            week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
            if now.hour >= summary_hour and last_summary != day:
                report = await scan_missing_summary()
                await _record_guard_result("missing_summary", report)
                last_summary = day
                logger.info(
                    "[质量守护] 待补汇总 %s：扫描 %s 条 / 有缺项 %s 条 / 占位词 %s 次",
                    day, report.get("scanned"), report.get("with_missing"),
                    report.get("placeholder_hits"),
                )
            if (now.weekday() == smoke_weekday and now.hour >= smoke_hour
                    and last_smoke != week):
                report = await run_smoke_checks()
                await _record_guard_result("smoke_check", report)
                last_smoke = week
                failed = [c["name"] for c in report["checks"] if not c["ok"]]
                if failed:
                    logger.error("[质量守护] 冒烟未通过 %s/%s：%s",
                                 report["passed"], report["total"], failed)
                else:
                    logger.info("[质量守护] 冒烟 %s/%s 全部通过",
                                report["passed"], report["total"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[质量守护] 本轮异常（不影响主流程）: %s", type(exc).__name__)
        await asyncio.sleep(60)
