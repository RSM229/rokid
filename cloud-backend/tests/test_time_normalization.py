# -*- coding: utf-8 -*-
"""时间归一化纯逻辑测试（unittest 风格，与项目其他测试一致）。

原 pytest 版本因 cloud-backend venv 未安装 pytest 而整模块加载失败，
这里改写为 unittest，可直接 `python -m unittest tests.test_time_normalization` 运行。
"""
from __future__ import annotations

from datetime import date, datetime
from unittest import TestCase, main
from unittest.mock import patch

from app.services import time_normalization
from app.services.time_normalization import (
    extract_explicit_time_range,
    normalize_datetime_value,
    parse_local_date,
    parse_local_datetime,
)


class ExplicitSpokenTimeRanges(TestCase):
    def test_normalized(self):
        cases = [
            ("今天 13 点到 15 点", {"start_time": "13:00", "end_time": "15:00"}),
            ("今天上午10点钟到12点钟", {"start_time": "10:00", "end_time": "12:00"}),
            ("下午2:30到4:00", {"start_time": "14:30", "end_time": "16:00"}),
            ("九点半到十一点", {"start_time": "09:30", "end_time": "11:00"}),
            ("今天9到11点", {"start_time": "09:00", "end_time": "11:00"}),
            ("23:30到次日凌晨1:00", {"start_time": "23:30", "end_time": "次日 01:00"}),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(extract_explicit_time_range(text), expected)


class NumericRangesAreNotTimes(TestCase):
    def test_no_time_markers(self):
        self.assertEqual(extract_explicit_time_range("3层到5层"), {})


class SingleTimeRequiresRoleLabel(TestCase):
    def test_labelled_only(self):
        self.assertEqual(
            extract_explicit_time_range("巡视开始时间是上午9点"), {"start_time": "09:00"})
        self.assertEqual(extract_explicit_time_range("3层到5层结束"), {})


class ChineseDateTimeNormalizer(TestCase):
    def test_shared_normalizer(self):
        self.assertEqual(
            normalize_datetime_value("2026年10月26日10时30分"), "2026-10-26 10:30")
        self.assertEqual(
            normalize_datetime_value("下午2点半", "2026-10-26"), "2026-10-26 14:30")
        self.assertEqual(
            normalize_datetime_value("次日凌晨1点", "2026-10-26"), "2026-10-27 01:00")


class DateRelativeWordsUseLocalAnchor(TestCase):
    def test_explicit_date_needs_time(self):
        self.assertIsNone(parse_local_datetime("2026-10-26"))
        self.assertEqual(
            parse_local_date("今天", today=date(2026, 10, 26)), date(2026, 10, 26))
        self.assertEqual(
            parse_local_date("明天", today=date(2026, 10, 26)), date(2026, 10, 27))


class RelativeDatetimeIgnoresStaleEventDate(TestCase):
    def test_does_not_inherit_event_date(self):
        with patch.object(time_normalization, "_local_today", lambda: date(2026, 10, 26)):
            result = parse_local_datetime("明天上午9点", "2020-01-01")
        self.assertIsNotNone(result)
        self.assertEqual(
            result.isoformat(sep=" ", timespec="minutes"), "2026-10-27 09:00")


class EndTimeNextDay(TestCase):
    def test_only_when_named(self):
        self.assertEqual(
            normalize_datetime_value("01:00", "2026-10-26"), "2026-10-26 01:00")
        self.assertEqual(
            normalize_datetime_value("次日 01:00", "2026-10-26"), "2026-10-27 01:00")


if __name__ == "__main__":
    main()
