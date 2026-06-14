"""
Fiscal year (FY) and pay-period boundaries for staffing dashboards and tools.

FY week 1 (first week of the new FY) is anchored around **September 28**:
``FY_week1_sunday`` is the **Sunday on or before September 28** (inclusive) for
the calendar year of that anchor. Examples:
  * Sept 28, 2025 is a Sunday → FY week 1 starts **2025-09-28**.
  * Sept 28, 2026 is a Monday → FY week 1 starts **2026-09-27** (the prior Sunday).

The FY that contains a calendar date ``d`` starts at that FY's ``FY_week1_sunday``
and ends **inclusive** on the day **before** the next FY's ``FY_week1_sunday``.
So the FY spans 52 or 53 weeks depending on how the Sundays fall.

**Pay periods** repeat every **14 days**, each starting on a **Sunday**, aligned
to ``FY_week1_sunday`` (period *i* is ``FY_week1_sunday + 14*(i-1)`` for 14 days
inclusive, clipped at FY end).

**Fiscal quarters** (not Oct–Dec calendar quarters): **Q1** Sep–Nov, **Q2** Dec–Feb,
**Q3** Mar–May, **Q4** Jun through the end of the FY (so closing partial September
rolls into Q4 with Jun–Aug).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import NamedTuple

__all__ = [
    "PayPeriod",
    "fy_end_date",
    "fy_label_year",
    "fy_week1_for_label_year",
    "fy_week1_sunday_containing",
    "normalize_fy_anchor",
    "next_fy_week1_sunday",
    "pay_period_count_for_fy",
    "pay_period_index_overlapping",
    "pay_periods_for_fy",
    "sunday_on_or_before_sept_28",
    "fiscal_quarter_label",
    "fiscal_quarter_windows_for_fy",
]


class PayPeriod(NamedTuple):
    period_index: int
    start: date
    end: date  # inclusive


def sunday_on_or_before_sept_28(year: int) -> date:
    """Sunday on or before Sept 28 for the given **calendar** ``year``."""
    sept28 = date(year, 9, 28)
    # Monday=0 .. Sunday=6 → days to subtract to reach Sunday on or before.
    off = (sept28.weekday() + 1) % 7
    return sept28 - timedelta(days=off)


def fy_week1_sunday_containing(d: date) -> date:
    """
    ``FY_week1_sunday`` for the fiscal year that contains ``d`` (inclusive of FY
    start/end boundaries).
    """
    s_this = sunday_on_or_before_sept_28(d.year)
    if d >= s_this:
        return s_this
    return sunday_on_or_before_sept_28(d.year - 1)


def next_fy_week1_sunday(fy_week1_sunday: date) -> date:
    """The first Sunday of the fiscal year immediately following ``fy_week1_sunday``."""
    return sunday_on_or_before_sept_28(fy_week1_sunday.year + 1)


def fy_end_date(fy_week1_sunday: date) -> date:
    """Last calendar day of the FY that begins ``fy_week1_sunday``."""
    return next_fy_week1_sunday(fy_week1_sunday) - timedelta(days=1)


def fy_label_year(fy_week1_sunday: date) -> int:
    """Display year for FY labels (matches prior Oct-FY convention: year of FY end)."""
    return fy_end_date(fy_week1_sunday).year


def fy_week1_for_label_year(label_year: int) -> date | None:
    """
    Week-1 Sunday (PP#1 start) for the FY labeled ``FY{label_year}`` (calendar year of FY end).

    Inverse of ``fy_label_year`` within a small search window around ``label_year``.
    """
    for ty in range(label_year - 2, label_year + 2):
        s = sunday_on_or_before_sept_28(ty)
        if fy_label_year(s) == label_year:
            return s
    return None


def normalize_fy_anchor(d: date) -> date:
    """Canonical FY start Sunday for the FY containing ``d`` (for query params)."""
    return fy_week1_sunday_containing(d)


def pay_periods_for_fy(fy_week1_sunday: date) -> list[PayPeriod]:
    """
    Biweekly pay periods: each starts Sunday, 14 days inclusive, until FY end.
    """
    fy_end = fy_end_date(fy_week1_sunday)
    periods: list[PayPeriod] = []
    idx = 1
    cursor = fy_week1_sunday
    while cursor <= fy_end:
        end = min(cursor + timedelta(days=13), fy_end)
        periods.append(PayPeriod(idx, cursor, end))
        cursor = end + timedelta(days=1)
        idx += 1
    return periods


def pay_period_count_for_fy(fy_week1_sunday: date) -> int:
    return len(pay_periods_for_fy(fy_week1_sunday))


def pay_period_index_overlapping(
    fy_week1_sunday: date, bucket_start: date, bucket_end: date
) -> int | None:
    """1-based PP index for the pay period overlapping ``bucket_start``..``bucket_end``, if any."""
    for p in pay_periods_for_fy(fy_week1_sunday):
        if p.end < bucket_start or p.start > bucket_end:
            continue
        return p.period_index
    return None


def _feb_last(calendar_year: int) -> date:
    return date(calendar_year, 3, 1) - timedelta(days=1)


def fiscal_quarter_windows_for_fy(
    fy_week1_sunday: date,
) -> list[tuple[int, date, date]]:
    """
    Ordered (quarter 1..4, start, end) inclusive, clipped to
    ``fy_week1_sunday`` .. ``fy_end_date``.

    Q1 Sep–Nov, Q2 Dec–Feb, Q3 Mar–May, Q4 Jun through FY end (includes closing Sep).
    """
    fy_e = fy_end_date(fy_week1_sunday)
    y = fy_week1_sunday.year
    out: list[tuple[int, date, date]] = []

    q1a = max(fy_week1_sunday, date(y, 9, 1))
    q1b = min(date(y, 11, 30), fy_e)
    if q1a <= q1b:
        out.append((1, q1a, q1b))

    q2a = max(fy_week1_sunday, date(y, 12, 1))
    q2b = min(_feb_last(y + 1), fy_e)
    if q2a <= q2b:
        out.append((2, q2a, q2b))

    q3a = max(fy_week1_sunday, date(y + 1, 3, 1))
    q3b = min(date(y + 1, 5, 31), fy_e)
    if q3a <= q3b:
        out.append((3, q3a, q3b))

    q4a = max(fy_week1_sunday, date(y + 1, 6, 1))
    q4b = fy_e
    if q4a <= q4b:
        out.append((4, q4a, q4b))

    return out


def fiscal_quarter_label(bucket_start: date) -> str:
    """Label like FY2026 Q2 for the fiscal quarter containing ``bucket_start``."""
    fy_w1 = fy_week1_sunday_containing(bucket_start)
    lab = fy_label_year(fy_w1)
    for q, a, b in fiscal_quarter_windows_for_fy(fy_w1):
        if a <= bucket_start <= b:
            return f"FY{lab} Q{q}"
    return f"FY{lab} ?"
