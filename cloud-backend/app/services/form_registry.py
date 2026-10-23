"""监理文书表式注册表。

把省统表、安全监理用表及系统内部表式统一登记。每类声明：
  - form_code   : 稳定标识（写入 documents.form_code）
  - name        : 文书显示名
  - template    : 模板 docx 文件名（可空 = 暂以内容 PDF/docx 生成）
  - folder      : sheng_tong_biao / safety / none
  - category    : 分组（现场记录/通知类/报告日志类/管理类）
  - source      : record=由工作记录勾选生成；aggregate=按日/周/月聚合生成；manual=仅手工/LLM 生成
  - record_types: 可由哪些工作记录类型（巡视/验收/见证/旁站）生成
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FormSpec:
    form_code: str          # 稳定标识，如 "gd_b_214_xunshi"
    name: str               # 文书名
    template: str | None    # 模板 docx 文件名（相对知识库目录）
    folder: str             # sheng_tong_biao / safety / none
    form_number: str        # 表格编号，如 GD-B-214
    category: str           # 现场记录/通知单/日志报告/管理类
    source: str             # record / aggregate / manual
    record_types: tuple = field(default_factory=tuple)
    default_title: str = "" # 缺省标题，可含 {date} {period} 占位
    agent_work_record_type: str | None = None
    agent_record_type: str | None = None
    agent_business_route: str | None = None


def _spec(form_code: str, name: str, template: str | None, folder: str,
          form_number: str, category: str, source: str,
          record_types: tuple = (), default_title: str = "",
          agent_work_record_type: str | None = None,
          agent_record_type: str | None = None,
          agent_business_route: str | None = None) -> FormSpec:
    return FormSpec(form_code, name, template, folder, form_number, category,
                    source, record_types, default_title, agent_work_record_type,
                    agent_record_type, agent_business_route)


# 按“编号”顺序列出省统表 word 版 25 类 + 安全监理用表常用 4 类
FORMS: list[FormSpec] = [
    # 业务验收记录：内部编号YS；没有伪造官方统表编号，使用独立字段文书。
    _spec("acceptance_record", "验收记录", "验收记录.docx", "none", "YS", "现场记录", "record", ("acceptance",), "验收记录", "验收"),
    # 见证取样没有独立省统表模板；使用内部结构化文书，不能冒用材料进场台账。
    _spec("witness_record", "见证取样记录", None, "none", "JL-JZ", "现场记录", "record", ("witness",), "见证取样记录", "见证"),
    # ---- 项目监理机构组建/管理类（1-9）----
    _spec("gd_a1_117_faren", "法定代表人任命书", "1法定代表人任命书.docx", "sheng_tong_biao", "GD-A1-117", "管理类", "manual"),
    _spec("gd_a1_118_zhiliang", "工程质量终身责任书", "2质量终身责任书.docx", "sheng_tong_biao", "GD-A1-118", "管理类", "manual"),
    _spec("gd_b_1_zongjian", "总监理工程师任命书", "3总监任命书.docx", "sheng_tong_biao", "GD-B-1", "管理类", "manual"),
    _spec("gd_b_2_zongjian_bg", "总监理工程师变更申请书", "4总监变更申请书.docx", "sheng_tong_biao", "GD-B-2", "管理类", "manual"),
    _spec("gd_b_3_zongdai", "总监理工程师代表授权书", "5总代授权书.docx", "sheng_tong_biao", "GD-B-3", "管理类", "manual"),
    _spec("gd_b_4_yinzhang", "项目印章使用授权书", "6项目印章使用授权书.docx", "sheng_tong_biao", "GD-B-4", "管理类", "manual"),
    _spec("gd_b_5_zhuchang", "驻场人员通知书", "7驻场人员通知书.docx", "sheng_tong_biao", "GD-B-5", "管理类", "manual"),
    _spec("gd_b_6_zhuchang_tz", "驻场人员调整通知书", "8驻场人员调整通知书.docx", "sheng_tong_biao", "GD-B-6", "管理类", "manual"),
    _spec("gd_b_28_guihua", "监理规划", "9监理规划.docx", "sheng_tong_biao", "GD-B-28", "管理类", "manual"),
    _spec("gd_b_28_xize", "监理实施细则", "10监理细则.docx", "sheng_tong_biao", "GD-B-28", "管理类", "manual"),
    # ---- 审查/指令类（11-13）----
    _spec("gd_b_29_shencha", "项目监理机构审查表", "11监理审查表.docx", "sheng_tong_biao", "GD-B-29", "通知类", "manual", ("acceptance",)),
    _spec("gd_b_210_kaigong", "工程开工令", "12工程开工令.docx", "sheng_tong_biao", "GD-B-210", "通知类", "manual"),
    _spec("gd_b_211_zanting", "工程暂停令", "13暂停令.docx", "sheng_tong_biao", "GD-B-211", "通知类", "manual"),
    # ---- 现场记录类（14-18）----
    _spec("gd_b_212_pangzhan", "旁站记录", "14旁站记录.docx", "sheng_tong_biao", "GD-B-212", "现场记录", "record", ("station",), "旁站记录", "旁站"),
    _spec("gd_b_213_pingxing", "平行检验记录", "15平行检查记录.docx", "sheng_tong_biao", "GD-B-213", "现场记录", "record", ("parallel_inspection",), "平行检验记录", agent_record_type="平行检验", agent_business_route="parallel_inspection"),
    _spec("gd_b_214_xunshi", "巡视记录", "16巡视记录.docx", "sheng_tong_biao", "GD-B-214", "现场记录", "record", ("inspection",), "巡视记录", "巡视"),
    _spec("gd_b_215_tongzhi", "监理通知单（质量类）", "17监理通知单、回复单.docx", "sheng_tong_biao", "GD-B-215", "通知类", "manual", ("inspection", "acceptance")),
    _spec("gd_b_217_huiyi", "会议纪要", "18会议纪要.docx", "sheng_tong_biao", "GD-B-217", "现场记录", "manual"),
    # ---- 台账类（19-20）----
    _spec("gd_b_218_biangeng", "工程变更台账", "19工程变更台账.docx", "sheng_tong_biao", "GD-B-218", "管理类", "manual"),
    _spec("gd_b_219_cailiao", "材料/构配件/设备进场台账", "20材料台账.docx", "sheng_tong_biao", "GD-B-219", "管理类", "manual"),
    # ---- 日志与报告（21-22）----
    # 模板：2026-10-15 由省统表 21监理日志.docx（15 列网格）换为「房建新版」
    # 21监理日志-房建新.docx（9 列网格，工程名称/编号在表内首行）。
    # 填充器 fill_rizhi 双版式自适应，回退旧模板无需改代码。
    _spec("gd_b_220_rizhi", "监理日志", "21监理日志-房建新.docx", "sheng_tong_biao", "GD-B-220", "日志报告", "aggregate", (), "监理日志_{date}"),
    _spec("anquan_jianli_rizhi", "安全监理日志", "安全监理日志表格.docx", "safety", "安全监理日志", "日志报告", "aggregate", (), "安全监理日志_{date}"),
    _spec("gd_b_221_yuebao", "监理月报", "22监理月报.docx", "sheng_tong_biao", "GD-B-221", "日志报告", "aggregate", (), "监理月报_{period}"),
    _spec("gd_b_223_zhoubao", "监理周报", None, "none", "GD-B-223", "日志报告", "aggregate", (), "监理周报_{period}"),
    # ---- 报告/联系/归档类（23-25）----
    _spec("gd_b_222_zhiliang", "工程质量事故报告", "23质量事故监理报告.docx", "sheng_tong_biao", "GD-B-222", "日志报告", "manual"),
    _spec("gd_b_216_lianxi", "监理工作联系单", "24监理工作联系单.docx", "sheng_tong_biao", "GD-B-216", "通知类", "manual"),
    _spec("gd_b_224_dangangui", "监理档案移交目录", "25监理档案移交目录.docx", "sheng_tong_biao", "GD-B-224", "管理类", "manual"),
    # ---- 安全监理用表（GDAQ）----
    _spec("gdaq_30306_weida", "危险性较大的分部分项工程监理巡视检查记录表", "危险性较大的分部分项工程监理巡视检查记录表.docx", "safety", "GDAQ30306", "现场记录", "record", ("inspection",), "危大工程监理巡视检查记录", "巡视"),
    _spec("gdaq_30307_lianxi", "安全监理工作联系单", "安全监理工作联系单.docx", "safety", "GDAQ30307", "通知类", "manual"),
    _spec("gdaq_30308_yinhuan", "安全隐患整改通知（监理对施工单位项目部）", "安全隐患整改通知.docx", "safety", "GDAQ30308", "通知类", "record", (), "安全隐患整改通知"),
    _spec("gdaq_30309_huifu", "安全隐患整改回复单", None, "none", "GDAQ2030309", "通知类", "manual"),
    _spec("gdaq_30305_qizhong", "建筑施工起重机械安装拆卸监理检查记录表", "建筑施工起重机械安装拆卸监理检查记录表.docx", "safety", "GDAQ30305", "现场记录", "record", ("crane_safety",), "起重机械安拆监理检查记录", "起重机械安拆检查"),
]

FORM_BY_CODE: dict[str, FormSpec] = {f.form_code: f for f in FORMS}
FORM_BY_NAME: dict[str, FormSpec] = {f.name: f for f in FORMS}


def get_form(form_code: str | None) -> FormSpec | None:
    if not form_code:
        return None
    return FORM_BY_CODE.get(form_code) or FORM_BY_NAME.get(form_code)


def forms_for_record(record_type: str | None) -> list[FormSpec]:
    """给定工作记录类型，返回可生成的省统表。"""
    if not record_type:
        return FORMS
    return [f for f in FORMS if record_type in f.record_types]


def form_groups() -> list[dict]:
    """按 category 分组，供前端渲染。"""
    groups: dict[str, list[dict]] = {}
    for f in FORMS:
        groups.setdefault(f.category, []).append({
            "form_code": f.form_code,
            "name": f.name,
            "form_number": f.form_number,
            "source": f.source,
            "record_types": list(f.record_types),
            "has_template": bool(f.template),
        })
    return [{"category": k, "forms": v} for k, v in groups.items()]
