"""省统表字段级 schema 层（P2.1 模板层，穗建通上架路线图）。

定位：挂在 ``form_registry``（25 类 GD-B + 4 类 GDAQ 文书注册表）之上，
为高优先级表单补**字段级 JSON schema**——小程序动态表单渲染、必填校验、
结构化 payload 提取、以及后续穗建通数据开放平台回写的字段映射，全部以
这里的定义为唯一数据源。

schema 结构约定（与前端 components/prov-form 渲染组件配套）：
  form_code   : 关联 form_registry.FormSpec.form_code（稳定标识）
  form_number : 表格编号，如 GD-B-214（展示用）
  name        : 表单显示名
  version     : schema 版本。当前均为 draft —— 字段按纸质统表栏目整理，
                待 P0 拿到穗建通数据开放平台 schema 后逐项校准升版
  sections    : [{title, fields:[FieldSpec]}]，分组渲染
  signoffs    : [{role, required}] 签认位（后续接电子签章服务）

FieldSpec:
  key, label, type(text|textarea|date|time|select|number),
  required, options(select), placeholder, maxlen
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.services.form_registry import FORM_BY_CODE, FORMS

SCHEMA_VERSION = "draft-2026.09"

# ---------------------------------------------------------------------------
# schema 定义（按纸质统表栏目整理，平台 schema 到校准）
# ---------------------------------------------------------------------------

def _f(key: str, label: str, type_: str = "text", required: bool = False,
       options: Optional[list[str]] = None, placeholder: str = "",
       maxlen: int = 0, system: bool = False) -> dict:
    spec: dict[str, Any] = {"key": key, "label": label, "type": type_,
                            "required": required}
    if options:
        spec["options"] = options
    if placeholder:
        spec["placeholder"] = placeholder
    if maxlen:
        spec["maxlen"] = maxlen
    if system:
        spec["system"] = True
    return spec


def _sec(title: str, *fields: dict) -> dict:
    return {"title": title, "fields": list(fields)}


SCHEMAS: dict[str, dict] = {
    "gd_b_212_pangzhan": {
        "form_code": "gd_b_212_pangzhan", "form_number": "GD-B-212", "name": "旁站记录", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("1_01", "工程项目名称", "text", True, system=True),
                          _f("1_02", "编号", "text", True, system=True),
                          _f("1_03", "日期", "date", True, system=True),
                          _f("weather", "天气", "select", False, ["晴", "多云", "阴", "雨", "大风"], system=True),
                          _f("1_05", "旁站的关键部位", "text", True),
                          _f("1_06", "旁站的关键工序", "text", True),
                          _f("1_07", "旁站开始时间（年 月 日 时 分）", "datetime", True),
                          _f("1_08", "旁站结束时间（年 月 日 时 分）", "datetime", True),
                          _f("1_09", "施工单位", "text", True, system=True)),
            _sec("施工情况",
                          _f("1_10", "各专业作业人数情况", "text", True),
                          _f("1_11", "机械设备情况", "text", False),
                          _f("1_12", "材料品牌、规格、数量情况", "text", True)),
            _sec("监理情况",
                          _f("1_13", "施工单位质检员、安全员、施工员等到岗情况", "select", True, ["是", "否"]),
                          _f("1_14", "施工单位是否完成自查自检并达到质量标准", "select", False, ["是", "否"]),
                          _f("1_15", "检查内容及检查结果情况", "textarea", True)),
            _sec("问题与处理",
                          _f("1_16", "旁站发现的问题及处理情况", "text", False))
        ],
        "signoffs": [{"role": "旁站监理人员", "required": True}, {"role": "施工单位现场质检员", "required": False}],
    },
    "gd_b_213_pingxing": {
        "form_code": "gd_b_213_pingxing", "form_number": "GD-B-213", "name": "平行检查记录", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("2_01", "工程项目名称", "text", True, system=True),
                          _f("2_02", "编号", "text", True, system=True),
                          _f("2_03", "检查时间（年 月 日）", "date", True, system=True),
                          _f("2_04", "检查部位", "text", True, system=True),
                          _f("2_05", "检查项目", "text", True, system=True),
                          _f("2_06", "检查依据（图纸/规范编号）", "text", True, system=True),
                          _f("method", "检查方法", "text", True, system=True),
                          _f("2_08", "使用仪器设备名称及编号", "text", True, system=True),
                          _f("2_09", "实测数据明细", "text", True, system=True),
                          _f("2_10", "检查记录", "text", True, system=True),
                          _f("2_11", "检查结论", "select", True, ["合格", "不合格"]),
                          _f("2_12", "处理记录（不合格项整改要求）", "text", False, system=True))
        ],
        "signoffs": [{"role": "检查人员", "required": True}, {"role": "复核人员", "required": False}],
    },
    "gd_b_214_xunshi": {
        "form_code": "gd_b_214_xunshi", "form_number": "GD-B-214", "name": "巡视记录", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("3_01", "工程项目名称", "text", True, system=True),
                          _f("3_02", "编号", "text", True, system=True),
                          _f("3_03", "巡视的工程部位", "text", True),
                          _f("3_04", "施工单位", "text", True, system=True),
                          _f("3_05", "巡视开始时间（年 月 日 时）", "datetime", True),
                          _f("3_06", "巡视结束时间（年 月 日 时）", "datetime", True)),
            _sec("巡视内容",
                          _f("3_07", "巡视内容：（1.施工单位是否按工程设计文件、工程建设标准和批准的施工组织设计、（专项）施工方案施工 2.使用的工程材料、构配件和设备是否合格 3.施工现场管理人员，特别是施工质量管理人员是否到位 4.特种作业人员是否持证上岗 5.安全文明施工情况 6.其他）", "text", True, system=True),
                          _f("3_08", "施工作业人数", "number", False)),
            _sec("问题与处理",
                          _f("3_09", "巡视的问题及处理情况", "text", False))
        ],
        "signoffs": [{"role": "巡视监理人员", "required": True}],
    },
    "gd_b_215_tongzhi": {
        "form_code": "gd_b_215_tongzhi", "form_number": "GD-B-215", "name": "监理通知单", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("4_01", "工程项目名称", "text", True, system=True),
                          _f("4_02", "编号", "text", True, system=True),
                          _f("4_03", "日期", "date", True, system=True),
                          _f("4_04", "抄送单位（默认建设单位）", "text", False, system=True),
                          _f("to_unit", "致（施工单位）", "text", True, system=True)),
            _sec("内容",
                          _f("4_06", "事由（关于XXXX问题）", "text", True),
                          _f("4_07", "问题区域", "text", True),
                          _f("4_08", "存在问题描述", "textarea", True),
                          _f("4_09", "违反的规范/图纸/合同条款", "text", True),
                          _f("requirement", "整改要求", "text", True),
                          _f("4_11", "整改完成时限", "date", True),
                          _f("4_12", "附件（问题照片编号/检测报告）", "text", False)),
            _sec("签章",
                          _f("4_13", "项目监理机构（项目章）", "text", True, system=True))
        ],
        "signoffs": [{"role": "总监理工程师/专业监理工程师", "required": True}],
    },
    "gdaq_30308_yinhuan": {
        "form_code": "gdaq_30308_yinhuan", "form_number": "GDAQ30308", "name": "安全隐患整改通知", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("5_01", "工程项目名称", "text", True, system=True),
                          _f("5_02", "编号", "text", True, system=True),
                          _f("5_03", "日期", "date", True, system=True),
                          _f("5_04", "致（施工单位）", "text", True, system=True),
                          _f("5_05", "抄送单位（默认监督部门）", "text", False, system=True)),
            _sec("内容",
                          _f("5_06", "事由（检查类型/背景）", "text", True),
                          _f("part", "隐患部位", "text", True),
                          _f("5_08", "隐患内容描述", "textarea", True),
                          _f("requirement", "整改要求", "text", True),
                          _f("5_10", "整改完成时限", "date", True),
                          _f("5_11", "复查/回复要求", "text", True),
                          _f("5_12", "处罚依据", "text", False),
                          _f("5_13", "附件（隐患照片编号）", "text", False)),
            _sec("签章",
                          _f("5_14", "项目监理机构（项目章）", "text", True, system=True)),
            _sec("签收",
                          _f("5_15", "签收日期", "date", True))
        ],
        "signoffs": [{"role": "总监理工程师/总监代表", "required": True}, {"role": "安全负责人签收", "required": True}],
    },
    "acceptance_record": {
        "form_code": "acceptance_record", "form_number": "YS", "name": "验收记录", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("6_01", "工程项目名称", "text", True, system=True),
                          _f("6_02", "编号", "text", True, system=True),
                          _f("date", "验收日期", "date", True, system=True)),
            _sec("内容",
                          _f("acceptance_type", "验收类型", "select", True, ["隐蔽工程", "检验批", "进场验收", "分项分部"]),
                          _f("6_05", "验收部位、工序、材料、设备进场", "text", True),
                          _f("6_06", "验收内容（验收要点）", "text", True),
                          _f("6_07", "施工单位自检结论", "text", True),
                          _f("6_08", "监理验收结论", "text", True),
                          _f("6_09", "举牌验收照片", "text", True)),
            _sec("签章",
                          _f("6_10", "验收单位及人员", "text", True))
        ],
        "signoffs": [{"role": "监理验收人", "required": True}, {"role": "施工单位质检员", "required": True}],
    },
    "gd_b_220_rizhi": {
        "form_code": "gd_b_220_rizhi", "form_number": "GD-B-220", "name": "监理日志", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("7_01", "工程项目名称", "text", True, system=True),
                          _f("7_02", "编号", "text", True, system=True),
                          _f("date", "日期", "date", True, system=True),
                          _f("7_04", "星期", "text", True, system=True),
                          _f("weather", "天气", "select", True, ["晴", "暴雨", "雨", "阴", "台风"], system=True),
                          _f("temp_high", "最高气温（℃）", "number", False, system=True),
                          _f("temp_low", "最低气温（℃）", "number", False, system=True)),
            _sec("内容",
                          _f("7_08", "当日各部位施工进展", "text", True, system=True),
                          _f("7_09", "当日材料/构配件/设备进场情况", "text", True, system=True),
                          _f("7_10", "当日监理工作情况（旁站/巡视/平行检验/验收）", "text", True, system=True),
                          _f("7_11", "当日存在问题及处理情况", "text", True, system=True),
                          _f("7_12", "上级/质监站检查情况", "text", False),
                          _f("7_13", "其他有关事项", "text", False)),
            _sec("签章",
                          _f("7_14", "审阅日期", "date", False))
        ],
        "signoffs": [{"role": "专业监理工程师", "required": True}, {"role": "总监理工程师/总监理工程师代表", "required": False}],
    },
    "gd_b_221_yuebao": {
        "form_code": "gd_b_221_yuebao", "form_number": "GD-B-221", "name": "监理月报", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("8_01", "工程项目名称", "text", True, system=True),
                          _f("8_02", "编号", "text", True, system=True),
                          _f("8_03", "报告期（年 月）", "text", True, system=True)),
            _sec("进度控制",
                          _f("8_04", "工程概况及本月形象进度", "text", True),
                          _f("8_05", "进度控制：本月计划vs实际完成", "text", True),
                          _f("8_06", "进度偏差分析及下月计划", "text", True)),
            _sec("质量控制",
                          _f("8_07", "质量控制：材料进场及报验汇总", "text", True),
                          _f("8_08", "质量控制：检验批/分项/分部验收情况", "text", True),
                          _f("8_09", "质量控制：旁站/巡视/平行检验次数统计", "number", True),
                          _f("8_10", "质量控制：质量问题及整改闭环情况", "text", True)),
            _sec("造价控制",
                          _f("8_11", "造价控制：本月工程款支付情况", "text", False),
                          _f("8_12", "造价控制：工程变更及费用增减", "text", False)),
            _sec("安全管理",
                          _f("8_13", "安全管理：安全检查及隐患整改统计", "text", True),
                          _f("8_14", "安全管理：危大工程管控情况", "text", False)),
            _sec("合同管理",
                          _f("8_15", "合同管理：变更/索赔/延期/争议处理", "text", False)),
            _sec("监理工作",
                          _f("8_16", "监理工作统计汇总（通知单/联系单/会议次数）", "text", True),
                          _f("8_17", "下月监理工作重点", "text", True)),
            _sec("签章",
                          _f("8_18", "监理单位（盖章）", "text", True, system=True),
                          _f("8_19", "报送日期", "date", True, system=True))
        ],
        "signoffs": [{"role": "总监理工程师", "required": True}],
    },
    "gd_b_219_cailiao": {
        "form_code": "gd_b_219_cailiao", "form_number": "GD-B-219", "name": "工程材料/构配件/设备管理台账", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("9_01", "工程项目名称", "text", True, system=True),
                          _f("9_02", "序号", "number", True, system=True),
                          _f("9_03", "名称", "text", True),
                          _f("spec", "规格/型号", "text", True),
                          _f("quantity", "数量", "number", True),
                          _f("9_06", "生产厂家或品牌", "text", True)),
            _sec("进场",
                          _f("date", "进场日期", "date", True),
                          _f("9_08", "使用部位", "text", False)),
            _sec("见证取样与检测",
                          _f("9_09", "取样日期", "date", False),
                          _f("9_10", "检测结果", "select", True, ["合格", "不合格"])),
            _sec("验收",
                          _f("9_11", "进场验收结论", "select", True, ["合格", "不合格"]),
                          _f("9_12", "验收日期", "date", False)),
            _sec("三对应溯源",
                          _f("9_13", "报审表编号", "text", False))
        ],
        "signoffs": [{"role": "取样见证人签名", "required": False}, {"role": "验收人签名", "required": True}],
    },
    "gdaq_30306_weida": {
        "form_code": "gdaq_30306_weida", "form_number": "GDAQ30306", "name": "危大工程巡视记录", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("10_01", "工程项目名称", "text", True, system=True),
                          _f("10_02", "编号", "text", True, system=True),
                          _f("10_03", "作业日期", "date", True, system=True),
                          _f("10_04", "天气", "select", True, ["晴", "多云", "阴", "雨"], system=True),
                          _f("10_05", "风力", "select", False, ["无风", "微风", "和风"], system=True),
                          _f("10_06", "巡视的工程部位", "text", True),
                          _f("construct_unit", "施工单位", "text", True, system=True),
                          _f("10_08", "专业分包单位", "text", False, system=True),
                          _f("10_09", "危大工程名称/类型", "text", True),
                          _f("10_10", "危大工程类别", "select", True, ["一般危大工程", "超规模工程"]),
                          _f("10_11", "巡视开始时间（年 月 日 时）", "datetime", True),
                          _f("10_12", "巡视结束时间（年 月 日 时）", "datetime", False)),
            _sec("巡视内容",
                          _f("10_13", "巡视内容（按危大类型带出检查表）", "textarea", True),
                          _f("10_14", "施工单位项目负责人到位情况", "select", True, ["是", "否"]),
                          _f("10_15", "专职安全生产管理人员到位情况", "select", True, ["是", "否"]),
                          _f("10_16", "特种作业人员持证情况", "text", True),
                          _f("10_17", "监测数据记录（基坑位移/沉降等）", "text", False)),
            _sec("问题与处理",
                          _f("10_18", "巡视发现的问题及处理情况", "text", False),
                          _f("10_19", "隐患整改及复查闭环情况", "text", False)),
            _sec("签章",
                          _f("10_20", "审阅时间", "date", False))
        ],
        "signoffs": [{"role": "施工单位专职安全员", "required": True}, {"role": "专业分包单位专职安全员", "required": False}, {"role": "监理检查人员", "required": True}, {"role": "总监理工程师", "required": False}],
    },
    "anquan_jianli_rizhi": {
        "form_code": "anquan_jianli_rizhi", "form_number": "安全监理日志", "name": "安全监理日志", "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                          _f("11_01", "工程名称", "text", True, system=True),
                          _f("11_02", "日期", "date", True, system=True),
                          _f("11_03", "星期", "text", True, system=True),
                          _f("11_04", "天气（上午/下午/夜间）", "text", True, system=True)),
            _sec("内容",
                          _f("11_05", "施工内容", "text", True, system=True),
                          _f("11_06", "安全检查情况（包括巡视检查情况）", "text", True, system=True),
                          _f("11_07", "处理措施（限期整改、暂时停工、上报建设单位或有关部门）", "text", True, system=True),
                          _f("11_08", "施工单位隐患整改情况", "text", True, system=True),
                          _f("11_09", "危险性较大的分部分项工程安全状况", "text", True, system=True),
                          _f("11_10", "其他", "text", False)),
            _sec("签章",
                          _f("11_11", "审阅日期", "date", False))
        ],
        "signoffs": [{"role": "安全监理员", "required": True}, {"role": "总监理工程师/总监代表", "required": False}],
    },
"gd_b_217_huiyi": {
        "form_code": "gd_b_217_huiyi",
        "form_number": "GD-B-217",
        "name": "会议纪要",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("title", "会议名称", "text", True),
                 _f("date", "会议时间", "date", True),
                 _f("location", "会议地点", "text", True),
                 _f("host", "主持人", "text", True),
                 _f("attendees", "参会单位及人员", "textarea", True)),
            _sec("会议内容",
                 _f("agenda", "会议议题", "textarea", True),
                 _f("decisions", "议定事项", "textarea", True),
                 _f("action_items", "待办事项（含责任方及完成期限）", "textarea")),
        ],
        "signoffs": [{'role': '记录人', 'required': True}, {'role': '总监理工程师', 'required': False}],
    },
"gd_a1_117_faren": {
        "form_code": "gd_a1_117_faren",
        "form_number": "GD-A1-117",
        "name": "法定代表人任命书",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "任命日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("appointee", "受任人姓名", "text", True),
                 _f("position", "受任职务", "text", True),
                 _f("appoint_term", "任命期限", "text")),
            _sec("职责权限",
                 _f("duty_scope", "职责与权限", "textarea", True)),
        ],
        "signoffs": [{'role': '法定代表人', 'required': True}]    },
"gd_a1_118_zhiliang": {
        "form_code": "gd_a1_118_zhiliang",
        "form_number": "GD-A1-118",
        "name": "工程质量终身责任书",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "签署日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("responsible_unit", "责任单位", "text", True),
                 _f("responsible_person", "责任人姓名", "text", True),
                 _f("position", "岗位/职务", "text", True)),
            _sec("责任承诺",
                 _f("duty_scope", "质量责任范围", "textarea", True),
                 _f("promise", "终身责任承诺内容", "textarea")),
        ],
        "signoffs": [{'role': '法定代表人', 'required': True}]    },
"gd_b_1_zongjian": {
        "form_code": "gd_b_1_zongjian",
        "form_number": "GD-B-1",
        "name": "总监理工程师任命书",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "任命日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("appointee", "总监理工程师姓名", "text", True),
                 _f("cert_no", "注册执业证书号", "text", True)),
            _sec("授权内容",
                 _f("auth_scope", "授权范围及职责", "textarea", True)),
        ],
        "signoffs": [{'role': '监理单位法定代表人', 'required': True}]    },
"gd_b_2_zongjian_bg": {
        "form_code": "gd_b_2_zongjian_bg",
        "form_number": "GD-B-2",
        "name": "总监理工程师变更申请书",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "申请日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("to_unit", "致（建设单位）", "text", True)),
            _sec("变更事项",
                 _f("old_chief", "原总监理工程师姓名", "text", True),
                 _f("new_chief", "拟任总监理工程师姓名", "text", True),
                 _f("new_cert_no", "拟任总监注册执业证书号", "text"),
                 _f("reason", "变更原因", "textarea", True)),
        ],
        "signoffs": [{'role': '监理单位法定代表人', 'required': True}]    },
"gd_b_3_zongdai": {
        "form_code": "gd_b_3_zongdai",
        "form_number": "GD-B-3",
        "name": "总监理工程师代表授权书",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "授权日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("delegate", "被授权人（总监代表）姓名", "text", True),
                 _f("position", "职务/职称", "text")),
            _sec("授权内容",
                 _f("auth_scope", "授权范围及权限", "textarea", True),
                 _f("auth_term", "授权期限", "text")),
        ],
        "signoffs": [{'role': '总监理工程师', 'required': True}]    },
"gd_b_4_yinzhang": {
        "form_code": "gd_b_4_yinzhang",
        "form_number": "GD-B-4",
        "name": "项目印章使用授权书",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "授权日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("seal_name", "印章名称", "text", True),
                 _f("keeper", "印章保管人", "text", True)),
            _sec("授权内容",
                 _f("use_scope", "印章使用范围", "textarea", True),
                 _f("auth_term", "授权期限", "text")),
        ],
        "signoffs": [{'role': '监理单位法定代表人', 'required': True}]    },
"gd_b_5_zhuchang": {
        "form_code": "gd_b_5_zhuchang",
        "form_number": "GD-B-5",
        "name": "驻场人员通知书",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "签发日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("to_unit", "致（建设单位/施工单位）", "text", True),
                 _f("arrive_date", "进场日期", "date", True)),
            _sec("驻场人员",
                 _f("staff_list", "驻场人员名单（姓名/职务/联系方式）", "textarea", True),
                 _f("work_requirement", "工作要求说明", "textarea")),
        ],
        "signoffs": [{'role': '总监理工程师', 'required': True}]    },
"gd_b_6_zhuchang_tz": {
        "form_code": "gd_b_6_zhuchang_tz",
        "form_number": "GD-B-6",
        "name": "驻场人员调整通知书",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "签发日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("to_unit", "致（建设单位/施工单位）", "text", True),
                 _f("effective_date", "调整生效日期", "date", True)),
            _sec("调整事项",
                 _f("out_staff", "调出人员（姓名/职务）", "textarea", True),
                 _f("in_staff", "调入人员（姓名/职务/联系方式）", "textarea", True),
                 _f("reason", "调整原因", "textarea")),
        ],
        "signoffs": [{'role': '总监理工程师', 'required': True}]    },
"gd_b_28_guihua": {
        "form_code": "gd_b_28_guihua",
        "form_number": "GD-B-28",
        "name": "监理规划",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("工程概况",
                 _f("project_name", "工程名称", "text", True),
                 _f("project_location", "工程地点", "text", True),
                 _f("owner_unit", "建设单位", "text", True),
                 _f("construct_unit", "施工单位", "text"),
                 _f("supervision_scope", "监理范围", "textarea", True)),
            _sec("规划内容",
                 _f("work_goal", "监理工作目标", "textarea", True),
                 _f("work_content", "监理工作内容", "textarea", True),
                 _f("organization", "监理组织机构及人员配置", "textarea"),
                 _f("work_method", "监理工作方法及措施", "textarea")),
        ],
        "signoffs": [{'role': '总监理工程师', 'required': True}, {'role': '监理单位技术负责人', 'required': True}],
    },
"gd_b_28_xize": {
        "form_code": "gd_b_28_xize",
        "form_number": "GD-B-28",
        "name": "监理实施细则",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("project_name", "工程名称", "text", True),
                 _f("specialty", "专业名称", "text", True)),
            _sec("细则内容",
                 _f("specialty_feature", "专业工程特点", "textarea", True),
                 _f("work_flow", "监理工作流程", "textarea"),
                 _f("control_points", "监理工作控制要点及目标值", "textarea", True),
                 _f("work_method", "监理工作方法及措施", "textarea", True)),
        ],
        "signoffs": [{'role': '专业监理工程师', 'required': True}, {'role': '总监理工程师', 'required': True}],
    },
"gd_b_29_shencha": {
        "form_code": "gd_b_29_shencha",
        "form_number": "GD-B-29",
        "name": "项目监理机构审查表",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "审查日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("review_item", "审查项目", "text", True)),
            _sec("审查内容",
                 _f("review_content", "审查内容", "textarea", True),
                 _f("review_opinion", "审查意见", "textarea", True),
                 _f("conclusion", "审查结论", "select", True, ["符合要求", "不符合要求"])),
        ],
        "signoffs": [{'role': '专业监理工程师', 'required': True}, {'role': '总监理工程师', 'required': True}],
    },
"gd_b_210_kaigong": {
        "form_code": "gd_b_210_kaigong",
        "form_number": "GD-B-210",
        "name": "工程开工令",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "签发日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("to_unit", "致（施工单位）", "text", True),
                 _f("start_date", "开工日期", "date", True)),
            _sec("开工条件审查",
                 _f("condition_review", "开工条件审查情况", "textarea", True),
                 _f("order_content", "开工指令内容", "textarea", True)),
        ],
        "signoffs": [{'role': '总监理工程师', 'required': True}]    },
"gd_b_211_zanting": {
        "form_code": "gd_b_211_zanting",
        "form_number": "GD-B-211",
        "name": "工程暂停令",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "签发日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("to_unit", "致（施工单位）", "text", True),
                 _f("part", "暂停施工部位", "text", True),
                 _f("suspend_start", "暂停开始时间", "date", True)),
            _sec("暂停事项",
                 _f("reason", "暂停原因", "textarea", True),
                 _f("requirement", "整改要求", "textarea", True),
                 _f("resume_condition", "复工条件", "textarea")),
        ],
        "signoffs": [{'role': '总监理工程师', 'required': True}]    },
"gd_b_218_biangeng": {
        "form_code": "gd_b_218_biangeng",
        "form_number": "GD-B-218",
        "name": "工程变更台账",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("单条变更记录",
                 _f("date", "变更日期", "date", True),
                 _f("change_no", "变更编号", "text", True),
                 _f("summary", "变更内容摘要", "textarea", True),
                 _f("propose_unit", "提出单位", "select", False,
                    ["建设单位", "设计单位", "施工单位", "监理单位"]),
                 _f("amount", "变更金额(万元)", "number"),
                 _f("approval_status", "审批状态", "select", False,
                    ["待审批", "已批准", "已驳回"]),
                 _f("remark", "备注", "text")),
        ],
        "signoffs": [{'role': '专业监理工程师', 'required': True}]    },
"gd_b_223_zhoubao": {
        "form_code": "gd_b_223_zhoubao",
        "form_number": "GD-B-223",
        "name": "监理周报",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("period_start", "报告周期起", "date", True),
                 _f("period_end", "报告周期止", "date", True),
                 _f("project_name", "工程名称", "text", True)),
            _sec("本周情况",
                 _f("progress", "本周工程进展", "textarea", True),
                 _f("supervision_work", "本周监理工作情况", "textarea", True),
                 _f("problems", "发现的问题及处理情况", "textarea")),
            _sec("计划与协调",
                 _f("next_plan", "下周工作计划", "textarea", True),
                 _f("coordination", "需协调事项", "textarea")),
        ],
        "signoffs": [{'role': '总监理工程师', 'required': True}]    },
"gd_b_222_zhiliang": {
        "form_code": "gd_b_222_zhiliang",
        "form_number": "GD-B-222",
        "name": "工程质量事故报告",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("事故概况",
                 _f("occur_time", "事故发生时间", "date", True),
                 _f("part", "事故部位", "text", True),
                 _f("level", "事故等级", "select", True,
                    ["一般质量事故", "较大质量事故", "重大质量事故", "特别重大质量事故"]),
                 _f("loss_estimate", "直接经济损失估计(万元)", "number")),
            _sec("事故详情",
                 _f("accident_course", "事故经过", "textarea", True),
                 _f("cause_analysis", "原因初步分析", "textarea", True),
                 _f("treatment", "应急处理措施", "textarea"),
                 _f("suggestion", "处理意见及建议", "textarea")),
        ],
        "signoffs": [{'role': '总监理工程师', 'required': True}]    },
"gd_b_216_lianxi": {
        "form_code": "gd_b_216_lianxi",
        "form_number": "GD-B-216",
        "name": "监理工作联系单",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "签发日期", "date", True),
                 _f("to_unit", "致（相关单位）", "text", True),
                 _f("subject", "事由", "text", True)),
            _sec("联系内容",
                 _f("content", "联系事项内容", "textarea", True),
                 _f("requirement", "要求事项", "textarea"),
                 _f("reply_deadline", "回复期限", "date")),
        ],
        "signoffs": [{'role': '专业监理工程师', 'required': True}, {'role': '总监理工程师', 'required': True}],
    },
"gd_b_224_dangangui": {
        "form_code": "gd_b_224_dangangui",
        "form_number": "GD-B-224",
        "name": "监理档案移交目录",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "移交日期", "date", True),
                 _f("project_name", "工程名称", "text", True),
                 _f("receive_unit", "接收单位", "text", True)),
            _sec("单条档案记录",
                 _f("archive_name", "档案名称", "text", True),
                 _f("volume_count", "卷数/份数", "number"),
                 _f("media_type", "载体形式", "select", False,
                    ["纸质", "电子", "纸质+电子"]),
                 _f("remark", "备注", "text")),
        ],
        "signoffs": [{'role': '移交人', 'required': True}, {'role': '接收人', 'required': True}],
    },
"gdaq_30307_lianxi": {
        "form_code": "gdaq_30307_lianxi",
        "form_number": "GDAQ30307",
        "name": "安全监理工作联系单",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("date", "签发日期", "date", True),
                 _f("to_unit", "致（相关单位）", "text", True),
                 _f("subject", "事由", "text", True)),
            _sec("联系内容",
                 _f("safety_issue", "安全问题描述", "textarea", True),
                 _f("requirement", "要求事项", "textarea", True),
                 _f("reply_deadline", "回复期限", "date")),
        ],
        "signoffs": [{'role': '专业监理工程师', 'required': True}, {'role': '总监理工程师', 'required': True}],
    },
"gdaq_30309_huifu": {
        "form_code": "gdaq_30309_huifu",
        "form_number": "GDAQ2030309",
        "name": "安全隐患整改回复单",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("reply_date", "回复日期", "date", True),
                 _f("to_unit", "致（监理单位）", "text", True),
                 _f("part", "隐患部位", "text", True),
                 _f("hazard_desc", "安全隐患描述", "textarea")),
            _sec("整改情况",
                 _f("rectify_desc", "整改完成情况", "textarea", True),
                 _f("finish_date", "整改完成日期", "date"),
                 _f("rectify_image", "整改影像说明", "text")),
            _sec("复查情况",
                 _f("recheck_date", "复检日期", "date"),
                 _f("recheck_conclusion", "复查结论", "select", False,
                    ["合格", "不合格", "待复查"]),
                 _f("recheck_remark", "复查意见", "textarea"),
                 _f("rechecker", "复查人", "text", True)),
        ],
        "signoffs": [{'role': '施工单位项目经理', 'required': True}, {'role': '专业监理工程师', 'required': True}],
    },
"gdaq_30305_qizhong": {
        "form_code": "gdaq_30305_qizhong",
        "form_number": "GDAQ30305",
        "name": "建筑施工起重机械安装拆卸监理检查记录表",
        "version": SCHEMA_VERSION,
        "sections": [
            _sec("基本信息",
                 _f("check_date", "检查日期", "date", True),
                 _f("machine_name", "起重机械名称及型号", "text", True),
                 _f("work_type", "作业类别", "select", True, ["安装", "拆卸", "顶升/加节"]),
                 _f("install_unit", "安装拆卸单位", "text", True)),
            _sec("检查内容",
                 _f("notice_status", "安拆告知及方案审批情况", "textarea", True),
                 _f("worker_cert", "作业人员持证上岗情况", "textarea", True),
                 _f("site_check", "现场检查情况", "textarea", True),
                 _f("problems", "发现的问题及处理意见", "textarea")),
        ],
        "signoffs": [{'role': '专业监理工程师', 'required': True}, {'role': '总监理工程师', 'required': False}],
    }
}

# ---------------------------------------------------------------------------
# schema-key → canonical content-key 映射（P3 通用录入页落库用）
#
# 后端 Document.content 使用一套 canonical key（project_name/section/content/…
# 见 form_renderer / record_fields.validate_document），而 prov-form schema 用
# 独立的字段 key（1_01 / 3_03 …）。通用录入页把表单值落库前必须做这一层映射，
# 否则 docx 导出与提交校验会全部断链。system 字段（工程项目名称/编号/施工单位/
# 项目监理机构）由后端用项目上下文覆盖，前端只读。
#
# 仅登记 11 类现场记录表式的映射；其余管理类表单走各自既有链路，不在此列。
CONTENT_MAP: dict[str, dict[str, str]] = {
    "gd_b_212_pangzhan": {
        "1_01": "project_name", "1_02": "doc_number", "1_03": "date", "weather": "weather",
        "1_05": "section", "1_06": "station_process", "1_07": "start_time", "1_08": "end_time",
        "1_09": "construction_unit", "1_10": "worker_count", "1_11": "equipment", "1_12": "material_info",
        "1_13": "qa_onsite", "1_14": "self_check", "1_15": "content", "1_16": "problems",
    },
    "gd_b_213_pingxing": {
        "2_01": "project_name", "2_02": "doc_number", "2_03": "time", "2_04": "section",
        "2_05": "acceptance_content", "2_06": "basis", "method": "method", "2_08": "instrument",
        "2_09": "measure_data", "2_10": "record", "2_11": "conclusion", "2_12": "handling",
    },
    "gd_b_214_xunshi": {
        "3_01": "project_name", "3_02": "doc_number", "3_03": "section", "3_04": "construction_unit",
        "3_05": "start_time", "3_06": "end_time", "3_07": "content", "3_08": "worker_count", "3_09": "problems",
    },
    "gd_b_215_tongzhi": {
        "4_01": "project_name", "4_02": "doc_number", "4_03": "date", "4_04": "cc_unit",
        "to_unit": "to_unit", "4_06": "subject", "4_07": "section", "4_08": "problems",
        "4_09": "violation", "requirement": "requirement", "4_11": "deadline", "4_12": "attachment", "4_13": "supervision_org",
    },
    "gdaq_30308_yinhuan": {
        "5_01": "project_name", "5_02": "doc_number", "5_03": "date", "5_04": "to_unit", "5_05": "cc_unit",
        "5_06": "subject", "part": "section", "5_08": "problems", "requirement": "requirement",
        "5_10": "deadline", "5_11": "reply_requirement", "5_12": "penalty_basis", "5_13": "attachment",
        "5_14": "supervision_org", "5_15": "receipt_date",
    },
    "acceptance_record": {
        "6_01": "project_name", "6_02": "doc_number", "date": "date", "acceptance_type": "acceptance_type",
        "6_05": "section", "6_06": "acceptance_content", "6_07": "self_check_conclusion", "6_08": "supervision_conclusion",
        "6_09": "photo", "6_10": "acceptance_participants",
    },
    "gd_b_220_rizhi": {
        "7_01": "project_name", "7_02": "doc_number", "date": "date", "7_04": "weekday", "weather": "weather",
        "temp_high": "temperature_high", "temp_low": "temperature_low", "7_08": "construction_progress",
        "7_09": "material_section", "7_10": "supervision_section", "7_11": "problems_section",
        "7_12": "supervision_check", "7_13": "other_section", "7_14": "review_date",
    },
    "gd_b_221_yuebao": {
        "8_01": "project_name", "8_02": "doc_number", "8_03": "report_period",
        "8_04": "project_overview", "8_05": "progress_plan", "8_06": "progress_deviation",
        "8_07": "quality_material", "8_08": "quality_acceptance", "8_09": "quality_stats", "8_10": "quality_issues",
        "8_11": "cost_payment", "8_12": "cost_change", "8_13": "safety_stats", "8_14": "safety_critical",
        "8_15": "contract_mgmt", "8_16": "work_stats", "8_17": "next_focus", "8_18": "supervision_org", "8_19": "submit_date",
    },
    "gd_b_219_cailiao": {
        "9_01": "project_name", "9_02": "serial_no", "9_03": "material_name", "spec": "material_spec",
        "quantity": "quantity", "9_06": "manufacturer", "date": "date", "9_08": "use_part",
        "9_09": "sample_date", "9_10": "test_result", "9_11": "acceptance_result",
        "9_12": "acceptance_date", "9_13": "report_no",
    },
    "gdaq_30306_weida": {
        "10_01": "project_name", "10_02": "doc_number", "10_03": "date", "10_04": "weather", "10_05": "wind",
        "10_06": "section", "construct_unit": "construction_unit", "10_08": "subcontractor",
        "10_09": "hazard_name", "10_10": "hazard_category", "10_11": "start_time", "10_12": "end_time",
        "10_13": "content", "10_14": "pm_onsite", "10_15": "safety_onsite", "10_16": "special_cert",
        "10_17": "monitoring", "10_18": "problems", "10_19": "rectify", "10_20": "review_date",
    },
    "anquan_jianli_rizhi": {
        "11_01": "project_name", "11_02": "date", "11_03": "weekday", "11_04": "weather",
        "11_05": "construction_progress", "11_06": "supervision_section", "11_07": "handling",
        "11_08": "problems_section", "11_09": "hazard_status", "11_10": "other_section", "11_11": "review_date",
    },
}

#: system 字段的 canonical key 集合——只有这些才由后端用项目上下文覆盖、
#: 前端只读展示。其余（日期/天气/部位/检查项目等）虽在 schema 里被误标 system，
#: 仍按普通字段处理（可编辑、不自动填）。
PROJECT_SYSTEM_KEYS = {"project_name", "construction_unit", "supervision_org", "supervision_unit", "doc_number"}


def canonical_key(form_code: str, field_key: str) -> str:
    """schema 字段 key → canonical content key；无映射则原样返回。"""
    return CONTENT_MAP.get(form_code, {}).get(field_key, field_key)


def is_system_field(form_code: str, field: dict) -> bool:
    """纠正 schema 里被高估的 system 标记：仅当 canonical key 属项目派生集合。"""
    if not field.get("system"):
        return False
    return canonical_key(form_code, field["key"]) in PROJECT_SYSTEM_KEYS


def build_content_from_fields(form_code: str, fields: dict, project) -> dict:
    """通用录入页落库：schema-key 值 → canonical content 字典。

    system 字段（项目派生）由 project 上下文覆盖；其余用前端传入值。
    编号（doc_number）缺失时按表号+时间戳生成。
    """
    from app.services.record_fields import project_fields
    from datetime import datetime

    mp = CONTENT_MAP.get(form_code, {})
    content: dict = {}
    for k, v in (fields or {}).items():
        ck = mp.get(k, k)
        content[ck] = v
    # 项目派生 system 字段：服务端覆盖，不容客户端篡改
    pf = project_fields(project) if project else {}
    for ck in PROJECT_SYSTEM_KEYS:
        if ck in pf:
            content[ck] = pf[ck]
    # 编号兜底生成
    if not content.get("doc_number"):
        schema = SCHEMAS.get(form_code) or {}
        prefix = (schema.get("form_number") or form_code).replace(" ", "")
        content["doc_number"] = f"{prefix}-{datetime.now():%Y%m%d%H%M%S}"
    return content


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def get_schema(form_code: str) -> Optional[dict]:
    """返回表单字段级 schema；无字段层定义的表单返回 None。

    返回深拷贝并纠正 system 标记：仅 canonical key 属项目派生集合的字段才标记
    system（前端只读、后端自填），避免 schema 数据里被高估的 system 标记把
    天气/日期等用户字段也误设为只读。
    """
    schema = SCHEMAS.get(form_code)
    if schema is None:
        return None
    import copy
    schema = copy.deepcopy(schema)
    for sec in schema.get("sections", []):
        for f in sec.get("fields", []):
            f["system"] = is_system_field(form_code, f)
    return schema


def schema_catalog() -> list[dict]:
    """全量目录：registry 里 29 类文书都列出，标出哪些已有字段级 schema。"""
    items = []
    for spec in FORMS:
        schema = SCHEMAS.get(spec.form_code)
        items.append({
            "form_code": spec.form_code,
            "form_number": spec.form_number,
            "name": spec.name,
            "category": spec.category,
            "source": spec.source,
            "has_schema": schema is not None,
            "schema_version": schema["version"] if schema else None,
        })
    return items


def validate_values(form_code: str, values: dict) -> dict:
    """按 schema 校验表单值，返回 {ok, errors:[{key,label,message}]}。

    校验规则：必填非空 / select 取值合法 / number 可解析 /
    date=YYYY-MM-DD / time=HH:MM / maxlen 超长。
    """
    schema = SCHEMAS.get(form_code)
    if schema is None:
        return {"ok": False, "errors": [{"key": "", "label": "",
                "message": f"表单 {form_code} 尚无字段级 schema"}]}
    errors: list[dict] = []
    for section in schema["sections"]:
        for field in section["fields"]:
            key = field["key"]
            # 项目派生 system 字段由后端自填，跳过其必填/格式校验
            if is_system_field(form_code, field):
                continue
            raw = values.get(key)
            value = "" if raw is None else str(raw).strip()
            if field.get("required") and not value:
                errors.append({"key": key, "label": field["label"],
                               "message": "必填项不能为空"})
                continue
            if not value:
                continue
            ftype = field["type"]
            if ftype == "select" and field.get("options") and value not in field["options"]:
                errors.append({"key": key, "label": field["label"],
                               "message": "取值不在可选范围内"})
            elif ftype == "number":
                try:
                    float(value)
                except ValueError:
                    errors.append({"key": key, "label": field["label"],
                                   "message": "应为数字"})
            elif ftype == "date" and not _DATE_RE.match(value):
                errors.append({"key": key, "label": field["label"],
                               "message": "日期格式应为 YYYY-MM-DD"})
            elif ftype == "time" and not _TIME_RE.match(value):
                errors.append({"key": key, "label": field["label"],
                               "message": "时间格式应为 HH:MM"})
            elif ftype == "datetime":
                from datetime import datetime
                try:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo or not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$", value):
                        raise ValueError()
                except ValueError:
                    errors.append({"key": key, "label": field["label"],
                                   "message": "日期时间格式应为 YYYY-MM-DD HH:mm"})
            if field.get("maxlen") and len(value) > field["maxlen"]:
                errors.append({"key": key, "label": field["label"],
                               "message": f"长度超过 {field['maxlen']} 字"})
    return {"ok": not errors, "errors": errors}


def known_form_codes() -> set[str]:
    return set(FORM_BY_CODE.keys())
