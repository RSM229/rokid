"""穗建通数据采集 SUP-002/003/005/006/007/012/021 映射器（批量）。

来源：文档/穗建通SDK文档包/supervision_data_api.md
    SUP-002 旁站     POST /tenantgz/e-api/ods/push/station_record
    SUP-003 平行检验 POST /tenantgz/e-api/ods/push/parallel_inspect
    SUP-005 验收     POST /tenantgz/e-api/ods/push/acceptance_record
    SUP-006 日志     POST /tenantgz/e-api/ods/push/daily_log
    SUP-007 月报     POST /tenantgz/e-api/ods/push/monthly_report
    SUP-012 材料进场 POST /tenantgz/e-api/ods/push/material_check
    SUP-021 见证取样 POST /tenantgz/e-api/ods/push/witness_sampling

职责边界（与 SUP-001 一致，刻意收窄）：
- 纯函数：监理通记录 dict → 穗建通请求体；不碰 DB、不发 HTTP。
- 必填缺失抛 MissingRequiredError，绝不静默填占位值污染数据中台。
- 可选字段监理通有对应来源才带，空串/None 一律不带。
- 时间/状态/是否类字段沿用 szcj_sup001_mapper 的归一函数，两处口径不漂移。

字段来源（监理通 WorkRecord 公共列 + content_json 扩展列）：
- 旁站：station_* 列；平行检验：acceptance_basis/work_content 等；验收：acceptance_*
- 日志/月报：generated_doc 关联表单值（doc_number 等）在 content_json 兜底取
- 材料：gd_b_219_cailiao 字段族；见证：witness_* 列
取值顺序统一为「专用列 → content_json → None」，调用方传入的已规范化 dict。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from app.services.szcj_sup001_mapper import (
    MissingRequiredError,
    _DEFAULT_STATUS,
    _STATUS_MAP,
    _fmt_date,
    _fmt_datetime,
    _fmt_time,
)

# 通用必填：所有接口都要求的骨架（各 map_* 再补各自的业务必填）
_COMMON_REQUIRED = ("id", "projectCode", "status", "createdTime", "updatedTime")

# 记录人姓名：002/003/012/021 必填（005/006/007 无 userName 必填项）
_NAME_REQUIRED = ("002", "003", "012", "021")


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _opt_str(value: Any) -> str | None:
    """可选字符串：有值才带。空串/None → 不带（避免用 "" 冒充「无」）。"""
    text = str(value or "").strip()
    return text or None


def _opt_num(value: Any) -> int | float | None:
    """可选数值：能转数值才带，转不了不带（原文照抄必炸类型校验）。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value).strip()
        return int(text) if text.isdigit() else float(text)
    except ValueError:
        return None


def _photos(urls: list[str] | None, public_file_url: Callable[[str], str] | None) -> list[str]:
    """照片列表：允许钩子把本地路径转公网 URL；过滤空值与非字符串。"""
    result = [u for u in (urls or []) if isinstance(u, str) and u.strip()]
    if public_file_url is not None:
        result = [public_file_url(u) for u in result]
    return [u for u in result if isinstance(u, str) and u.strip()]


def _emit(payload: dict[str, Any]) -> dict[str, Any]:
    """去掉值缺失的键：None/空串/空列表都不带（必填项已在各自 map_* 里保证）。"""
    return {k: v for k, v in payload.items() if v not in (None, "", [])}


def _check_required(payload: dict[str, Any], fields: tuple[str, ...], code: str) -> None:
    missing = [f for f in fields if payload.get(f) in (None, "", [])]
    if missing:
        raise MissingRequiredError(missing, code)


def _status_of(record: dict[str, Any]) -> int:
    return _STATUS_MAP.get(str(record.get("status") or ""), _DEFAULT_STATUS)


def _cj(record: dict[str, Any], canonical: str) -> Any:
    """content_json 扩展字段取值（dict 或已摊平的 key 都兼容）。"""
    cj = record.get("content_json") or {}
    if isinstance(cj, dict):
        return cj.get(canonical)
    return None


# ---------------------------------------------------------------------------
# SUP-002 旁站记录
# ---------------------------------------------------------------------------

def map_sup002(
    record: dict[str, Any],
    *,
    project_code: str,
    user_name: str,
    photo_urls: list[str],
    created_time: Any,
    updated_time: Any,
    user_id: str | None = None,
    public_file_url: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """旁站记录（SUPERVISOR_STATION）。

    record 键名沿用 WorkRecord：id, station_process, station_location, section,
    construction_unit(经 content_json), inspection_start_time/station_start_time,
    work_content, problems, treatment, weather, status, content_json。
    """
    rid = record.get("id")
    start = record.get("station_start_time") or record.get("inspection_start_time")
    end = record.get("station_end_time") or record.get("inspection_end_time")
    payload: dict[str, Any] = {
        "id": int(rid) if rid is not None else None,
        "stationNo": _opt_str(record.get("doc_number") or _cj(record, "doc_number")),
        "projectCode": _clean_str(project_code),
        "userId": _opt_str(user_id),
        "userName": _clean_str(user_name),
        # 关键部位/工序：GD-B-212 的 section(关键部位) + station_process(关键工序) 拼合
        "keyPart": _clean_str(
            " ".join(x for x in (
                _opt_str(record.get("section")), _opt_str(record.get("station_process")),
            ) if x)
        ),
        "supervisionLocation": _opt_str(record.get("station_location")),
        "constructionUnit": _opt_str(record.get("construction_unit") or _cj(record, "construction_unit")),
        "startTime": _fmt_datetime(start) or None,
        "endTime": _opt_str(_fmt_datetime(end)),
        "constructionDesc": _clean_str(
            record.get("station_content") or record.get("work_content") or ""
        ),
        "qualityCheck": _opt_str(record.get("station_supervision_status")),
        "safetyCheck": _opt_str(_cj(record, "qa_onsite")),
        "siteConditions": _opt_str(_cj(record, "worker_count")),
        "issuesFound": _opt_str(record.get("problems")),
        "handlingMeasures": _opt_str(record.get("treatment")),
        "remark": _opt_str(record.get("remark")),
        "weather": _opt_str(record.get("weather")),
        "photoUrls": _photos(photo_urls, public_file_url),
        "status": _status_of(record),
        "createdTime": _fmt_datetime(created_time),
        "updatedTime": _fmt_datetime(updated_time),
    }
    payload = _emit(payload)
    _check_required(payload, _COMMON_REQUIRED + (
        "userName", "keyPart", "startTime", "constructionDesc", "photoUrls",
    ), "002")
    return payload


# ---------------------------------------------------------------------------
# SUP-003 平行检验
# ---------------------------------------------------------------------------

def map_sup003(
    record: dict[str, Any],
    *,
    project_code: str,
    user_name: str,
    photo_urls: list[str],
    created_time: Any,
    updated_time: Any,
    user_id: str | None = None,
    public_file_url: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """平行检验（SUPERVISOR_PARALLEL_INSPECT）。record_type=验收(gd_b_213)记录。"""
    rid = record.get("id")
    result = record.get("acceptance_result") or _cj(record, "conclusion")
    payload: dict[str, Any] = {
        "id": int(rid) if rid is not None else None,
        "inspectNo": _opt_str(record.get("doc_number") or _cj(record, "doc_number")),
        "projectCode": _clean_str(project_code),
        "userId": _opt_str(user_id),
        "userName": _clean_str(user_name),
        "inspectDate": _fmt_date(record.get("record_date") or _cj(record, "time")),
        "inspectPart": _clean_str(record.get("section")),
        "constructionUnit": _opt_str(record.get("construction_unit") or _cj(record, "construction_unit")),
        "inspectBasis": _opt_str(record.get("acceptance_basis") or _cj(record, "basis")),
        "inspectMethod": _opt_str(_cj(record, "method")),
        "inspectEquipment": _opt_str(_cj(record, "instrument")),
        "inspectItem": _opt_str(_cj(record, "acceptance_content")),
        "inspectionData": _opt_str(_cj(record, "measure_data")),
        # 合格/是=1，不合格/否=0；未见值不带（宁缺勿错）
        "inspectResult": _yn_result(result),
        "inspectRecord": _opt_str(record.get("work_content") or _cj(record, "record")),
        "inspectConclusion": _opt_str(_cj(record, "conclusion")),
        "handlingRecord": _opt_str(record.get("treatment") or _cj(record, "handling")),
        "photoUrls": _photos(photo_urls, public_file_url),
        "status": _status_of(record),
        "createdTime": _fmt_datetime(created_time),
        "updatedTime": _fmt_datetime(updated_time),
    }
    payload = _emit(payload)
    _check_required(payload, _COMMON_REQUIRED + (
        "userName", "inspectDate", "inspectPart", "photoUrls",
    ), "003")
    return payload


def _yn_result(value: Any) -> int | None:
    """结论类（合格/不合格）→ 1/0。"""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if "不合格" in text:
        return 0
    if "合格" in text:
        return 1
    return None


def _norm_month(value: Any) -> str:
    """月份归一：``2026-11`` / ``2026年11月`` / ``2026/11`` → ``2026-11``。

    其他格式（无法归一）返回空串，交由调用方从 record_date 推导。
    """
    text = _clean_str(value)
    if not text:
        return ""
    m = re.match(r"^(\d{4})\s*[-/年\.]\s*(\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    return ""


# ---------------------------------------------------------------------------
# SUP-005 验收记录
# ---------------------------------------------------------------------------

def map_sup005(
    record: dict[str, Any],
    *,
    project_code: str,
    photo_urls: list[str],
    created_time: Any,
    updated_time: Any,
    acceptance_type: str,
    organizer_name: str | None = None,
    participants: str | None = None,
    public_file_url: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """验收记录（SUPERVISOR_ACCEPTANCE）。record_type=验收（GD-B-YS）。

    acceptanceType 是穗建通枚举（6.2），监理通的 acceptance_guide_ref/表单值
    直接透传，联调时对不上的由调用方归一——mapper 不猜枚举。
    """
    rid = record.get("id")
    payload: dict[str, Any] = {
        "id": int(rid) if rid is not None else None,
        "projectCode": _clean_str(project_code),
        "acceptanceType": _clean_str(acceptance_type),
        "acceptanceNo": _opt_str(record.get("doc_number") or _cj(record, "doc_number")),
        "acceptanceDate": _fmt_date(record.get("record_date") or _cj(record, "date")),
        "organizerName": _opt_str(organizer_name),
        "participants": _opt_str(participants or record.get("acceptance_participants")),
        "acceptanceContent": _opt_str(
            record.get("work_content") or _cj(record, "acceptance_content")
        ),
        "qualityStatus": _opt_str(record.get("acceptance_result")),
        "conclusion": _yn_result(record.get("acceptance_result")),
        "issues": _opt_str(record.get("problems")),
        "rectificationRequirements": _opt_str(record.get("treatment")),
        "photoUrls": _photos(photo_urls, public_file_url),
        "status": _status_of(record),
        "createdTime": _fmt_datetime(created_time),
        "updatedTime": _fmt_datetime(updated_time),
    }
    payload = _emit(payload)
    _check_required(payload, _COMMON_REQUIRED + ("acceptanceType", "acceptanceDate"), "005")
    return payload


# ---------------------------------------------------------------------------
# SUP-006 监理日志
# ---------------------------------------------------------------------------

def map_sup006(
    record: dict[str, Any],
    *,
    project_code: str,
    project_name: str,
    created_time: Any,
    updated_time: Any,
    photo_urls: list[str] | None = None,
    public_file_url: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """监理日志（SUPERVISOR_DAILY_LOG）。GD-B-220 表单值在 content_json。

    状态映射注意：006 的枚举是 1-生成中 2-预览中 3-已归档，与通用 1/2/3 语义
    错位（草稿→生成中、已提交→预览中、已归档→已归档），数值上仍复用 _STATUS_MAP。
    """
    rid = record.get("id")
    weather = record.get("weather") or _cj(record, "weather")
    payload: dict[str, Any] = {
        "id": int(rid) if rid is not None else None,
        "logNo": _opt_str(record.get("doc_number") or _cj(record, "doc_number")),
        "projectName": _clean_str(project_name),
        "projectCode": _clean_str(project_code),
        "logDate": _fmt_date(record.get("record_date") or _cj(record, "date")),
        "temperatureHigh": _opt_num(_cj(record, "temperature_high") or _cj(record, "temp_high")),
        "temperatureLow": _opt_num(_cj(record, "temperature_low") or _cj(record, "temp_low")),
        "weatherMorning": _opt_str(weather),
        "constructionLocation": _opt_str(record.get("section")),
        "constructionContent": _opt_str(
            _cj(record, "construction_progress") or record.get("work_content")
        ),
        "supervisionPersonnel": _opt_str(_cj(record, "supervision_section")),
        "constructionProgress": _opt_str(_cj(record, "construction_progress")),
        "patrolStatus": _opt_str(_cj(record, "supervision_section")),
        "foundProblems": _opt_str(record.get("problems") or _cj(record, "problems_section")),
        "handlingStatus": _opt_str(record.get("treatment")),
        "relatedMeetings": _opt_str(_cj(record, "other_section")),
        "relatedOtherMatters": _opt_str(record.get("remark") or _cj(record, "other_section")),
        "logImages": _photos(photo_urls, public_file_url),
        "status": _status_of(record),
        "createdTime": _fmt_datetime(created_time),
        "updatedTime": _fmt_datetime(updated_time),
    }
    payload = _emit(payload)
    _check_required(payload, _COMMON_REQUIRED + ("projectName", "logDate"), "006")
    return payload


# ---------------------------------------------------------------------------
# SUP-007 监理月报
# ---------------------------------------------------------------------------

def map_sup007(
    record: dict[str, Any],
    *,
    project_code: str,
    project_name: str,
    report_month: str,
    created_time: Any,
    updated_time: Any,
    compiler: str | None = None,
    reviewer: str | None = None,
    attachment_urls: list[str] | None = None,
    public_file_url: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """监理月报（SUPERVISOR_MONTHLY_REPORT）。GD-B-221 表单值在 content_json。

    reportMonth 必填 yyyy-MM：优先显式传（报告期），否则从 record_date 推。
    「2026年11月」中文周期标签也能解析（period_report_service 的 period_label）。
    """
    rid = record.get("id")
    month = _norm_month(report_month) or _fmt_date(record.get("record_date"))[:7]
    payload: dict[str, Any] = {
        "id": int(rid) if rid is not None else None,
        "reportNo": _opt_str(record.get("doc_number") or _cj(record, "doc_number")),
        "projectCode": _clean_str(project_code),
        "projectName": _clean_str(project_name),
        "reportMonth": month,
        "projectOverview": _opt_str(_cj(record, "project_overview")),
        "progressDesc": _opt_str(
            _cj(record, "progress_plan") or _cj(record, "progress_deviation")
        ),
        "qualityDesc": _opt_str(
            _cj(record, "quality_material") or _cj(record, "quality_acceptance")
            or _cj(record, "quality_issues")
        ),
        "safetyDesc": _opt_str(
            _cj(record, "safety_stats") or _cj(record, "safety_critical")
        ),
        "supervisionWork": _opt_str(_cj(record, "work_stats")),
        "issueAndMeasures": _opt_str(_cj(record, "quality_issues")),
        "nextPlan": _opt_str(_cj(record, "next_focus")),
        "attachmentUrls": _photos(attachment_urls, public_file_url),
        "compiler": _opt_str(compiler or _cj(record, "chief_supervisor")),
        "compileDate": _opt_str(_fmt_date(_cj(record, "submit_date") or record.get("record_date"))),
        "status": _status_of(record),
        "createdTime": _fmt_datetime(created_time),
        "updatedTime": _fmt_datetime(updated_time),
    }
    payload = _emit(payload)
    _check_required(payload, _COMMON_REQUIRED + ("projectName", "reportMonth"), "007")
    return payload


# ---------------------------------------------------------------------------
# SUP-012 材料进场验收
# ---------------------------------------------------------------------------

def map_sup012(
    record: dict[str, Any],
    *,
    project_code: str,
    checker_name: str,
    photo_urls: list[str] | None = None,
    created_time: Any,
    updated_time: Any,
    checker_id: str | None = None,
    public_file_url: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """材料进场验收（SUPERVISOR_MATERIAL_CHECK）。GD-B-219 台账/材料记录。

    checkDate 必填，手册口径「以记录创建时间近似承载」→ 落到 record_date，
    再退 created_time。
    """
    rid = record.get("id")
    payload: dict[str, Any] = {
        "id": int(rid) if rid is not None else None,
        "projectCode": _clean_str(project_code),
        "materialName": _clean_str(
            record.get("witness_material_name") or _cj(record, "material_name")
        ),
        "materialSpec": _opt_str(
            record.get("witness_material_spec") or _cj(record, "material_spec")
        ),
        "materialBatch": _opt_str(_cj(record, "batch_no") or _cj(record, "material_batch")),
        "manufacturer": _opt_str(_cj(record, "manufacturer")),
        "quantity": _opt_num(
            record.get("witness_quantity") or _cj(record, "quantity")
        ),
        "unit": _opt_str(record.get("witness_unit") or _cj(record, "unit")),
        "arrivalDate": _opt_str(_fmt_date(record.get("record_date"))),
        "checkDate": _fmt_date(
            record.get("record_date") or _fmt_date(created_time)
        ),
        "materialUsageLocation": _opt_str(_cj(record, "use_part") or record.get("section")),
        "certificateNo": _opt_str(record.get("witness_cert_no") or _cj(record, "cert_no")),
        "checkResult": _yn_result(
            record.get("witness_accept_result") or _cj(record, "acceptance_result")
        ),
        "photoUrls": _photos(photo_urls, public_file_url),
        "checkerId": _opt_str(checker_id),
        "checkerName": _clean_str(checker_name),
        "status": _status_of(record),
        "createdTime": _fmt_datetime(created_time),
        "updatedTime": _fmt_datetime(updated_time),
    }
    payload = _emit(payload)
    _check_required(payload, _COMMON_REQUIRED + ("materialName", "checkDate", "checkerName"), "012")
    return payload


# ---------------------------------------------------------------------------
# SUP-021 见证取样
# ---------------------------------------------------------------------------

def map_sup021(
    record: dict[str, Any],
    *,
    project_code: str,
    witness_person: str,
    photo_urls: list[str] | None = None,
    created_time: Any,
    updated_time: Any,
    witness_person_id: str | None = None,
    public_file_url: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """见证取样（SUPERVISOR_WITNESS_SAMPLING）。JL-JZ 表 / witness_* 列。

    sampleNo 必填：台账序号/编号没有就拒收（不编造假编号污染对端）。
    """
    rid = record.get("id")
    sample_no = _opt_str(
        record.get("doc_number") or _cj(record, "batch_no") or _cj(record, "serial_no")
    )
    payload: dict[str, Any] = {
        "id": int(rid) if rid is not None else None,
        "projectCode": _clean_str(project_code),
        "sampleNo": _clean_str(sample_no),
        "sampleName": _clean_str(
            record.get("witness_material_name") or _cj(record, "material_name")
        ),
        "sampleSpec": _opt_str(
            record.get("witness_material_spec") or _cj(record, "material_spec")
        ),
        "sampleQuantity": _opt_num(record.get("witness_quantity") or _cj(record, "quantity")),
        "unit": _opt_str(record.get("witness_unit") or _cj(record, "unit")),
        "samplingLocation": _opt_str(record.get("section")),
        "samplingDate": _fmt_date(
            record.get("record_date") or _cj(record, "sample_date")
        ),
        "witnessPersonId": _opt_str(witness_person_id),
        "witnessPerson": _clean_str(witness_person),
        "testReportNo": _opt_str(record.get("witness_cert_no") or _cj(record, "cert_no")),
        "testResult": _yn_result(_cj(record, "test_result")),
        "photoUrls": _photos(photo_urls, public_file_url),
        "status": _status_of(record),
        "createdTime": _fmt_datetime(created_time),
        "updatedTime": _fmt_datetime(updated_time),
    }
    payload = _emit(payload)
    _check_required(payload, _COMMON_REQUIRED + (
        "sampleNo", "sampleName", "samplingDate", "witnessPerson",
    ), "021")
    return payload
