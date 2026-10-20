"""危大工程巡视检查项清单（对齐房建样表 GDAQ30306）。

清单取自项目上真实使用的《危险性较大的分部分项工程监理巡视检查记录表（新）-房建》
样表，按危大工程类型分别固定：脚手架类 17 项、模板支撑类 15 项、装配式构件安装 10 项。

渲染时按记录里的「危大工程名称/类型」文本匹配到对应清单，逐行填入
「序号 / 检查内容 / 检查记录 / 检查结果」。
"""
from __future__ import annotations

# 落地式/悬挑式钢管脚手架（样表 17 项，两类共用同一套检查内容）
SCAFFOLD_ITEMS: list[str] = [
    "施工作业前是否已履行监理报审手续？",
    "施工单位专职安全生产管理人员是否到位？",
    "特种作业人员持证上岗情况是否符合要求？",
    "作业前是否对作业人员进行安全技术交底？",
    "作业过程中作业人员安全防护措施是否到位？",
    "脚手架构配件及材料质量是否满足安装要求？",
    "脚手架基础条件是否满足方案的搭设要求？",
    "脚手架构造措施是否符合方案及规范要求？",
    "结构强度是否满足悬挑脚手架安装要求？",
    "卸荷钢丝绳的设置是否满足规范及方案要求？",
    "作业区域是否设置警戒及专人监护？",
    "脚手架作业层消防措施是否到位？",
    "脚手架是否承受混凝土泵等外力荷载？",
    "施工过程中是否存在作业人员上下交叉作业？",
    "临近供电线路安全距离不足的是否采取保护措施？",
    "应急救援准备情况是否到位？",
    "其他：",
]

# 混凝土模板支撑工程（样表 15 项）
FORMWORK_ITEMS: list[str] = [
    "施工作业前是否已履行监理报审手续？",
    "施工单位专职安全生产管理人员是否到位：",
    "特种作业人员持证上岗情况是否符合要求？",
    "作业前是否对作业人员进行安全技术交底？",
    "作业过程中作业人员安全防护措施是否到位？",
    "使用的钢管、扣件等质量是否满足安装要求？",
    "模板支架基础条件是否满足方案的搭设要求？",
    "支架构造措施是否符合方案及规范要求？",
    "立杆垂直度偏差是否符合规范要求？",
    "上部荷载是否在方案计算荷载范围内？",
    "作业区域是否设置警戒及专人监护？",
    "混凝土浇筑时是否对架体采取监测措施？",
    "模板作业过程中消防措施是否到位？",
    "应急救援准备情况是否到位？",
    "其他：",
]

# 装配式建筑混凝土预制构件安装工程（样表 10 项）
PREFAB_ITEMS: list[str] = [
    "施工前是否已履行监理报审手续",
    "总承包单位专职安全生产管理人员到位情况",
    "专业承包单位专职安全生产管理人员到位情况",
    "安全技术交底情况",
    "特种作业人员到位情况是否与方案相符",
    "特种作业人员证书与人员是否相符",
    "应急救援准备情况",
    "作业警戒区的设立与警戒人员到位情况",
    "专项方案执行情况",
    "其它",
]

# 未识别类型时的通用检查项（保持与其他清单同源，覆盖报审/到岗/交底/防护/警戒/应急）
DEFAULT_ITEMS: list[str] = [
    "施工作业前是否已履行监理报审手续？",
    "施工单位专职安全生产管理人员是否到位？",
    "专项施工方案是否经审批并按方案实施？",
    "特种作业人员持证上岗情况是否符合要求？",
    "作业前是否对作业人员进行安全技术交底？",
    "作业过程中作业人员安全防护措施是否到位？",
    "作业区域是否设置警戒及专人监护？",
    "应急救援准备情况是否到位？",
    "其他：",
]

#: 类型关键词 → 清单（按顺序匹配，先命中先返回）
_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    (("装配式", "预制构件"), PREFAB_ITEMS),
    (("脚手架",), SCAFFOLD_ITEMS),
    (("模板支撑", "模板支架", "模板工程", "模板"), FORMWORK_ITEMS),
)


def items_for(hazard_type: str | None) -> list[str]:
    """按危大工程名称/类型返回检查项清单。"""
    text = (hazard_type or "").strip()
    for keywords, items in _RULES:
        if any(k in text for k in keywords):
            return list(items)
    return list(DEFAULT_ITEMS)


def checklist_payload(hazard_type: str | None,
                      results: dict[str, str] | None = None,
                      default_record: str = "符合要求",
                      default_result: str = "符合") -> list[dict[str, str]]:
    """生成逐项检查结果列表：[{item, record, result}]。

    results 可为「检查内容 → 检查结果」的覆盖表（如 {"其他：": "不符合"}）。
    """
    results = results or {}
    out: list[dict[str, str]] = []
    for item in items_for(hazard_type):
        result = str(results.get(item) or default_result).strip() or default_result
        record = str(results.get(item + "#记录") or "").strip()
        if not record:
            record = default_record if result == default_result else result
        out.append({"item": item, "record": record, "result": result})
    return out
