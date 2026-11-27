"""用户一句话里的信息抽取与字段映射（2026-11-27 真机回归）。

原则：**不写死某一句话的解析**。
- 类型识别读 ``agent_request_plan.RECORD_TYPES`` 的名称与 aliases；
- 日期/时间由通用时间解析处理；
- 「落到哪个参数」由 ``record_field_spec`` 的字段表决定（start_time →
  inspection_start_time / station_start_time），不在这里堆正则。
"""
from app.services.agent_context import (
    extract_explicit_message_facts,
    infer_record_type_from_text,
)
from app.services.agent_request_plan import field_contract_text
from app.services.agent_tools import refill_missing_from_message


def _facts(message: str) -> dict:
    items = extract_explicit_message_facts(message, message_ref=message)
    return {item["field"]: item["value"] for item in items}


def test_date_and_time_are_extracted_generically():
    facts = _facts("2026/11/27 16:41 日常巡视 内围排水沟")
    assert facts["record_date"] == "2026-11-27"
    assert facts["start_time"] == "16:41"


def test_facts_use_canonical_names_shared_by_record_types():
    """巡视/旁站的时间 canonical 都是 start_time，差异在落库参数（由表单层处理）。"""
    facts = _facts("2026/11/27 16:41 日常巡视 内围排水沟")
    assert facts["start_time"] == "16:41"
    assert "inspection_start_time" not in facts
    assert "station_start_time" not in facts


def test_record_type_comes_from_registry_not_hardcoded_words():
    assert infer_record_type_from_text("2026/11/27 16:41 日常巡视 内围排水沟") == "巡视"
    assert infer_record_type_from_text("今天上午在三层梁板旁站混凝土浇筑") == "旁站"
    assert infer_record_type_from_text("打开旁站记录页面") == ""
    assert infer_record_type_from_text("我不是已经说了吗") == ""


def test_explicit_range_still_wins_over_bare_clock():
    facts = _facts("今天 9:00 到 11:30 旁站三层梁板混凝土浇筑")
    assert facts["start_time"] == "09:00"
    assert facts["end_time"] == "11:30"


def test_field_contract_is_generated_from_form_specs():
    contract = field_contract_text()
    assert "巡视: " in contract and "start_time=巡视开始时间" in contract
    assert "落库参数 inspection_start_time" in contract
    assert "旁站: " in contract and "落库参数 station_start_time" in contract


def test_complaints_are_not_treated_as_field_values():
    assert refill_missing_from_message(
        {"record_type": "巡视", "section": ""}, ["巡视的工程部位"], "我不是已经说了吗"
    ) is None
