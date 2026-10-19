"""工序类检查项清单（旁站记录 GD-B-212 等），对齐房建真实样表。

样表里「监理情况」是一张「序号 / 检查内容 / 检查记录 / 检查结果」的检查表，
内容随旁站的关键工序不同而变化；未识别工序时回落到通用检查项。
"""
from __future__ import annotations

# 混凝土浇筑（样表 6 项）
CONCRETE_ITEMS: list[str] = [
    "混凝土施工前检查模板、钢筋、预埋件，是否符合设计要求",
    "混凝土出场合格证，混凝土无离折、泌水情况",
    "混凝土供料、浇筑时间是否连续。",
    "混凝土振捣是否均匀、充分、密实。",
    "模板支架有无松动。",
    "浇筑过程中钢筋有无移位现象。",
]

# 通用旁站检查项（工序未识别时）
COMMON_ITEMS: list[str] = [
    "施工方案是否已审批，作业前是否进行安全技术交底",
    "特种作业人员是否持证上岗",
    "进场材料、构配件是否报验合格",
    "施工机具设备是否完好、运行正常",
    "施工过程是否按批准的方案及规范执行",
    "现场安全防护措施是否到位",
]

_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    (("混凝土", "砼", "浇筑", "墙柱", "梁板", "底板", "楼板"), CONCRETE_ITEMS),
)


def pangzhan_items_for(process: str | None) -> list[str]:
    """按旁站关键工序返回检查项清单。"""
    text = (process or "").strip()
    for keywords, items in _RULES:
        if any(k in text for k in keywords):
            return list(items)
    return list(COMMON_ITEMS)


def pangzhan_checklist(process: str | None,
                       results: dict[str, str] | None = None,
                       default_record: str = "符合要求",
                       default_result: str = "符合") -> list[dict[str, str]]:
    """生成旁站监理情况的逐项检查结果：[{item, record, result}]。"""
    results = results or {}
    out: list[dict[str, str]] = []
    for item in pangzhan_items_for(process):
        result = str(results.get(item) or default_result).strip() or default_result
        record = str(results.get(item + "#记录") or "").strip()
        if not record:
            record = default_record if result == default_result else result
        out.append({"item": item, "record": record, "result": result})
    return out
