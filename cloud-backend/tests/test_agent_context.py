import importlib.util
import sys
from pathlib import Path

# 仓库搬迁后统一用相对路径推导后端根，不再依赖 pytest 的 rootdir 注入。
# agent_context 内部会 `from app.services.time_normalization import …`，
# 必须先让 app 包在 sys.path 中可导入，importlib 加载才不会断链。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODULE = Path(__file__).parents[1] / "app" / "services" / "agent_context.py"
spec = importlib.util.spec_from_file_location("agent_context", MODULE)
assert spec and spec.loader
agent_context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_context)


def test_user_facts_are_source_aware_and_merge_corrections():
    first = agent_context.extract_user_facts(
        {"record_date": "2026-10-18", "section": "3号楼二层"},
        message_ref="m1",
    )
    second = agent_context.extract_user_facts(
        {"section": "3号楼三层", "work_content": "钢筋绑扎"},
        message_ref="m2",
    )
    merged = agent_context.merge_user_facts(first, second)
    by_field = {item["field"]: item for item in merged}
    assert by_field["record_date"]["value"] == "2026-10-18"
    assert by_field["section"]["value"] == "3号楼三层"
    assert by_field["section"]["history"][0]["value"] == "3号楼二层"
    assert by_field["work_content"]["source_ref"] == "m2"


def test_context_summary_does_not_turn_missing_media_into_missing_user_fact():
    summary = agent_context.render_context_summary(
        {"section": "3号楼二层"},
        [{
            "field": "section",
            "value": "3号楼二层",
            "source": "user_message_extraction",
            "source_ref": "m1",
            "status": "user_stated_candidate",
        }],
        ["天气"],
    )
    assert "用户消息中已提取 section：3号楼二层" in summary
    # 🔴 2026-11-06 更新：10-27 收敛追问后「当前草稿尚有 N 项待用户补充…一次只问一栏」替
    # 代了旧文案「当前仍缺少：天气」；语义（揭示缺项、不覆盖已确认值）未变。
    assert "1 项待用户补充" in summary
    assert "不得覆盖用户已确认值" in summary


def test_context_messages_are_bounded_and_deduplicated():
    context = {}
    for value in ["第一段", "第二段", "第二段", "第三段", "第四段", "第五段", "第六段", "第七段", "第八段", "第九段"]:
        context = agent_context.append_context_message(context, value)
    assert [item["text"] for item in context["messages"]] == [
        "第二段", "第三段", "第四段", "第五段", "第六段", "第七段", "第八段", "第九段"
    ]


def test_same_value_keeps_all_sources_without_creating_correction():
    first = agent_context.extract_user_facts({"section": "A区"}, message_ref="m1")
    second = agent_context.extract_user_facts({"section": "A区"}, message_ref="m2")
    merged = agent_context.merge_user_facts(first, second)
    item = merged[0]
    assert item["value"] == "A区"
    assert not item.get("history")
    assert {source["source_ref"] for source in item["sources"]} == {"m1", "m2"}


def test_conversation_history_is_reference_only():
    rendered = agent_context.render_conversation_history([
        {"text": "在A区施工"},
        {"role": "assistant", "text": "已记下A区施工，还差开始时间。"},
        {"text": "后来补充上午"},
    ])
    assert rendered.startswith("以下 JSON")
    assert "强行合并" in rendered
    assert "在A区施工" in rendered
    assert "已记下A区施工" in rendered  # assistant 轮必须进引用块（指代消解）


def test_conversation_history_assistant_turn_is_truncated():
    long_reply = "已记下" + "很长的回执" * 100
    rendered = agent_context.render_conversation_history([
        {"role": "user", "text": "记一条巡视"},
        {"role": "assistant", "text": long_reply},
    ])
    import json as _json
    block = _json.loads(rendered.split("\n", 1)[1])
    assistant_text = next(item["text"] for item in block if item["role"] == "assistant")
    assert len(assistant_text) == 240  # 摘要上限，防长回执挤占上下文
    user_text = next(item["text"] for item in block if item["role"] == "user")
    assert user_text == "记一条巡视"  # 用户原话不截断


def test_explicit_spoken_time_range_is_preserved_when_model_omits_it():
    facts = agent_context.extract_explicit_message_facts(
        "今天 13 点到 15 点，我在一技楼7楼进行巡视",
        message_ref="m-time",
    )
    values = {item["field"]: item["value"] for item in facts}
    assert values["start_time"] == "13:00"
    assert values["end_time"] == "15:00"
    assert "record_date" in values


def test_explicit_relative_dates_and_labelled_single_times_are_preserved():
    facts = agent_context.extract_explicit_message_facts(
        "明天巡视开始时间上午9点，在西楼A区巡查",
        message_ref="m-relative",
    )
    values = {item["field"]: item["value"] for item in facts}
    assert values["record_date"]
    assert values["start_time"] == "09:00"
    assert values["location"] == "西楼A区"
