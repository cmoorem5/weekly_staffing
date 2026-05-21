"""Shared date-range bucket helpers for dashboard views."""

from __future__ import annotations

from datetime import date, timedelta

from .fiscal_year import (
    fiscal_quarter_label,
    fiscal_quarter_windows_for_fy,
    fy_week1_sunday_containing,
    next_fy_week1_sunday,
    pay_period_index_overlapping,
    pay_periods_for_fy,
)


def bucket_label(
    granularity: str,
    bucket_start: date,
    bucket_end: date,
    *,
    fy_week1: date | None = None,
) -> str:
    if granularity == "quarter":
        return fiscal_quarter_label(bucket_start)
    if granularity == "month":
        return bucket_start.strftime("%Y-%m")
    if granularity == "pay_period" and fy_week1 is not None:
        idx = pay_period_index_overlapping(fy_week1, bucket_start, bucket_end)
        if idx is not None:
            return f"PP#{idx} ({bucket_start.isoformat()}–{bucket_end.isoformat()})"
    if granularity == "pay_period":
        return f"{bucket_start.isoformat()}–{bucket_end.isoformat()}"
    return f"{bucket_start.isoformat()}–{bucket_end.isoformat()}"


def bucket_label_short(
    granularity: str,
    bucket_start: date,
    bucket_end: date,
    *,
    fy_week1: date | None = None,
) -> str:
    """Compact label for chart axes."""
    if granularity == "quarter":
        return fiscal_quarter_label(bucket_start)
    if granularity == "month":
        return bucket_start.strftime("%b %Y")
    if granularity == "pay_period" and fy_week1 is not None:
        idx = pay_period_index_overlapping(fy_week1, bucket_start, bucket_end)
        if idx is not None:
            return f"PP#{idx}"
    return bucket_start.strftime("%m/%d")


def buckets_for_range(
    granularity: str, range_start: date, range_end: date
) -> list[tuple[date, date]]:
    """Return inclusive (start, end) buckets within ``range_start``..``range_end``."""
    buckets: list[tuple[date, date]] = []
    if range_start > range_end:
        return buckets

    if granularity == "pay_period":
        fy_a = fy_week1_sunday_containing(range_start)
        fy_b = fy_week1_sunday_containing(range_end)
        cur_fy = fy_a
        while True:
            for p in pay_periods_for_fy(cur_fy):
                if p.end < range_start or p.start > range_end:
                    continue
                buckets.append((max(p.start, range_start), min(p.end, range_end)))
            if cur_fy == fy_b:
                break
            cur_fy = next_fy_week1_sunday(cur_fy)
        return buckets

    if granularity == "month":
        cur = date(range_start.year, range_start.month, 1)
        while cur <= range_end:
            next_month = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = next_month - timedelta(days=1)
            buckets.append((max(cur, range_start), min(end, range_end)))
            cur = next_month
        return buckets

    end_fy = fy_week1_sunday_containing(range_end)
    cur_fy = fy_week1_sunday_containing(range_start)
    while True:
        for _, qa, qb in fiscal_quarter_windows_for_fy(cur_fy):
            rs = max(qa, range_start)
            re = min(qb, range_end)
            if rs <= re:
                buckets.append((rs, re))
        if cur_fy == end_fy:
            break
        cur_fy = next_fy_week1_sunday(cur_fy)
    return buckets
