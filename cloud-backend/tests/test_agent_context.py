import importlib.util
from pathlib import Path


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
    assert "当前仍缺少：天气" in summary
    assert "不得覆盖用户已确认值" in summary
