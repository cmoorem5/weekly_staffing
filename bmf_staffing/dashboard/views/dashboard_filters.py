"""Shared FY / date filter helpers for dashboard views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

from staffing_tool.fiscal_year import (
    fy_end_date,
    fy_label_year,
    fy_week1_for_label_year,
    fy_week1_sunday_containing,
    normalize_fy_anchor,
    pay_periods_for_fy,
)


def parse_date_param(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(value.strip())
    except Exception:
        return fallback


def parse_fy_week1_from_request(request, today: date) -> date:
    """
    Resolve FY week-1 Sunday from ``fy`` (display label year) or legacy ``fy_start``.
    """
    fy_raw = (request.GET.get("fy") or "").strip()
    if fy_raw.isdigit():
        w1 = fy_week1_for_label_year(int(fy_raw))
        if w1 is not None:
            return w1
    fy_start_raw = (request.GET.get("fy_start") or "").strip()
    if fy_start_raw:
        return normalize_fy_anchor(parse_date_param(fy_start_raw, today))
    return fy_week1_sunday_containing(today)


def last_closed_pay_period_end_for_fy(today: date, fy_week1: date) -> date:
    """Last pay period in the FY that ended fully before ``today``."""
    periods = pay_periods_for_fy(fy_week1)
    closed = [p for p in periods if p.end < today]
    return closed[-1].end if closed else fy_week1 - timedelta(days=1)


def fy_choice_rows(center_label: int) -> list[dict[str, object]]:
    """Dropdown rows: FY label, PP#1 Sunday, human-readable option text."""
    rows: list[dict[str, object]] = []
    for lab in range(center_label - 6, center_label + 3):
        w1 = fy_week1_for_label_year(lab)
        if w1 is None:
            continue
        rows.append(
            {
                "fy_label": lab,
                "pp1_iso": w1.isoformat(),
                "option_label": f"FY{lab} — PP#1 starts {w1:%b %d, %Y} (Sun)",
            }
        )
    return rows


def serialize_filters_query(
    fy_label: int,
    granularity: str,
    date_start: date,
    date_end: date,
) -> str:
    return urlencode(
        {
            "fy": str(fy_label),
            "granularity": granularity,
            "date_start": date_start.isoformat(),
            "date_end": date_end.isoformat(),
        }
    )


def serialize_filters_query_from_parts(parts: dict[str, str]) -> str:
    clean = {k: v for k, v in parts.items() if v is not None and str(v).strip() != ""}
    return urlencode(clean)


@dataclass(frozen=True)
class FyDateWindow:
    """FY selection plus the date window inside it, as resolved from a request."""

    fy_start: date
    fy_end: date
    fy_label: int
    is_current_fy: bool
    default_start: date
    default_end: date
    date_start: date
    date_end: date


def resolve_fy_date_window(request, today: date) -> FyDateWindow:
    """
    FY from ``fy``; dates from ``date_start``/``date_end``, clamped to that FY.

    The FY drives the window: forms post a hidden ``dates_fy`` naming the FY the
    date inputs were filled for, and when the user picks a different FY those
    stale dates are dropped in favour of the new FY's defaults (FY-to-date at
    the last closed pay period for the current FY, the full FY otherwise).
    Without that, switching FY clamped the old dates into a sliver of the new
    FY or silently fell back to defaults depending on how they overlapped.
    Links without ``dates_fy`` (presets, report-hub cards) are unaffected.
    """
    fy_start = parse_fy_week1_from_request(request, today)
    fy_end = fy_end_date(fy_start)
    fy_label = fy_label_year(fy_start)
    is_current_fy = fy_start == fy_week1_sunday_containing(today)
    default_end = (
        last_closed_pay_period_end_for_fy(today, fy_start) if is_current_fy else fy_end
    )
    default_start = fy_start

    dates_fy = (request.GET.get("dates_fy") or "").strip()
    if dates_fy and dates_fy != str(fy_label):
        date_start, date_end = default_start, default_end
    else:
        date_start = parse_date_param(request.GET.get("date_start", ""), default_start)
        date_end = parse_date_param(request.GET.get("date_end", ""), default_end)
        date_start = max(date_start, fy_start)
        date_end = min(date_end, fy_end)
        if date_start > date_end:
            date_start, date_end = default_start, default_end

    return FyDateWindow(
        fy_start=fy_start,
        fy_end=fy_end,
        fy_label=fy_label,
        is_current_fy=is_current_fy,
        default_start=default_start,
        default_end=default_end,
        date_start=date_start,
        date_end=date_end,
    )
