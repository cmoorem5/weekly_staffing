"""Coverage heatmap: base × day-of-week staffing from the ops view import.

Shows where coverage holes recur (e.g. "Manchester Thursdays") instead of
letting weekly averages hide them. Data comes from ``weekly_ops_view_days``
(per-day staffed RW/GR counts captured on schedule import) against each
base's planned unit-days from ``base_config`` (weekly totals / 7).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render
from staffing_tool.db import session_scope
from staffing_tool.models import BaseConfig, WeeklyOpsViewDay

from .helpers import DB_PATH, _ensure_db

# Sun→Sat, matching the dashboard's week convention (weeks start Sunday).
WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
# Python weekday() (Mon=0..Sun=6) -> column index in WEEKDAY_LABELS.
_COL_FOR_WEEKDAY = {6: 0, 0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6}

DEFAULT_WEEKS = 8


def _bucket(pct: float | None) -> str:
    """CSS bucket for a coverage percentage."""
    if pct is None:
        return "none"
    if pct >= 0.999:
        return "full"
    if pct >= 0.90:
        return "good"
    if pct >= 0.75:
        return "watch"
    if pct >= 0.50:
        return "low"
    return "critical"


def _parse_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _build_grid(rows, bases, kind: str, day_counts: dict[int, int]):
    """One heatmap grid ('rw' or 'gr'): rows of cells keyed base × weekday."""
    staffed: dict[tuple[str, int], int] = {}
    for r in rows:
        day = datetime.strptime(str(r.day_date), "%Y-%m-%d").date()
        col = _COL_FOR_WEEKDAY[day.weekday()]
        count = int(r.rw_count if kind == "rw" else r.gr_count)
        staffed[(str(r.base_name), col)] = (
            staffed.get((str(r.base_name), col), 0) + count
        )

    grid = []
    for cfg in bases:
        weekly_cap = int(
            cfg.rw_total_unit_days if kind == "rw" else cfg.gr_total_unit_days
        )
        daily_cap = weekly_cap / 7.0
        cells = []
        for col in range(7):
            n_days = day_counts.get(col, 0)
            capacity = daily_cap * n_days
            worked = staffed.get((str(cfg.base_name), col), 0)
            if capacity <= 0 or n_days == 0:
                cells.append({"bucket": "none", "pct": None, "label": "—"})
                continue
            pct = worked / capacity
            unfilled = max(0.0, capacity - worked)
            cells.append(
                {
                    "bucket": _bucket(pct),
                    "pct": round(100 * pct),
                    "label": f"{round(100 * pct)}%",
                    "detail": (
                        f"{worked} staffed / {capacity:.0f} planned over "
                        f"{n_days} day(s) — {unfilled:.0f} unfilled"
                    ),
                }
            )
        if weekly_cap > 0:
            grid.append({"base": str(cfg.base_name), "cells": cells})
    return grid


def coverage_heatmap(request):
    _ensure_db()
    if not DB_PATH:
        messages.error(request, "Database is not configured (STAFFING_DB_PATH).")
        return redirect("home")

    with session_scope(DB_PATH) as session:
        latest = (
            session.query(WeeklyOpsViewDay.day_date)
            .order_by(WeeklyOpsViewDay.day_date.desc())
            .first()
        )
        if latest is None:
            return render(
                request,
                "dashboard/coverage_heatmap.html",
                {"has_data": False},
            )

        default_end = datetime.strptime(str(latest[0]), "%Y-%m-%d").date()
        default_start = default_end - timedelta(days=7 * DEFAULT_WEEKS - 1)
        start = _parse_date(request.GET.get("start", "")) or default_start
        end = _parse_date(request.GET.get("end", "")) or default_end
        if start > end:
            start, end = end, start

        rows = (
            session.query(WeeklyOpsViewDay)
            .filter(WeeklyOpsViewDay.day_date >= start.isoformat())
            .filter(WeeklyOpsViewDay.day_date <= end.isoformat())
            .all()
        )
        bases = list(session.query(BaseConfig).order_by(BaseConfig.base_name).all())

        # How many distinct dates fall on each weekday (per-cell denominator).
        seen_dates = {str(r.day_date) for r in rows}
        day_counts: dict[int, int] = {}
        for iso in seen_dates:
            day = datetime.strptime(iso, "%Y-%m-%d").date()
            col = _COL_FOR_WEEKDAY[day.weekday()]
            day_counts[col] = day_counts.get(col, 0) + 1

        rw_grid = _build_grid(rows, bases, "rw", day_counts)
        gr_grid = _build_grid(rows, bases, "gr", day_counts)

    return render(
        request,
        "dashboard/coverage_heatmap.html",
        {
            "has_data": bool(rows),
            "start": start,
            "end": end,
            "weekday_labels": WEEKDAY_LABELS,
            "rw_grid": rw_grid,
            "gr_grid": gr_grid,
            "days_covered": len(seen_dates),
        },
    )
