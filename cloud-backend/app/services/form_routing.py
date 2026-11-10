"""工程类型（房建 / 市政）感知的表式路由。

监理通同时承载房建与市政工程。两者使用不同的省统表/安全统表版式：
- 房建：GD-B / GDAQ 系列（监理日志 GD-B-220、旁站 GD-B-212、巡视 GD-B-214、
  危大 GDAQ30306、安全监理日志 anquan_jianli_rizhi …）。
- 市政：广东省市政监 / AQSZ 系列（监理日志 市政监-24、旁站 市政监-9、巡视 市政监-11、
  危大 AQSZ-J-2.5-1~7、安全监理日志 AQSZ-J-2.2 …）。

此前选表逻辑散落在 form_doc / period_report_service / record_field_spec 多处，
且全部硬编码房建 code。本模块把「按工程类型选表」收敛到一处，所有入口统一调用，
避免 `engineering_type == "市政"` 的判断重复散布、日后漂移。

路由依据：`Project.engineering_type`（`"房建" | "市政" | "其他" | NULL`）。
NULL/其他 一律按房建处理（向后兼容，现有项目未填类型时不改变行为）。
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# 房建默认表式（原 RECORD_TYPE_TO_FORM）
# ---------------------------------------------------------------------------
BUILDING_RECORD_DEFAULT: dict[str, str] = {
    "巡视": "gd_b_214_xunshi",
    "旁站": "gd_b_212_pangzhan",
    "验收": "acceptance_record",
    "见证": "witness_record",
    "起重机械安拆检查": "gdaq_30305_qizhong",
}
# 市政默认表式
MUNICIPAL_RECORD_DEFAULT: dict[str, str] = {
    "巡视": "shijian_11_xunshi",
    "旁站": "shijian_9_pangzhan",
    "验收": "acceptance_record",   # 市政验收暂无专用模板，沿用内部验收记录
    "见证": "witness_record",
    "起重机械安拆检查": "gdaq_30305_qizhong",
}

BUILDING_RECORD_EXTRA: dict[str, list[str]] = {
    "巡视": ["gd_b_214_xunshi", "gdaq_30306_weida", "gd_b_215_tongzhi"],
    "验收": ["acceptance_record"],
    "见证": ["witness_record"],
    "旁站": ["gd_b_212_pangzhan"],
    "起重机械安拆检查": ["gdaq_30305_qizhong"],
}
MUNICIPAL_RECORD_EXTRA: dict[str, list[str]] = {
    "巡视": [
        "shijian_11_xunshi",
        "aqsz_2_5_1_weida", "aqsz_2_5_2_weida", "aqsz_2_5_3_weida",
        "aqsz_2_5_4_weida", "aqsz_2_5_5_weida", "aqsz_2_5_6_weida", "aqsz_2_5_7_weida",
        "gd_b_215_tongzhi",
    ],
    "旁站": ["shijian_9_pangzhan"],
    "验收": ["acceptance_record"],
    "见证": ["witness_record"],
    "起重机械安拆检查": ["gdaq_30305_qizhong"],
}

# 危大 category -> 市政 AQSZ-J-2.5-x 具体表式
WEIDA_HAZARD_TO_MUNICIPAL: dict[str, str] = {
    "通用": "aqsz_2_5_1_weida",
    "基坑": "aqsz_2_5_2_weida",
    "深基坑": "aqsz_2_5_2_weida",
    "模板": "aqsz_2_5_3_weida",
    "支模": "aqsz_2_5_3_weida",
    "脚手架": "aqsz_2_5_4_weida",
    "起重": "aqsz_2_5_5_weida",
    "吊装": "aqsz_2_5_5_weida",
    "暗挖": "aqsz_2_5_6_weida",
    "盾构": "aqsz_2_5_6_weida",
    "有限空间": "aqsz_2_5_7_weida",
}
MUNICIPAL_WEIDA_FALLBACK = "aqsz_2_5_1_weida"

# 房建危大表式（默认）
BUILDING_WEIDA_DEFAULT = "gdaq_30306_weida"


def _eng_type(project) -> str:
    """取项目工程类型（空串兜底）。"""
    if project is None:
        return ""
    return str(getattr(project, "engineering_type", "") or "").strip()


def is_municipal(project) -> bool:
    """项目是否为市政工程。NULL/其他/房建 一律 False（向后兼容）。"""
    return _eng_type(project) == "市政"


def default_record_form(
    record_type: str, project=None, *, is_weida: bool = False
) -> str | None:
    """记录类型 -> 默认表式 code（按工程类型路由）。

    ``is_weida`` 为 True 时（危大工程巡视），忽略普通巡视默认表式，
    直接返回危大专用表式（市政=2.5-1 通用，房建=GDAQ30306）。
    """
    if is_weida:
        return MUNICIPAL_WEIDA_FALLBACK if is_municipal(project) else BUILDING_WEIDA_DEFAULT
    table = MUNICIPAL_RECORD_DEFAULT if is_municipal(project) else BUILDING_RECORD_DEFAULT
    return table.get(record_type)


def extra_record_forms(
    record_type: str, project=None, *, is_weida: bool = False
) -> list[str]:
    """记录类型 -> 可生成表式清单（供前端「记录→文书」选择器）。"""
    if is_weida:
        if is_municipal(project):
            return list(WEIDA_HAZARD_TO_MUNICIPAL.values()) + [BUILDING_WEIDA_DEFAULT]
        return [BUILDING_WEIDA_DEFAULT]
    table = MUNICIPAL_RECORD_EXTRA if is_municipal(project) else BUILDING_RECORD_EXTRA
    return list(table.get(record_type, []))


def municipal_weida_code(hazard_type: str | None) -> str:
    """危大 category -> 市政 AQSZ-J-2.5-x 具体表式。

    直接命中优先；否则按包含关系（如「深基坑开挖」含「基坑」→ 2.5-2）；
    都不中回退通用表 2.5-1。
    """
    if not hazard_type:
        return MUNICIPAL_WEIDA_FALLBACK
    key = str(hazard_type).strip()
    if key in WEIDA_HAZARD_TO_MUNICIPAL:
        return WEIDA_HAZARD_TO_MUNICIPAL[key]
    for frag, code in WEIDA_HAZARD_TO_MUNICIPAL.items():
        if frag in key:
            return code
    return MUNICIPAL_WEIDA_FALLBACK


def daily_log_form_code(project=None) -> str:
    """项目级监理日志（日聚合）表式 code。"""
    return "shijian_24_rizhi" if is_municipal(project) else "gd_b_220_rizhi"


def safety_log_form_code(project=None) -> str:
    """安全监理日志（日聚合）表式 code。"""
    return "aqsz_2_2_anquan_rizhi" if is_municipal(project) else "anquan_jianli_rizhi"


def record_is_weida(record) -> bool:
    """判断工作记录是否为危大工程巡视（content_json 含 hazard_type / is_weida 标记）。"""
    if record is None:
        return False
    cj = getattr(record, "content_json", None)
    if isinstance(cj, dict):
        if cj.get("is_weida"):
            return True
        if cj.get("hazard_type") or cj.get("hazard_name"):
            return True
    # 记录类型本身即危大（少数历史数据直接标 record_type）
    return str(getattr(record, "record_type", "") or "") == "危大工程巡视"


# 房建 code -> 市政对应 code（终极闸门翻译表）。
# Agent/历史数据可能把房建 code 存进 record.content_json.form_code，市政项目
# 生成时必须翻译；gdaq_30306_weida 特判按 hazard_type 细化到 AQSZ-J-2.5-x。
MUNICIPAL_BUILDING_OVERRIDE: dict[str, str | None] = {
    "gd_b_214_xunshi": "shijian_11_xunshi",
    "gd_b_212_pangzhan": "shijian_9_pangzhan",
    "gdaq_30306_weida": None,  # 特殊：按 hazard_type 细化
    "gd_b_220_rizhi": "shijian_24_rizhi",
    "anquan_jianli_rizhi": "aqsz_2_2_anquan_rizhi",
}


def effective_record_form_code(
    saved_code: str | None,
    record_type: str,
    project=None,
    record=None,
) -> str | None:
    """记录型文书「实际渲染 code」的终极闸门（form_doc 生成入口统一调用）。

    规则（不破坏房建路径、市政项目整链换表）：
    - ``saved_code`` 是用户/Agent 显式选定的表式（record.content_json.form_code）；
      市政项目下若为房建 code 则翻译到对应市政表式（gdaq_30306 按危大类型细化）；
      已是市政/其它 code 则原样保留。
    - 没有 saved_code 时按记录类型 + 危大标记路由：市政 → 市政默认表，
      危大 → AQSZ-J-2.5-x（按 hazard_type 选具体表）；房建始终走原默认。
    - ``record`` 用于取 hazard_type / is_weida 标记（可为 None）。
    """
    hazard: str | None = None
    is_weida = False
    if record is not None:
        cj = getattr(record, "content_json", None)
        if isinstance(cj, dict):
            hazard = str(cj.get("hazard_type") or cj.get("hazard_name") or "").strip() or None
            is_weida = bool(cj.get("is_weida")) or hazard is not None
        is_weida = is_weida or record_is_weida(record)

    if is_municipal(project):
        if saved_code:
            if saved_code == "gdaq_30306_weida":
                return municipal_weida_code(hazard)
            if saved_code in MUNICIPAL_BUILDING_OVERRIDE:
                return MUNICIPAL_BUILDING_OVERRIDE[saved_code]
            return saved_code
        if is_weida and str(record_type or "") == "巡视":
            return municipal_weida_code(hazard)
        return MUNICIPAL_RECORD_DEFAULT.get(str(record_type or ""))

    # 房建：显式选择优先，否则记录默认；危大巡视回 GDAQ30306。
    if saved_code:
        return saved_code
    if is_weida and str(record_type or "") == "巡视":
        return BUILDING_WEIDA_DEFAULT
    return BUILDING_RECORD_DEFAULT.get(str(record_type or ""))
