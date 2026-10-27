from datetime import date

import pytest

from app.services import time_normalization
from app.services.time_normalization import (
    extract_explicit_time_range,
    normalize_datetime_value,
    parse_local_date,
    parse_local_datetime,
)


@pytest.mark.parametrize(("text", "expected"), [
    ("今天 13 点到 15 点", {"start_time": "13:00", "end_time": "15:00"}),
    ("今天上午10点钟到12点钟", {"start_time": "10:00", "end_time": "12:00"}),
    ("下午2:30到4:00", {"start_time": "14:30", "end_time": "16:00"}),
    ("九点半到十一点", {"start_time": "09:30", "end_time": "11:00"}),
    ("今天9到11点", {"start_time": "09:00", "end_time": "11:00"}),
    ("23:30到次日凌晨1:00", {"start_time": "23:30", "end_time": "次日 01:00"}),
])
def test_explicit_spoken_time_ranges_are_normalized(text, expected):
    assert extract_explicit_time_range(text) == expected


def test_numeric_ranges_without_time_markers_are_not_treated_as_times():
    assert extract_explicit_time_range("3层到5层") == {}


def test_single_time_is_extracted_only_when_user_labels_its_role():
    assert extract_explicit_time_range("巡视开始时间是上午9点") == {"start_time": "09:00"}
    assert extract_explicit_time_range("3层到5层结束") == {}


def test_chinese_datetime_and_time_only_values_share_one_normalizer():
    assert normalize_datetime_value("2026年10月26日10时30分") == "2026-10-26 10:30"
    assert normalize_datetime_value("下午2点半", "2026-10-26") == "2026-10-26 14:30"
    assert normalize_datetime_value("次日凌晨1点", "2026-10-26") == "2026-10-27 01:00"


def test_datetime_requires_an_explicit_time_and_date_relative_words_use_local_anchor():
    assert parse_local_datetime("2026-10-26") is None
    assert parse_local_date("今天", today=date(2026, 10, 26)) == date(2026, 10, 26)
    assert parse_local_date("明天", today=date(2026, 10, 26)) == date(2026, 10, 27)


def test_explicit_relative_datetime_does_not_inherit_a_stale_event_date(monkeypatch):
    monkeypatch.setattr(time_normalization, "_local_today", lambda: date(2026, 10, 26))
    result = parse_local_datetime("明天上午9点", "2020-01-01")
    assert result is not None
    assert result.isoformat(sep=" ", timespec="minutes") == "2026-10-27 09:00"


def test_end_time_is_only_next_day_when_user_says_so_or_names_dawn():
    assert normalize_datetime_value("01:00", "2026-10-26") == "2026-10-26 01:00"
    assert normalize_datetime_value("次日 01:00", "2026-10-26") == "2026-10-27 01:00"
