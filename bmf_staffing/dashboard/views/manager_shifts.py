"""Manager line shifts listing view."""

from collections import defaultdict
from datetime import date, datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from sqlalchemy import func
from staffing_tool.db import session_scope
from staffing_tool.fiscal_year import (
    fy_end_date,
    fy_label_year,
    fy_week1_for_label_year,
    fy_week1_sunday_containing,
    pay_period_count_for_fy,
)
from staffing_tool.models import WeeklyManagerShift

from .helpers import DB_PATH, _ensure_db

# Manager line-shift minimums (policy): full FY and biweekly pay-period equivalent.
MANAGER_MIN_SHIFTS_PER_FY = 52
MANAGER_MIN_PER_PAY_PERIOD = 2


def _prorated_manager_minimum(
    range_start: date, range_end: date
) -> tuple[float, date, date, int, int]:
    """
    Minimum expected manager line-shifts per person for ``range_start``..``range_end``,
    prorated from 52 per fiscal year over the overlap with the FY that contains ``range_end``.

    Returns (target_float, fy_start, fy_end, overlap_days, fy_total_days).
    """
    fy_start = fy_week1_sunday_containing(range_end)
    fy_end = fy_end_date(fy_start)
    fy_total_days = (fy_end - fy_start).days + 1
    overlap_s = max(range_start, fy_start)
    overlap_e = min(range_end, fy_end)
    if overlap_s > overlap_e:
        return 0.0, fy_start, fy_end, 0, fy_total_days
    overlap_days = (overlap_e - overlap_s).days + 1
    target = MANAGER_MIN_SHIFTS_PER_FY * overlap_days / fy_total_days
    return target, fy_start, fy_end, overlap_days, fy_total_days


def manager_shifts(request):
    """Manager line shifts stored from schedule import (date range filter)."""
    _ensure_db()
    if not DB_PATH:
        messages.error(request, "Database is not configured (STAFFING_DB_PATH).")
        return redirect("home")

    today = date.today()
    fy_param = (request.GET.get("fy") or "").strip()
    if fy_param.isdigit():
        w1 = fy_week1_for_label_year(int(fy_param))
        default_start = (w1 or fy_week1_sunday_containing(today)).isoformat()
    else:
        default_start = fy_week1_sunday_containing(today).isoformat()
    default_end = today.isoformat()
    date_start = (request.GET.get("date_start") or default_start).strip()
    date_end = (request.GET.get("date_end") or default_end).strip()
    try:
        datetime.strptime(date_start, "%Y-%m-%d")
        datetime.strptime(date_end, "%Y-%m-%d")
    except ValueError:
        messages.error(request, "Use YYYY-MM-DD for both dates.")
        date_start, date_end = default_start, default_end

    totals: dict[str, int] = defaultdict(int)
    with session_scope(DB_PATH) as session:
        db_min, db_max = (
            session.query(
                func.min(WeeklyManagerShift.shift_date),
                func.max(WeeklyManagerShift.shift_date),
            ).one()
        )
        shifts = (
            session.query(WeeklyManagerShift)
            .filter(
                WeeklyManagerShift.shift_date >= date_start,
                WeeklyManagerShift.shift_date <= date_end,
            )
            .order_by(
                WeeklyManagerShift.shift_date,
                WeeklyManagerShift.person_display,
                WeeklyManagerShift.role,
                WeeklyManagerShift.unit_code,
            )
            .all()
        )
    for m in shifts:
        totals[m.person_display] += 1
    grand_total = len(shifts)
    cumulative_rows: list[dict[str, object]] = []
    running = 0
    totals_by_person = sorted(totals.items(), key=lambda x: (-x[1], x[0].lower()))
    range_start_d = date.fromisoformat(date_start)
    range_end_d = date.fromisoformat(date_end)
    prorated_min, fy_anchor_start, fy_anchor_end, overlap_days, fy_total_days = (
        _prorated_manager_minimum(range_start_d, range_end_d)
    )

    for name, n in totals_by_person:
        running += n
        pct = round(100.0 * n / grand_total, 1) if grand_total else 0.0
        if prorated_min < 1e-6:
            status_label = "N/A (no FY overlap)"
            met = True
            delta = float(n)
            target_disp = 0.0
        else:
            delta = n - prorated_min
            met = n >= prorated_min - 1e-6
            short_by = max(0.0, prorated_min - n)
            ahead_by = max(0.0, n - prorated_min)
            target_disp = round(prorated_min, 1)
            if met and ahead_by < 0.05:
                status_label = "Met"
            elif met:
                status_label = f"Ahead by {ahead_by:.1f}"
            else:
                status_label = f"Short by {short_by:.1f}"
        cumulative_rows.append(
            {
                "name": name,
                "count": n,
                "pct": pct,
                "running": running,
                "target": target_disp,
                "delta": round(delta, 1),
                "met": met,
                "status_label": status_label,
            }
        )

    fy_anchor = fy_week1_sunday_containing(today)
    fy_end_cur = fy_end_date(fy_anchor)
    fy_label_current = fy_label_year(fy_anchor)
    cy_start = date(today.year, 1, 1)
    pp_count = pay_period_count_for_fy(fy_anchor)
    return render(
        request,
        "dashboard/manager_shifts.html",
        {
            "date_start": date_start,
            "date_end": date_end,
            "shifts": shifts,
            "cumulative_rows": cumulative_rows,
            "shift_count": grand_total,
            "grand_total": grand_total,
            "today_iso": today.isoformat(),
            "fy_label_current": fy_label_current,
            "fy_start_iso": fy_anchor.isoformat(),
            "fy_end_iso": fy_end_cur.isoformat(),
            "calendar_year_start_iso": cy_start.isoformat(),
            "db_date_min": db_min,
            "db_date_max": db_max,
            "manager_min_fy": MANAGER_MIN_SHIFTS_PER_FY,
            "manager_min_per_pp": MANAGER_MIN_PER_PAY_PERIOD,
            "manager_pp_per_fy": pp_count,
            "prorated_manager_min": round(prorated_min, 1),
            "fy_target_start": fy_anchor_start.isoformat(),
            "fy_target_end": fy_anchor_end.isoformat(),
            "fy_overlap_days": overlap_days,
            "fy_total_days": fy_total_days,
        },
    )
