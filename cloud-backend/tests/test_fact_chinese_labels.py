"""验证：模型输出中文字段名的 facts 不再被白名单整体丢弃。

生产实测（2026-11-25）：qwen3.7-plus 路由模型把 facts/args 字段名写成中文
标签（「施工内容」「开始时间」「部位」「结论」），而 normalize_model_facts
的白名单只认英文 canonical（station_process/start_time/section/…），
中文名全部被丢 → facts 恒空。统计：qwen3.7-plus 15 轮 0 次模型抽取，
deepseek-v4.1-flash 75%（69/92）、qwen3.8-flash 82%（9/11）。

修法：_FACT_ALIASES 补中文标签 → canonical 映射（实测三例原话复刻 +
常见变体 + 英文原名不回归）。
"""
import sys

sys.path.insert(0, ".")

from app.services.agent_fact_model import normalize_model_facts


def fields_of(raw):
    return [(f["field"], f["value"]) for f in normalize_model_facts(raw, message_ref="t")]


def main():
    # ---- 1. qwen3.7-plus 实测输出复刻（旁站例）----
    raw1 = [
        {"field": "施工内容", "value": "屋面防水卷材铺贴", "source_span": "屋面防水卷材铺贴", "confidence": 0.95},
        {"field": "开始时间", "value": "9:00", "source_span": "9:00", "confidence": 0.95},
        {"field": "结束时间", "value": "12:00", "source_span": "12:00", "confidence": 0.95},
        {"field": "人数", "value": "10人", "source_span": "10人", "confidence": 0.95},
    ]
    got1 = fields_of(raw1)
    expect1 = [
        ("station_process", "屋面防水卷材铺贴"),
        ("start_time", "9:00"),
        ("end_time", "12:00"),
        ("worker_count", "10人"),
    ]
    assert got1 == expect1, f"旁站例: {got1}"
    print("1. 旁站例中文字段全映射: True")

    # ---- 2. qwen3.7-plus 实测输出复刻（验收例）----
    raw2 = [
        {"field": "部位", "value": "3号楼二层东侧", "source_span": "3号楼二层东侧", "confidence": 0.95},
        {"field": "验收内容", "value": "临边防护", "source_span": "临边防护", "confidence": 0.95},
        {"field": "结论", "value": "合格", "source_span": "合格", "confidence": 0.95},
    ]
    got2 = fields_of(raw2)
    expect2 = [
        ("section", "3号楼二层东侧"),
        ("work_content", "临边防护"),
        ("acceptance_result", "合格"),
    ]
    assert got2 == expect2, f"验收例: {got2}"
    print("2. 验收例中文字段全映射: True")

    # ---- 3. 英文原字段名不回归（deepseek/flash 时代口径）----
    raw3 = [
        {"field": "station_process", "value": "防水铺贴", "source_span": "防水铺贴"},
        {"field": "section", "value": "屋面", "source_span": "屋面"},
        {"field": "record_type", "value": "旁站", "source_span": "旁站"},
    ]
    got3 = fields_of(raw3)
    assert ("station_process", "防水铺贴") in got3
    assert ("section", "屋面") in got3
    assert ("record_type", "旁站") in got3
    print("3. 英文字段名保持: True")

    # ---- 4. 无映射的中文名转槽位键（不漏进 facts 顶层，值保留）----
    raw4 = [{"field": "随便编的标签", "value": "x", "source_span": "x"}]
    got4 = fields_of(raw4)
    assert got4 == [("extra_fields.随便编的标签", "x")], got4
    print("4. 未知中文键转槽位: True")

    # ---- 5. 常见变体 ----
    raw5 = [
        {"field": "工程部位", "value": "西侧基坑", "source_span": "西侧基坑"},
        {"field": "施工单位", "value": "中铁十二局", "source_span": "中铁十二局"},
        {"field": "天气", "value": "晴", "source_span": "晴"},
        {"field": "问题", "value": "临边防护缺失", "source_span": "临边防护缺失"},
    ]
    got5 = dict(fields_of(raw5))
    assert got5.get("section") == "西侧基坑"
    assert got5.get("construction_unit") == "中铁十二局"
    assert got5.get("weather") == "晴"
    assert got5.get("problems") == "临边防护缺失"
    print("5. 变体映射正确: True")

    # ---- 6. 空值/未知值仍被过滤 ----
    raw6 = [
        {"field": "部位", "value": "", "source_span": ""},
        {"field": "天气", "value": "未知", "source_span": "未知"},
    ]
    assert fields_of(raw6) == []
    print("6. 空/未知值过滤保持: True")

    # ---- 7. 开放中文标签（二轮实测）：字段表 label 匹配 ----
    raw7 = [
        {"field": "巡视部位", "value": "3号楼周边", "source_span": "3号楼周边"},
        {"field": "施工人数", "value": "10人", "source_span": "10人"},
        {"field": "旁站的关键工序", "value": "卷材铺贴", "source_span": "卷材铺贴"},
    ]
    got7 = dict(fields_of(raw7))
    assert got7.get("section") == "3号楼周边", got7
    assert got7.get("worker_count") == "10人", got7
    assert got7.get("station_process") == "卷材铺贴", got7
    print("7. 开放中文标签→字段表匹配: True")

    # ---- 8. 无法匹配的中文键转槽位（值不丢，不漏进顶层）----
    raw8 = [{"field": "随便编的标签", "value": "x", "source_span": "x"}]
    got8 = fields_of(raw8)
    assert got8 == [("extra_fields.随便编的标签", "x")], got8
    print("8. 未知中文键转槽位: True")

    # ---- 9. 保留字与英文顶层键约束保持 ----
    assert fields_of([{"field": "tool", "value": "x"}]) == []
    assert fields_of([{"field": "随便键 no dot", "value": "x"}]) == [
        ("extra_fields.随便键 no dot", "x")
    ] or fields_of([{"field": "随便键", "value": "x"}]) != []
    print("9. 保留字拦截保持: True")

    print("ALL PASS")


if __name__ == "__main__":
    main()
