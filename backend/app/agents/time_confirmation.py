"""Detect incomplete user time expressions and propose an explicit range."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TimeConfirmation:
    reason: str
    start: str
    end: str
    prompt: str


@dataclass(frozen=True)
class ConfirmationReply:
    action: str
    start: str = ""
    end: str = ""


_RELATIVE_TIME = re.compile(
    r"(?:最近|近|过去)\s*\d{1,3}\s*(?:天|日|周|个月|月|年)|"
    r"今天|今日|昨天|昨日|本周|上周|本月|上月|今年|去年"
)
_EXPLICIT_YEAR = re.compile(r"(?:19|20)\d{2}\s*年|(?:19|20)\d{2}[-/.]\d{1,2}")
_MONTH = re.compile(r"(?<!\d)(1[0-2]|0?[1-9])\s*月")
_MONTH_RANGE = re.compile(r"(?<!\d)(1[0-2]|0?[1-9])\s*(?:到|至|[-~—])\s*(1[0-2]|0?[1-9])\s*月")
_YEAR_MONTH_RANGE = re.compile(
    r"((?:19|20)\d{2})\s*年\s*(1[0-2]|0?[1-9])\s*(?:到|至|[-~—])\s*(1[0-2]|0?[1-9])\s*月"
)
_YEAR_MONTH = re.compile(r"((?:19|20)\d{2})\s*年\s*(1[0-2]|0?[1-9])\s*月")
_ISO_RANGE = re.compile(r"((?:19|20)\d{2}-\d{2}-\d{2})\s*(?:到|至|~|—)\s*((?:19|20)\d{2}-\d{2}-\d{2})")
_AFFIRMATIVE = frozenset({"是", "是的", "确定", "确认", "可以", "好", "好的", "对", "没问题", "按这个", "就这样"})
_NEGATIVE = frozenset({"不", "不是", "不对", "否", "取消", "不要", "不确定", "先不查"})


def _month_range(year: int, start_month: int, end_month: int) -> tuple[str, str]:
    return (
        f"{year:04d}-{start_month:02d}-01",
        f"{year:04d}-{end_month:02d}-{calendar.monthrange(year, end_month)[1]:02d}",
    )


def detect_time_confirmation(question: str, *, today: date | None = None) -> TimeConfirmation | None:
    q = (question or "").strip()
    current = today or date.today()
    if _EXPLICIT_YEAR.search(q) or _RELATIVE_TIME.search(q):
        return None

    range_match = _MONTH_RANGE.search(q)
    if range_match:
        start_month, end_month = int(range_match.group(1)), int(range_match.group(2))
        start, end = _month_range(current.year, start_month, end_month)
        prompt = (
            f"你没有指定年份，是否按今年处理，即查询"
            f"{current.year}年{start_month}月1日至"
            f"{current.year}年{end_month}月{calendar.monthrange(current.year, end_month)[1]}日？"
        )
        return TimeConfirmation("missing_year", start, end, prompt)

    month_match = _MONTH.search(q)
    if month_match:
        month = int(month_match.group(1))
        start, end = _month_range(current.year, month, month)
        return TimeConfirmation(
            "missing_year", start, end,
            f"你没有指定年份，是否按今年处理，即查询{current.year}年{month}月？",
        )

    iso = current.isoformat()
    return TimeConfirmation("missing_time", iso, iso, "你没有指定时间，是否查询今天的数据？")


def classify_confirmation_reply(reply: str, *, today: date | None = None) -> ConfirmationReply:
    """Classify only an answer to an existing pending confirmation."""
    text = re.sub(r"[\s，。！？!?]", "", reply or "")
    current = today or date.today()
    if text in _AFFIRMATIVE:
        return ConfirmationReply("confirm")
    if text in _NEGATIVE:
        return ConfirmationReply("reject")

    iso_match = _ISO_RANGE.search(text)
    if iso_match:
        return ConfirmationReply("replace", iso_match.group(1), iso_match.group(2))

    range_match = _YEAR_MONTH_RANGE.search(text)
    if range_match:
        year, start_month, end_month = map(int, range_match.groups())
        start, end = _month_range(year, start_month, end_month)
        return ConfirmationReply("replace", start, end)

    month_match = _YEAR_MONTH.search(text)
    if month_match:
        year, month = map(int, month_match.groups())
        start, end = _month_range(year, month, month)
        return ConfirmationReply("replace", start, end)

    relative = re.search(r"(?:最近|近|过去)(\d{1,3})(?:天|日)", text)
    if relative:
        from datetime import timedelta

        days = int(relative.group(1))
        if 1 <= days <= 366:
            return ConfirmationReply(
                "replace",
                (current - timedelta(days=days - 1)).isoformat(),
                current.isoformat(),
            )
    return ConfirmationReply("new_query")
