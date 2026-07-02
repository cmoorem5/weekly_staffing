"""Shared view helpers: date parsing and month calendar building."""

from __future__ import annotations

import calendar
import datetime as dt

from django.http import Http404
from django.utils import timezone


def parse_date_or_404(date_str: str) -> dt.date:
    try:
        return dt.date.fromisoformat(date_str)
    except ValueError as exc:
        raise Http404(f"Invalid date: {date_str}") from exc


def local_today() -> dt.date:
    return timezone.localdate()


def parse_month(request) -> tuple[int, int]:
    """(year, month) from ?month=YYYY-MM, defaulting to the current month."""
    raw = request.GET.get("month", "")
    try:
        year_s, month_s = raw.split("-")
        year, month = int(year_s), int(month_s)
        if 1 <= month <= 12 and 2000 <= year <= 2100:
            return year, month
    except ValueError:
        pass
    today = local_today()
    return today.year, today.month


def month_weeks(year: int, month: int) -> list[list[dt.date | None]]:
    """Weeks (Sun→Sat, matching the dashboard convention) for a month grid.

    Cells outside the month are None.
    """
    cal = calendar.Calendar(firstweekday=calendar.SUNDAY)
    weeks: list[list[dt.date | None]] = []
    for week in cal.monthdatescalendar(year, month):
        weeks.append([day if day.month == month else None for day in week])
    return weeks


def month_nav(year: int, month: int) -> dict:
    first = dt.date(year, month, 1)
    prev_month = (first - dt.timedelta(days=1)).replace(day=1)
    next_month = (first + dt.timedelta(days=32)).replace(day=1)
    return {
        "current": first,
        "prev": f"{prev_month.year:04d}-{prev_month.month:02d}",
        "next": f"{next_month.year:04d}-{next_month.month:02d}",
    }
