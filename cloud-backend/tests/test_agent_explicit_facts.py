"""用户一句话里的显式信息抽取（2026-11-27 真机回归）。

现场原话：「2026/11/27 16:41 日常巡视 内围排水沟」
之前只抽出日期，时间/类型/部位全丢，导致草稿反复追问「巡视开始时间」，
用户回「我不是已经说了吗」还被当成补充内容写进草稿。
"""
import asyncio

from app.services.agent_context import (
    extract_explicit_message_facts,
    infer_record_type_from_text,
)
from app.services.agent_tools import refill_missing_from_message


def _facts(message: str) -> dict:
    return {item["field"]: item["value"] for item in extract_explicit_message_facts(message)}


def test_date_time_section_extracted_from_one_line():
    facts = _facts("2026/11/27 16:41 日常巡视 内围排水沟")
    assert facts["record_date"] == "2026-11-27"
    assert facts["start_time"] == "16:41"
    assert facts["section"] == "内围排水沟"
    assert infer_record_type_from_text("2026/11/27 16:41 日常巡视 内围排水沟") == "巡视"


def test_explicit_range_still_wins_over_bare_clock():
    facts = _facts("今天 9:00 到 11:30 旁站三层梁板混凝土浇筑")
    assert facts["start_time"] == "09:00"
    assert facts["end_time"] == "11:30"
    assert infer_record_type_from_text("今天 9:00 到 11:30 旁站三层梁板混凝土浇筑") == "旁站"


def test_navigation_and_complaints_do_not_set_type_or_field():
    assert infer_record_type_from_text("打开旁站记录页面") == ""
    assert infer_record_type_from_text("我不是已经说了吗") == ""
    assert refill_missing_from_message(
        {"record_type": "巡视", "section": ""}, ["巡视的工程部位"], "我不是已经说了吗"
    ) is None


def test_section_skips_form_words():
    facts = _facts("巡视记录怎么填")
    assert "section" not in facts
