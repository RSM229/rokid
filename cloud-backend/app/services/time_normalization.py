"""Normalize user-entered Chinese and ISO date/time values for local records."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Any


_PERIODS = r"凌晨|清晨|早晨|早上|上午|中午|下午|傍晚|晚上|夜间"
_CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_HOUR_TOKEN = r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})"
_TIME_MARKER = re.compile(
    rf"(?<!\d)(?:{_HOUR_TOKEN}\s*[:：]\s*\d{{1,2}}|{_HOUR_TOKEN}\s*(?:点|时))",
)


def _time_token_pattern(prefix: str = "") -> str:
    return (
        rf"(?P<{prefix}period>{_PERIODS})?\s*"
        rf"(?P<{prefix}hour>\d{{1,2}}|[零〇一二两三四五六七八九十]{{1,3}})"
        rf"(?:\s*(?:(?::|：)(?P<{prefix}colon_minute>\d{{1,2}})|"
        rf"点\s*(?:(?P<{prefix}point_minute>\d{{1,2}}|[零〇一二两三四五六七八九十]{{1,3}})\s*分?|"
        rf"(?P<{prefix}point_half>半))?\s*钟?|"
        rf"时\s*(?P<{prefix}clock_minute>\d{{1,2}}|[零〇一二两三四五六七八九十]{{1,3}})?\s*分?钟?))?"
    )


_TIME_TOKEN = re.compile(_time_token_pattern())
_DATE_PREFIX = re.compile(r"^(?P<relative>次日|翌日|第二天)\s*")
_CHINESE_DATE = re.compile(
    r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*"
    r"(?P<day>\d{1,2})\s*日?"
)


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if "十" not in value:
        if len(value) == 1:
            return _CN_DIGITS[value]
        return int("".join(str(_CN_DIGITS[char]) for char in value))
    tens, ones = value.split("十", 1)
    return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)


def _local_today() -> date:
    return datetime.now(_CN_TZ).date()


def _period_hour(hour: int, period: str | None) -> int | None:
    if not 0 <= hour <= 24:
        return None
    period = period or ""
    if period in {"凌晨", "清晨"}:
        if hour == 12:
            return 0
        return hour if hour <= 11 else None
    if period in {"早晨", "早上", "上午"}:
        return hour if 0 <= hour <= 12 else None
    if period == "中午":
        if hour == 12:
            return 12
        return hour + 12 if 1 <= hour <= 11 else None
    if period in {"下午", "傍晚", "晚上", "夜间"}:
        if hour == 12:
            return 12
        return hour + 12 if 1 <= hour <= 11 else None
    return hour if hour <= 23 else None


def _clock(
    match: re.Match[str], inherited_period: str | None = None, prefix: str = "",
) -> tuple[time, str | None] | None:
    period = match.group(f"{prefix}period") or inherited_period
    hour = _period_hour(_number(match.group(f"{prefix}hour")), period)
    minute_text = (
        match.group(f"{prefix}colon_minute")
        or match.group(f"{prefix}point_minute")
        or match.group(f"{prefix}clock_minute")
        or "0"
    )
    minute = 30 if match.group(f"{prefix}point_half") else _number(minute_text)
    if hour is None or not 0 <= minute <= 59:
        return None
    return time(hour, minute), match.group(f"{prefix}period")


def parse_local_date(value: Any, *, today: date | None = None) -> date | None:
    """Parse ISO/Chinese dates and explicit relative day labels."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    anchor = today or _local_today()
    if text.startswith(("今天", "今日")):
        return anchor
    if text.startswith(("昨天", "昨日")):
        return anchor - timedelta(days=1)
    if text.startswith(("明天", "明日")):
        return anchor + timedelta(days=1)
    chinese = _CHINESE_DATE.search(text)
    if chinese:
        try:
            return date(int(chinese.group("year")), int(chinese.group("month")), int(chinese.group("day")))
        except ValueError:
            return None
    normalized = text.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.strip().replace(".", "-")
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def parse_local_datetime(value: Any, event_date: Any = None) -> datetime | None:
    """Parse a local datetime, combining time-only values with the event date."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value or "").strip()
    if not text:
        return None
    if not _TIME_MARKER.search(text):
        return None

    relative_match = _DATE_PREFIX.match(text)
    relative_next_day = bool(relative_match)
    if relative_match:
        text = text[relative_match.end():]

    try:
        parsed = datetime.fromisoformat(text.replace("T", " ").replace("Z", "+00:00"))
        if parsed.tzinfo:
            return None
        return parsed
    except ValueError:
        pass

    base_date = parse_local_date(event_date)
    chinese_date = _CHINESE_DATE.search(text)
    if chinese_date:
        try:
            base_date = date(
                int(chinese_date.group("year")),
                int(chinese_date.group("month")),
                int(chinese_date.group("day")),
            )
        except ValueError:
            return None
        text = text[:chinese_date.start()] + text[chinese_date.end():]
    else:
        for label, offset in (("今天", 0), ("今日", 0), ("昨天", -1), ("昨日", -1), ("明天", 1), ("明日", 1)):
            if label in text:
                base_date = _local_today() + timedelta(days=offset)
                text = text.replace(label, "", 1)
                break

    match = _TIME_TOKEN.search(text)
    if not match:
        return None
    parsed_clock = _clock(match)
    if parsed_clock is None:
        return None
    clock, _ = parsed_clock
    if base_date is None:
        return None
    if relative_next_day:
        base_date += timedelta(days=1)
    return datetime.combine(base_date, clock)


def normalize_datetime_value(value: Any, event_date: Any = None) -> str | None:
    """Return the canonical local ``YYYY-MM-DD HH:MM`` form value."""
    parsed = parse_local_datetime(value, event_date)
    return parsed.isoformat(sep=" ", timespec="minutes") if parsed else None


def extract_explicit_time_range(text: str, event_date: date | None = None) -> dict[str, str]:
    """Extract explicit time ranges and explicitly-labelled single endpoints."""
    if not text:
        return {}
    range_pattern = re.compile(
        rf"(?<!\d)(?P<start>{_time_token_pattern('start_')})\s*"
        rf"(?:到|至|~|～|—|-)\s*(?P<next_day>次日|翌日|第二天)?\s*"
        rf"(?P<end>{_time_token_pattern('end_')})(?!\d)"
    )
    for match in range_pattern.finditer(text):
        start_raw = match.group("start")
        end_raw = match.group("end")
        # Do not interpret ordinary numeric ranges such as楼层 or轴线 as times.
        if not re.search(r"点|时|[:：]|" + _PERIODS, start_raw + end_raw):
            continue
        start_result = _clock(match, prefix="start_")
        end_result = _clock(
            match, start_result[1] if start_result else None, prefix="end_",
        )
        if not start_result or not end_result:
            continue
        start_clock, _ = start_result
        end_clock, _ = end_result
        explicit_rollover = bool(match.group("next_day"))
        explicit_rollover = explicit_rollover or (
            match.group("end_period") == "凌晨" and start_clock.hour >= 18
        )
        start_value = start_clock.strftime("%H:%M")
        end_value = end_clock.strftime("%H:%M")
        if explicit_rollover:
            end_value = f"次日 {end_value}"
        return {"start_time": start_value, "end_time": end_value}

    start_pattern = re.compile(
        rf"(?:开始时间|起始时间|開始時間|开始于|起始于|从|于)\s*(?:是|为|[:：])?\s*"
        rf"{_time_token_pattern('single_start_')}"
    )
    end_pattern = re.compile(
        rf"(?:结束时间|终止时间|结束于|截至)\s*(?:是|为|[:：])?\s*"
        rf"{_time_token_pattern('single_end_')}"
    )
    for pattern, prefix, field in (
        (start_pattern, "single_start_", "start_time"),
        (end_pattern, "single_end_", "end_time"),
    ):
        match = pattern.search(text)
        if not match:
            continue
        parsed = _clock(match, prefix=prefix)
        if parsed is not None:
            return {field: parsed[0].strftime("%H:%M")}
    return {}
