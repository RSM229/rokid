"""穗建通数据采集接口 SUP-001「巡视记录采集」映射器（原型）。

来源：文档/穗建通SDK文档/supervision_data_api.md — DATA-IN-SUP-001
     POST http://{ip:port}/tenantgz/e-api/ods/push/patrol_record

职责边界（刻意收窄）：
- 只做「监理通记录 → 穗建通 data 对象」的纯函数映射，不碰 DB、不发 HTTP。
- 输入是调用方已从 ORM/JSON 组装好的普通 dict，便于单测与联调分离。
- 必填字段缺失时抛 MissingRequiredError，绝不静默填占位值污染数据中台。
- 占位符（{ip:port}/appCode/token）由调用方注入，mapper 不猜。

对接契约（21 类接口通用，此处只实现 001）：
    Headers: Authorization: Bearer / appCode / X-Data-Source: jlt
    Body: {"data": {...}}
    Response status: INSERT/UPDATE/DUPLICATE_INSERT/DUPLICATE_UPDATE
幂等：以监理通记录 id 作为 srcId 上报，重复推送返回 DUPLICATE_* 不算失败。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable


class MissingRequiredError(ValueError):
    """必填字段缺失或为空，禁止上报。"""

    def __init__(self, fields, code: str = "001"):
        self.fields = fields
        super().__init__(f"SUP-{code} 必填字段缺失: {', '.join(fields)}")


# SUP-001 接口要求的必填字段（来自手册「请求参数（Body）」表）
REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "projectCode",
    "userName",
    "patrolDate",
    "patrolStartTime",
    "patrolArea",
    "contentType",
    "sceneDesc",
    "photoUrls",
    "status",
    "createdTime",
    "updatedTime",
)

# 可选字段：监理通有对应来源才带上，没有就不发（避免用空串冒充"无"）
OPTIONAL_FIELDS: tuple[str, ...] = (
    "patrolNo", "userId", "patrolEndTime", "constructionUnit",
    "constructionProgressDesc", "constructionAsDesign", "materialQualified",
    "qualityStaffOnJob", "specialWorkCertified", "weather",
    "temperatureHigh", "temperatureLow", "longitude", "latitude",
    "constructionPersonnel", "supervisionPersonnel", "patrolStatus",
    "foundProblems", "handlingStatus", "signatureUrl",
)

# 巡检内容六项 → 安全/质量 归类。监理通 GD-B-214 的 content 是六项固定文本，
# 分类需要显式判定；无法判定时不猜，交由调用方传 contentType。
_CONTENT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "安全": ("安全文明施工", "特种作业", "持证上岗", "安全隐患"),
    "质量": ("工程设计文件", "质量标准", "工程材料", "构配件", "设备是否合格"),
}

# 监理通状态 → 穗建通 status：1-草稿 2-已提交 3-已锁定
_STATUS_MAP: dict[str, int] = {
    "草稿": 1,
    "进行中": 1,
    "已提交": 2,
    "已审批": 2,
    "已驳回": 2,
    "已归档": 3,
}
_DEFAULT_STATUS = 1  # 未知状态一律按草稿，不冒充已提交


def _fmt_date(value: Any) -> str:
    """统一 yyyy-MM-dd。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip().replace("/", "-")[:10]


def _fmt_time(value: Any) -> str:
    """统一 HH:mm:ss（手册要求 24 小时制）。"""
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    text = str(value or "").strip()
    return text if len(text) >= 8 else text + ":00" if len(text) == 5 else text


def _fmt_datetime(value: Any) -> str:
    """统一 yyyy-MM-dd HH:mm:ss。"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "").strip().replace("T", " ")[:19]


def _yn(value: Any) -> int | None:
    """是否类字段 → 1/0；None 表示源数据没答，不默认为否。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value).strip()
    if text in {"1", "是", "合格", "到位", "已持证"}:
        return 1
    if text in {"0", "否", "不合格", "未到位", "未持证"}:
        return 0
    return None


def classify_content_type(content: str | None) -> str | None:
    """从六项巡视内容文本推断 contentType（安全/质量）。

    单项可归类时返回该值；同时命中两类或都不命中返回 None，
    由调用方显式传值——不做猜测性上报。
    """
    text = content or ""
    hits = {name for name, kws in _CONTENT_TYPE_KEYWORDS.items() if any(k in text for k in kws)}
    return next(iter(hits)) if len(hits) == 1 else None


def map_sup001(
    record: dict[str, Any],
    *,
    project_code: str,
    user_name: str,
    photo_urls: list[str],
    created_time: Any,
    updated_time: Any,
    created_id: int | None = None,
    content_type: str | None = None,
    public_file_url: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """把一条监理通巡视记录映射成 SUP-001 的请求体。

    Args:
        record: 规范化记录，键名沿用 WorkRecord 字段名：
            id, record_date, inspection_start_time, inspection_end_time,
            section, work_content, weather, inspection_remark, problems,
            treatment, status, content_json, location, longitude/latitude
        project_code: 穗建通项目编码（PRJ2026...）；调用方负责映射表。
        user_name: 记录人姓名（对应数据中台 userCode 关联人）。
        photo_urls: 已上传文件服务的可访问 URL 列表（不能是 /uploads/ 本地路径）。
        created_time / updated_time: 记录创建/更新时间。
        created_id: 显式指定上报 id（幂等键）；默认取 record["id"]。
        content_type: 安全/质量；不传时从 work_content 推断，推断不出则报缺失。
        public_file_url: 可选，把 /uploads/xxx 转公网 URL 的钩子。

    Returns:
        {"data": {...}}，可直接作为 POST body。

    Raises:
        MissingRequiredError: 任一必填字段缺失。
    """
    cj = record.get("content_json") or {}
    if not isinstance(cj, dict):
        cj = {}

    rid = created_id if created_id is not None else record.get("id")
    ctype = content_type or classify_content_type(str(record.get("work_content") or ""))

    # 照片 URL：允许调用方传钩子做本地路径→公网 URL 转换
    urls = list(photo_urls or [])
    if public_file_url is not None:
        urls = [public_file_url(u) for u in urls]
    urls = [u for u in urls if isinstance(u, str) and u.strip()]

    payload: dict[str, Any] = {
        "id": int(rid) if rid is not None else None,
        "projectCode": str(project_code or "").strip(),
        "userName": str(user_name or "").strip(),
        "patrolDate": _fmt_date(record.get("record_date")),
        "patrolStartTime": _fmt_time(record.get("inspection_start_time")),
        "patrolArea": str(record.get("section") or "").strip(),
        "contentType": str(ctype or "").strip(),
        "sceneDesc": str(record.get("work_content") or "").strip(),
        "photoUrls": urls,
        "status": _STATUS_MAP.get(str(record.get("status") or ""), _DEFAULT_STATUS),
        "createdTime": _fmt_datetime(created_time),
        "updatedTime": _fmt_datetime(updated_time),
    }

    # 可选字段：有值才带
    mapping: dict[str, Callable[[], Any]] = {
        "patrolNo": lambda: record.get("doc_number") or cj.get("doc_number"),
        "userId": lambda: cj.get("user_code") or cj.get("user_id"),
        "patrolEndTime": lambda: _fmt_time(record.get("inspection_end_time")),
        "constructionUnit": lambda: cj.get("construction_unit"),
        "constructionProgressDesc": lambda: cj.get("construction_progress"),
        "constructionAsDesign": lambda: _yn(cj.get("construction_as_design")),
        "materialQualified": lambda: _yn(cj.get("material_qualified")),
        "qualityStaffOnJob": lambda: _yn(cj.get("quality_staff_on_job")),
        "specialWorkCertified": lambda: _yn(cj.get("special_work_certified")),
        "weather": lambda: record.get("weather"),
        "temperatureHigh": lambda: cj.get("temperature_high"),
        "temperatureLow": lambda: cj.get("temperature_low"),
        "longitude": lambda: record.get("longitude") or cj.get("longitude"),
        "latitude": lambda: record.get("latitude") or cj.get("latitude"),
        "foundProblems": lambda: record.get("problems") or None,
        "handlingStatus": lambda: record.get("treatment") or None,
        "signatureUrl": lambda: cj.get("signature_url"),
    }
    for key, getter in mapping.items():
        value = getter()
        if value not in (None, "", []):
            payload[key] = value

    missing = [f for f in REQUIRED_FIELDS if payload.get(f) in (None, "", [])]
    if missing:
        raise MissingRequiredError(missing)

    return {"data": payload}


def assert_duplicate_ok(response: dict[str, Any]) -> bool:
    """判定响应是否为「重复推送」（不算失败，视为幂等成功）。"""
    code = response.get("code")
    status = (response.get("data") or {}).get("status") or ""
    return code == 0 and str(status).startswith("DUPLICATE")
