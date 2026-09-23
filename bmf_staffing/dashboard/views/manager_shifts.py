"""Manager line shifts listing view and exports."""

import csv
import io
import json
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from openpyxl import Workbook
from sqlalchemy import func
from staffing_tool.db import session_scope
from staffing_tool.fiscal_year import (
    fiscal_quarter_windows_for_fy,
    fy_end_date,
    fy_label_year,
    fy_week1_sunday_containing,
    pay_period_count_for_fy,
    pay_periods_for_fy,
)
from staffing_tool.manager_names import canonical_manager_name
from staffing_tool.models import ManagerRequirement, WeeklyManagerShift
from staffing_tool.time_buckets import (
    bucket_label,
    bucket_label_short,
    buckets_for_range,
)
from staffing_tool.timeutil import utc_now_iso as _utc_now_iso

from .dashboard_filters import (
    fy_choice_rows,
    last_closed_pay_period_end_for_fy,
    parse_date_param,
    parse_fy_week1_from_request,
    serialize_filters_query,
)
from .helpers import (
    DB_PATH,
    FY_AND_PAY_PERIOD_POLICY_NOTE,
    _ensure_db,
    _manager_last_names_upper_for_parse,
)

# Manager line-shift minimums (policy): full FY and biweekly pay-period equivalent.
MANAGER_MIN_SHIFTS_PER_FY = 52
MANAGER_MIN_PER_PAY_PERIOD = 2
# AOC days convert to line-shift credit at this rate: every 7 AOC days is one
# week of AOC coverage, worth 1 shift against the 2-per-pay-period minimum.
# The division is on the manager's total AOC days in the range, not on which
# calendar weeks those days landed in -- scattered AOC days would otherwise
# credit a whole week each.
MANAGER_AOC_DAYS_PER_CREDIT = 7
# Manager leave is entered by hand per manager (annual shifts) because schedule
# workbooks do not carry manager LT reliably and each manager's entitlement
# differs. Imported leave rows are shown for reference only.
MANAGER_LEAVE_CREDIT_NOTE = (
    "Manual annual figure per manager (Settings on the manager row); "
    "LT on the schedule workbook is reference only and does not adjust targets."
)
MANAGER_DETAIL_PAGE_SIZE = 50
MANAGER_AOC_DETAIL_PAGE_SIZE = 50


def _manager_row_event_type(row: WeeklyManagerShift) -> str:
    """Normalize stored event_type (legacy rows default to line_shift)."""
    et = (getattr(row, "event_type", None) or "").strip().lower()
    return et if et in {"line_shift", "aoc", "leave"} else "line_shift"


def write_manager_line_shift_sheet(
    ws, rows: list[dict[str, object]], *, include_week_start: bool
) -> None:
    """Shared column layout for a manager line-shift detail sheet.

    Used by both this report's applied-data export (multi-week, so it
    includes ``Week start``) and the pre-apply import-review export
    (a single previewed week, so it omits that redundant column) —
    keeping one writer means the two can't silently drift apart.
    """
    header = [
        "Shift date",
        "Manager",
        "Legacy label",
        "Role",
        "Base",
        "RW/GR",
        "D/N",
        "Unit",
        "OT",
        "Source value",
    ]
    if include_week_start:
        header.append("Week start")
    header += ["Source tab", "Source cell"]
    ws.append(header)
    for row in rows:
        values = [
            row.get("shift_date"),
            row.get("person_display"),
            row.get("raw_person_display") or "",
            row.get("role"),
            row.get("base_name"),
            row.get("service_type"),
            row.get("day_night"),
            row.get("unit_code"),
            "Yes" if row.get("overtime") else "",
            row.get("raw_value"),
        ]
        if include_week_start:
            values.append(row.get("week_start"))
        values += [row.get("source_tab"), row.get("source_cell")]
        ws.append(values)


def write_manager_aoc_sheet(
    ws, rows: list[dict[str, object]], *, include_week_start: bool
) -> None:
    """Shared column layout for a manager AOC-day detail sheet (see
    ``write_manager_line_shift_sheet`` for why this is factored out)."""
    header = ["Date", "Manager", "Legacy label", "Role", "Source value"]
    if include_week_start:
        header.append("Week start")
    header += ["Source tab", "Source cell"]
    ws.append(header)
    for row in rows:
        values = [
            row.get("shift_date"),
            row.get("person_display"),
            row.get("raw_person_display") or "",
            row.get("role"),
            row.get("raw_value"),
        ]
        if include_week_start:
            values.append(row.get("week_start"))
        values += [row.get("source_tab"), row.get("source_cell")]
        ws.append(values)


# Distinct colors for stacked period chart (BMF palette + extras).
MANAGER_CHART_COLORS = (
    "#2a4492",
    "#052c47",
    "#c12126",
    "#0b3d91",
    "#5c2d91",
    "#b31b1b",
    "#198754",
    "#fd7e14",
    "#6f42c1",
    "#20c997",
    "#495057",
    "#adb5bd",
)


def _prorated_manager_minimum(
    range_start: date, range_end: date, annual_min: int = MANAGER_MIN_SHIFTS_PER_FY
) -> tuple[float, date, date, int, int]:
    """
    Minimum expected manager line-shifts per person for ``range_start``..``range_end``,
    prorated from ``annual_min`` per fiscal year over the overlap with the FY that
    contains ``range_end``. ``annual_min`` defaults to the policy minimum (52) but
    can be a manager's own override (see ``ManagerRequirement``).

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
    target = annual_min * overlap_days / fy_total_days
    return target, fy_start, fy_end, overlap_days, fy_total_days


def _manager_requirements(session) -> dict[str, dict[str, object]]:
    """Per-manager annual requirement and manual leave-credit overrides.

    Maps ``person_display`` to ``{"annual", "leave_credit", "note"}``. Managers
    without a row fall back to the policy minimum and no leave credit.
    """
    rows = session.query(ManagerRequirement).all()
    return {
        r.person_display: {
            "annual": int(r.annual_shift_requirement),
            "leave_credit": int(getattr(r, "annual_leave_credit_shifts", 0) or 0),
            "note": (getattr(r, "leave_credit_note", "") or "").strip(),
        }
        for r in rows
    }


def _aoc_shift_credit(aoc_days: int) -> int:
    """Line-shift credit earned by ``aoc_days`` days of AOC coverage.

    Total days over MANAGER_AOC_DAYS_PER_CREDIT (7), rounded to the nearest
    whole shift: 76 AOC days is 10.9 weeks of coverage and earns 11 shifts.
    Rounds half up rather than using ``round``, which is half-to-even and would
    send 10.5 down to 10 while sending 3.5 up to 4.
    """
    if aoc_days <= 0:
        return 0
    weeks = Decimal(aoc_days) / Decimal(MANAGER_AOC_DAYS_PER_CREDIT)
    return int(weeks.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _aoc_weeks_display(aoc_days: int) -> float:
    """AOC days as weeks, to one decimal, for showing the credit's derivation."""
    if aoc_days <= 0:
        return 0.0
    return round(aoc_days / MANAGER_AOC_DAYS_PER_CREDIT, 1)


def _status_for_count(n: int, prorated_min: float) -> tuple[str, bool, float, float]:
    if prorated_min < 1e-6:
        return "N/A (no FY overlap)", True, float(n), 0.0
    delta = n - prorated_min
    met = n >= prorated_min - 1e-6
    short_by = max(0.0, prorated_min - n)
    ahead_by = max(0.0, n - prorated_min)
    if met and ahead_by < 0.05:
        status_label = "Met"
    elif met:
        status_label = f"Ahead by {ahead_by:.1f}"
    else:
        status_label = f"Short by {short_by:.1f}"
    return status_label, met, round(delta, 1), round(prorated_min, 1)


def _build_manager_shifts_context(request) -> dict[str, object]:
    """Shared context for manager shifts page and export endpoints."""
    _ensure_db()
    if not DB_PATH:
        raise Http404("Database is not configured (STAFFING_DB_PATH).")

    today = date.today()
    roster_upper = _manager_last_names_upper_for_parse()
    fy_start = parse_fy_week1_from_request(request, today)
    fy_end = fy_end_date(fy_start)
    fy_label = fy_label_year(fy_start)
    fy_choices = fy_choice_rows(fy_label_year(fy_week1_sunday_containing(today)))

    granularity = (request.GET.get("granularity") or "pay_period").strip().lower()
    if granularity not in {"quarter", "month", "pay_period", "fy_total"}:
        granularity = "pay_period"

    is_current_fy = fy_start == fy_week1_sunday_containing(today)
    last_closed_in_fy = last_closed_pay_period_end_for_fy(today, fy_start)
    default_end = last_closed_in_fy if is_current_fy else fy_end
    default_start = fy_start

    date_start = parse_date_param(request.GET.get("date_start", ""), default_start)
    date_end = parse_date_param(request.GET.get("date_end", ""), default_end)
    date_start = max(date_start, fy_start)
    date_end = min(date_end, fy_end)
    if date_start > date_end:
        date_start, date_end = default_start, default_end

    date_start_s = date_start.isoformat()
    date_end_s = date_end.isoformat()

    totals: dict[str, int] = defaultdict(int)
    aoc_totals: dict[str, int] = defaultdict(int)
    leave_day_totals: dict[str, int] = defaultdict(int)
    leave_codes: dict[str, set[str]] = defaultdict(set)
    shift_rows: list[dict[str, object]] = []
    aoc_rows: list[dict[str, object]] = []
    with session_scope(DB_PATH) as session:
        db_min, db_max = session.query(
            func.min(WeeklyManagerShift.shift_date),
            func.max(WeeklyManagerShift.shift_date),
        ).one()
        shifts_raw = (
            session.query(WeeklyManagerShift)
            .filter(
                WeeklyManagerShift.shift_date >= date_start_s,
                WeeklyManagerShift.shift_date <= date_end_s,
            )
            .order_by(
                WeeklyManagerShift.shift_date,
                WeeklyManagerShift.person_display,
                WeeklyManagerShift.role,
                WeeklyManagerShift.unit_code,
            )
            .all()
        )
        for m in shifts_raw:
            raw_name = (m.person_display or "").strip() or "(unknown)"
            canon = canonical_manager_name(raw_name, roster_upper)
            event_type = _manager_row_event_type(m)
            if event_type == "leave":
                # Informational only. Manager leave on the schedule workbook is
                # incomplete and each manager's annual LT entitlement differs,
                # so the target is reduced by the manual annual leave credit on
                # ManagerRequirement, never by these rows.
                leave_day_totals[canon] += 1
                leave_code = (m.leave_type or "").strip().upper()
                if leave_code:
                    leave_codes[canon].add(leave_code)
                continue
            if event_type == "aoc":
                aoc_totals[canon] += 1
                aoc_rows.append(
                    {
                        "shift_date": m.shift_date,
                        "person_display": canon,
                        "raw_person_display": raw_name if raw_name != canon else "",
                        "role": m.role,
                        "raw_value": m.raw_value,
                        "week_start": m.week_start,
                        "source_tab": m.source_tab,
                        "source_cell": m.source_cell,
                    }
                )
                continue
            totals[canon] += 1
            shift_rows.append(
                {
                    "shift_date": m.shift_date,
                    "person_display": canon,
                    "raw_person_display": raw_name if raw_name != canon else "",
                    "role": m.role,
                    "base_name": m.base_name,
                    "service_type": m.service_type,
                    "day_night": m.day_night,
                    "unit_code": m.unit_code,
                    "overtime": m.overtime,
                    "raw_value": m.raw_value,
                    "week_start": m.week_start,
                    "source_tab": m.source_tab,
                    "source_cell": m.source_cell,
                }
            )
        requirements = _manager_requirements(session)

    grand_total = len(shift_rows)
    aoc_grand_total = len(aoc_rows)
    range_start_d = date_start
    range_end_d = date_end
    # Default (policy, no per-manager override or leave credit) prorated
    # target -- shown as reference context; per-manager rows below compute
    # their own adjusted target.
    (
        default_prorated_min,
        fy_anchor_start,
        fy_anchor_end,
        overlap_days,
        fy_total_days,
    ) = _prorated_manager_minimum(range_start_d, range_end_d)

    cumulative_rows: list[dict[str, object]] = []
    running = 0
    all_manager_names = sorted(
        set(totals) | set(aoc_totals) | set(leave_day_totals),
        key=lambda n: (-(totals.get(n, 0) + aoc_totals.get(n, 0)), n.lower()),
    )
    totals_by_person = sorted(totals.items(), key=lambda x: (-x[1], x[0].lower()))
    for name in all_manager_names:
        n = totals.get(name, 0)
        aoc_n = aoc_totals.get(name, 0)
        if n:
            running += n
        pct = round(100.0 * n / grand_total, 1) if grand_total else 0.0
        override = requirements.get(name) or {}
        annual_requirement = int(override.get("annual", MANAGER_MIN_SHIFTS_PER_FY) or 0)
        annual_leave_credit = int(override.get("leave_credit", 0) or 0)
        leave_note = str(override.get("note", "") or "")
        # The manual leave credit is an annual figure, so it is prorated with
        # the annual requirement rather than subtracted whole from a partial
        # window: net the two first, then scale to the selected dates.
        net_annual = max(0, annual_requirement - annual_leave_credit)
        prorated_min, _fs, _fe, _od, _ftd = _prorated_manager_minimum(
            range_start_d, range_end_d, annual_min=net_annual
        )
        prorated_before_leave, _fs2, _fe2, _od2, _ftd2 = _prorated_manager_minimum(
            range_start_d, range_end_d, annual_min=annual_requirement
        )
        leave_credit = round(prorated_before_leave - prorated_min, 1)
        # AOC days are observed inside the selected range, so their credit is
        # subtracted as counted, not prorated.
        aoc_weeks = _aoc_weeks_display(aoc_n)
        aoc_credit = _aoc_shift_credit(aoc_n)
        adjusted_min = max(0.0, prorated_min - aoc_credit)
        status_label, met, delta, target_disp = _status_for_count(n, adjusted_min)
        cumulative_rows.append(
            {
                "name": name,
                "count": n,
                "aoc_count": aoc_n,
                "pct": pct,
                "running": running if n else None,
                "annual_requirement": annual_requirement,
                "annual_leave_credit": annual_leave_credit,
                "leave_credit": leave_credit,
                "leave_note": leave_note,
                "leave_days": leave_day_totals.get(name, 0),
                "leave_codes": ", ".join(sorted(leave_codes.get(name, set()))),
                "aoc_weeks": aoc_weeks,
                "aoc_credit": aoc_credit,
                "target": target_disp,
                "delta": delta,
                "met": met,
                "status_label": status_label if n else "—",
            }
        )

    chart_granularity = "fy_total" if granularity == "fy_total" else granularity
    buckets = (
        [(range_start_d, range_end_d)]
        if chart_granularity == "fy_total"
        else buckets_for_range(chart_granularity, range_start_d, range_end_d)
    )
    bucket_labels_full = [
        bucket_label(chart_granularity, bs, be, fy_week1=fy_start) for bs, be in buckets
    ]
    bucket_labels_short = [
        bucket_label_short(chart_granularity, bs, be, fy_week1=fy_start)
        for bs, be in buckets
    ]

    manager_bucket: dict[str, list[int]] = {
        name: [0] * len(buckets) for name, _ in totals_by_person
    }
    for row in shift_rows:
        sd = date.fromisoformat(str(row["shift_date"]))
        name = str(row["person_display"])
        if name not in manager_bucket:
            manager_bucket[name] = [0] * len(buckets)
        for i, (bs, be) in enumerate(buckets):
            if bs <= sd <= be:
                manager_bucket[name][i] += 1
                break

    period_table_rows: list[dict[str, object]] = []
    for name, _n in totals_by_person:
        counts = manager_bucket.get(name, [0] * len(buckets))
        period_table_rows.append({"name": name, "counts": counts, "total": sum(counts)})

    top_n = 10
    chart_managers = [name for name, _n in totals_by_person[:top_n]]
    if len(totals_by_person) > top_n:
        chart_managers.append("Other")
    stacked_series: dict[str, list[int]] = {}
    for mgr in chart_managers:
        if mgr == "Other":
            other_counts = [0] * len(buckets)
            for name, counts in manager_bucket.items():
                if name in chart_managers[:-1]:
                    continue
                for i, c in enumerate(counts):
                    other_counts[i] += c
            stacked_series[mgr] = other_counts
        else:
            stacked_series[mgr] = manager_bucket.get(mgr, [0] * len(buckets))

    progress_labels = [row["name"] for row in cumulative_rows]
    progress_shifts = [row["count"] for row in cumulative_rows]
    progress_targets = [
        row["target"] if row["target"] else 0 for row in cumulative_rows
    ]
    progress_met = [row["met"] for row in cumulative_rows]

    periods = pay_periods_for_fy(fy_start)
    end_anchor = default_end
    closed = [p for p in periods if p.end <= end_anchor]
    last6 = closed[-6:] if len(closed) >= 6 else closed
    last6_start = last6[0].start if last6 else fy_start
    last6_end = last6[-1].end if last6 else end_anchor
    quarter_presets: list[dict[str, str]] = []
    for qnum, qa, qb in fiscal_quarter_windows_for_fy(fy_start):
        rs = max(qa, fy_start)
        re = min(qb, fy_end)
        if rs > re:
            continue
        quarter_presets.append(
            {
                "label": f"FY{fy_label} Q{qnum}",
                "qs": serialize_filters_query(fy_label, "quarter", rs, re),
            }
        )
    preset_links = {
        "fy_ytd": {
            "label": "FY YTD (last closed PP)",
            "qs": serialize_filters_query(fy_label, "pay_period", fy_start, end_anchor),
        },
        "last_6_pp": {
            "label": "Last 6 pay periods",
            "qs": serialize_filters_query(
                fy_label, "pay_period", last6_start, last6_end
            ),
        },
        "full_fy": {
            "label": "Full FY",
            "qs": serialize_filters_query(fy_label, "quarter", fy_start, fy_end),
        },
        "fy_total": {
            "label": "FY total (single bar)",
            "qs": serialize_filters_query(fy_label, "fy_total", fy_start, end_anchor),
        },
    }

    fy_anchor = fy_week1_sunday_containing(today)
    fy_end_cur = fy_end_date(fy_anchor)
    fy_label_current = fy_label_year(fy_anchor)
    cy_start = date(today.year, 1, 1)
    pp_count = pay_period_count_for_fy(fy_anchor)

    page_raw = (request.GET.get("page") or "1").strip()
    try:
        detail_page = max(1, int(page_raw))
    except ValueError:
        detail_page = 1
    detail_total = len(shift_rows)
    detail_page_count = max(
        1, (detail_total + MANAGER_DETAIL_PAGE_SIZE - 1) // MANAGER_DETAIL_PAGE_SIZE
    )
    detail_page = min(detail_page, detail_page_count)
    detail_start = (detail_page - 1) * MANAGER_DETAIL_PAGE_SIZE
    detail_page_rows = shift_rows[
        detail_start : detail_start + MANAGER_DETAIL_PAGE_SIZE
    ]

    aoc_page_raw = (request.GET.get("aoc_page") or "1").strip()
    try:
        aoc_detail_page = max(1, int(aoc_page_raw))
    except ValueError:
        aoc_detail_page = 1
    aoc_detail_total = len(aoc_rows)
    aoc_detail_page_count = max(
        1,
        (aoc_detail_total + MANAGER_AOC_DETAIL_PAGE_SIZE - 1)
        // MANAGER_AOC_DETAIL_PAGE_SIZE,
    )
    aoc_detail_page = min(aoc_detail_page, aoc_detail_page_count)
    aoc_detail_start = (aoc_detail_page - 1) * MANAGER_AOC_DETAIL_PAGE_SIZE
    aoc_detail_page_rows = aoc_rows[
        aoc_detail_start : aoc_detail_start + MANAGER_AOC_DETAIL_PAGE_SIZE
    ]

    aoc_summary_rows = sorted(
        (
            {"name": name, "count": aoc_totals[name]}
            for name in all_manager_names
            if aoc_totals.get(name, 0)
        ),
        key=lambda row: (-row["count"], row["name"].lower()),
    )

    return {
        "date_start": date_start_s,
        "date_end": date_end_s,
        "shifts": detail_page_rows,
        "all_shifts": shift_rows,
        "aoc_rows": aoc_detail_page_rows,
        "all_aoc_rows": aoc_rows,
        "aoc_summary_rows": aoc_summary_rows,
        "aoc_grand_total": aoc_grand_total,
        "detail_page": detail_page,
        "detail_page_count": detail_page_count,
        "detail_page_size": MANAGER_DETAIL_PAGE_SIZE,
        "detail_total": detail_total,
        "aoc_detail_page": aoc_detail_page,
        "aoc_detail_page_count": aoc_detail_page_count,
        "aoc_detail_page_size": MANAGER_AOC_DETAIL_PAGE_SIZE,
        "aoc_detail_total": aoc_detail_total,
        "cumulative_rows": cumulative_rows,
        "period_table_rows": period_table_rows,
        "bucket_labels": bucket_labels_full,
        "shift_count": grand_total,
        "grand_total": grand_total,
        "today_iso": today.isoformat(),
        "fy_label": fy_label,
        "fy_label_current": fy_label_current,
        "fy_start": fy_start.isoformat(),
        "fy_end": fy_end.isoformat(),
        "fy_start_iso": fy_anchor.isoformat(),
        "fy_end_iso": fy_end_cur.isoformat(),
        "calendar_year_start_iso": cy_start.isoformat(),
        "db_date_min": db_min,
        "db_date_max": db_max,
        "manager_min_fy": MANAGER_MIN_SHIFTS_PER_FY,
        "manager_min_per_pp": MANAGER_MIN_PER_PAY_PERIOD,
        "manager_aoc_days_per_credit": MANAGER_AOC_DAYS_PER_CREDIT,
        "manager_pp_per_fy": pp_count,
        "prorated_manager_min": round(default_prorated_min, 1),
        "fy_target_start": fy_anchor_start.isoformat(),
        "fy_target_end": fy_anchor_end.isoformat(),
        "fy_overlap_days": overlap_days,
        "fy_total_days": fy_total_days,
        "granularity": granularity,
        "fy_choices": fy_choices,
        "preset_links": preset_links,
        "quarter_presets": quarter_presets,
        "filters_qs": serialize_filters_query(
            fy_label, granularity, date_start, date_end
        ),
        "chart_labels_json": json.dumps(bucket_labels_short),
        "chart_stacked_json": json.dumps(stacked_series),
        "chart_colors_json": json.dumps(
            list(MANAGER_CHART_COLORS[: len(chart_managers)])
        ),
        "chart_progress_labels_json": json.dumps(progress_labels),
        "chart_progress_shifts_json": json.dumps(progress_shifts),
        "chart_progress_targets_json": json.dumps(progress_targets),
        "chart_progress_met_json": json.dumps(progress_met),
        "is_current_fy": is_current_fy,
        "data_through": end_anchor.isoformat(),
    }


def manager_shifts(request):
    """Manager line shifts stored from schedule import (date range filter)."""
    try:
        ctx = _build_manager_shifts_context(request)
    except Http404 as exc:
        messages.error(request, str(exc))
        return redirect("home")
    return render(request, "dashboard/manager_shifts.html", ctx)


def manager_requirement_save(request):
    """Save one manager's annual requirement and manual leave credit.

    Both fields are optional in the POST: the form on the manager report posts
    only the one that was edited, so a blank field leaves the stored value
    alone rather than resetting it to zero.
    """
    _ensure_db()
    if request.method != "POST" or not DB_PATH:
        return redirect("manager_shifts")

    person_display = (request.POST.get("person_display") or "").strip()
    raw_requirement = (request.POST.get("annual_shift_requirement") or "").strip()
    raw_leave_credit = (request.POST.get("annual_leave_credit_shifts") or "").strip()
    leave_note = (request.POST.get("leave_credit_note") or "").strip()[:256]
    note_submitted = "leave_credit_note" in request.POST
    filters_qs = request.POST.get("filters_qs") or ""

    def _whole_number(raw: str, label: str) -> int | None:
        try:
            return max(0, int(raw))
        except ValueError:
            messages.error(request, f"{label} must be a whole number.")
            return None

    annual_value = (
        _whole_number(raw_requirement, "Annual requirement")
        if raw_requirement
        else None
    )
    leave_value = (
        _whole_number(raw_leave_credit, "Leave credit") if raw_leave_credit else None
    )
    bad_input = (raw_requirement and annual_value is None) or (
        raw_leave_credit and leave_value is None
    )

    if person_display and not bad_input:
        with session_scope(DB_PATH) as session:
            row = session.get(ManagerRequirement, person_display)
            if row is None:
                row = ManagerRequirement(person_display=person_display)
                session.add(row)
            if annual_value is not None:
                row.annual_shift_requirement = annual_value
            if leave_value is not None:
                row.annual_leave_credit_shifts = leave_value
            if note_submitted:
                row.leave_credit_note = leave_note or None
            # Flush so a newly created row picks up its column defaults before
            # the confirmation message reads them back.
            session.flush()
            saved_requirement = int(row.annual_shift_requirement or 0)
            saved_leave = int(row.annual_leave_credit_shifts or 0)
        messages.success(
            request,
            f"{person_display}: annual requirement {saved_requirement} shifts, "
            f"leave credit {saved_leave} shifts "
            f"(net {max(0, saved_requirement - saved_leave)}).",
        )

    url = reverse("manager_shifts")
    if filters_qs:
        url = f"{url}?{filters_qs}"
    return redirect(url)


def manager_shifts_export_csv(request):
    """Export manager shift summary and detail for the current filter selection."""
    ctx = _build_manager_shifts_context(request)
    cumulative_rows = cast(list[dict[str, object]], ctx.get("cumulative_rows") or [])
    period_rows = cast(list[dict[str, object]], ctx.get("period_table_rows") or [])
    bucket_labels = cast(list[str], ctx.get("bucket_labels") or [])
    shift_rows = cast(
        list[dict[str, object]], ctx.get("all_shifts") or ctx.get("shifts") or []
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Metadata"])
    writer.writerow(["Generated (UTC)", _utc_now_iso()])
    writer.writerow(["Report", "Manager line shifts"])
    writer.writerow(["FY label", f"FY{ctx.get('fy_label')}"])
    writer.writerow(["PP#1 week-1 Sunday (FY start)", ctx.get("fy_start")])
    writer.writerow(["FY end (inclusive)", ctx.get("fy_end")])
    writer.writerow(["FY / pay period policy", FY_AND_PAY_PERIOD_POLICY_NOTE])
    writer.writerow(["Minimum shifts per person per FY", ctx.get("manager_min_fy")])
    writer.writerow(
        ["Minimum shifts per pay period (policy)", ctx.get("manager_min_per_pp")]
    )
    writer.writerow(
        ["AOC credit (AOC days per shift)", ctx.get("manager_aoc_days_per_credit")]
    )
    writer.writerow(["LT credit source", MANAGER_LEAVE_CREDIT_NOTE])
    writer.writerow(["Granularity", ctx.get("granularity")])
    writer.writerow(["Date start", ctx.get("date_start")])
    writer.writerow(["Date end", ctx.get("date_end")])
    writer.writerow(["Data through (FY YTD default)", ctx.get("data_through")])
    writer.writerow(["Prorated minimum (per person)", ctx.get("prorated_manager_min")])
    writer.writerow(["FY target window start", ctx.get("fy_target_start")])
    writer.writerow(["FY target window end", ctx.get("fy_target_end")])
    writer.writerow(["Total person-shifts", ctx.get("grand_total")])
    writer.writerow(["Total AOC days", ctx.get("aoc_grand_total")])
    writer.writerow([])

    writer.writerow(
        [
            "Manager (last name)",
            "Shifts",
            "AOC days",
            "Annual req.",
            "LT credit (annual, manual)",
            "LT credit note",
            "LT credit applied",
            "LT days on schedule",
            "AOC weeks (days / 7)",
            "AOC credit shifts",
            "Min (prorated)",
            "Delta",
            "Status",
            "% of total",
            "Running total",
        ]
    )
    for row in cumulative_rows:
        writer.writerow(
            [
                row.get("name"),
                row.get("count"),
                row.get("aoc_count"),
                row.get("annual_requirement"),
                row.get("annual_leave_credit"),
                row.get("leave_note"),
                row.get("leave_credit"),
                row.get("leave_days"),
                row.get("aoc_weeks"),
                row.get("aoc_credit"),
                row.get("target"),
                row.get("delta"),
                row.get("status_label"),
                row.get("pct"),
                row.get("running") if row.get("running") is not None else "",
            ]
        )
    writer.writerow(
        [
            "Total (all managers)",
            ctx.get("grand_total"),
            ctx.get("aoc_grand_total"),
            *[""] * 10,
            100 if ctx.get("grand_total") else "",
            ctx.get("grand_total"),
        ]
    )

    if period_rows and bucket_labels:
        writer.writerow([])
        writer.writerow(["Shifts by manager and period"])
        writer.writerow(["Manager"] + bucket_labels + ["Total"])
        for row in period_rows:
            counts = cast(list[int], row.get("counts") or [])
            writer.writerow(
                [row.get("name")]
                + [c if c else "" for c in counts]
                + [row.get("total")]
            )

    writer.writerow([])
    writer.writerow(["Detail (one row per staffed unit assignment)"])
    writer.writerow(
        [
            "Shift date",
            "Manager",
            "Legacy label",
            "Role",
            "Base",
            "RW/GR",
            "D/N",
            "Unit",
            "OT",
            "Source value",
            "Week start",
            "Source tab",
            "Source cell",
        ]
    )
    for row in shift_rows:
        writer.writerow(
            [
                row.get("shift_date"),
                row.get("person_display"),
                row.get("raw_person_display") or "",
                row.get("role"),
                row.get("base_name"),
                row.get("service_type"),
                row.get("day_night"),
                row.get("unit_code"),
                "Yes" if row.get("overtime") else "",
                row.get("raw_value"),
                row.get("week_start"),
                row.get("source_tab"),
                row.get("source_cell"),
            ]
        )

    aoc_rows = cast(
        list[dict[str, object]], ctx.get("all_aoc_rows") or ctx.get("aoc_rows") or []
    )
    if aoc_rows:
        writer.writerow([])
        writer.writerow(["AOC detail (one row per AOC cell on manager roster rows)"])
        writer.writerow(
            [
                "Date",
                "Manager",
                "Legacy label",
                "Role",
                "Source value",
                "Week start",
                "Source tab",
                "Source cell",
            ]
        )
        for row in aoc_rows:
            writer.writerow(
                [
                    row.get("shift_date"),
                    row.get("person_display"),
                    row.get("raw_person_display") or "",
                    row.get("role"),
                    row.get("raw_value"),
                    row.get("week_start"),
                    row.get("source_tab"),
                    row.get("source_cell"),
                ]
            )

    filename = (
        f"manager_shifts_{ctx.get('granularity')}_"
        f"{ctx.get('date_start')}_to_{ctx.get('date_end')}.csv"
    )
    response = HttpResponse(
        output.getvalue().encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def manager_shifts_export_xlsx(request):
    """Export manager shift summary and detail as Excel workbook."""
    ctx = _build_manager_shifts_context(request)
    cumulative_rows = cast(list[dict[str, object]], ctx.get("cumulative_rows") or [])
    period_rows = cast(list[dict[str, object]], ctx.get("period_table_rows") or [])
    bucket_labels = cast(list[str], ctx.get("bucket_labels") or [])
    shift_rows = cast(
        list[dict[str, object]], ctx.get("all_shifts") or ctx.get("shifts") or []
    )

    wb = Workbook()
    ws_meta = wb.active
    ws_meta.title = "Metadata"
    ws_meta.append(["Key", "Value"])
    ws_meta.append(["Generated (UTC)", _utc_now_iso()])
    ws_meta.append(["Report", "Manager line shifts"])
    ws_meta.append(["FY label", f"FY{ctx.get('fy_label')}"])
    ws_meta.append(["PP#1 week-1 Sunday (FY start)", ctx.get("fy_start")])
    ws_meta.append(["FY end (inclusive)", ctx.get("fy_end")])
    ws_meta.append(["FY / pay period policy", FY_AND_PAY_PERIOD_POLICY_NOTE])
    ws_meta.append(["Minimum shifts per person per FY", ctx.get("manager_min_fy")])
    ws_meta.append(
        ["Minimum shifts per pay period (policy)", ctx.get("manager_min_per_pp")]
    )
    ws_meta.append(
        ["AOC credit (AOC days per shift)", ctx.get("manager_aoc_days_per_credit")]
    )
    ws_meta.append(["LT credit source", MANAGER_LEAVE_CREDIT_NOTE])
    ws_meta.append(["Granularity", ctx.get("granularity")])
    ws_meta.append(["Date start", ctx.get("date_start")])
    ws_meta.append(["Date end", ctx.get("date_end")])
    ws_meta.append(["Data through (FY YTD default)", ctx.get("data_through")])
    ws_meta.append(["Prorated minimum (per person)", ctx.get("prorated_manager_min")])
    ws_meta.append(["FY target window start", ctx.get("fy_target_start")])
    ws_meta.append(["FY target window end", ctx.get("fy_target_end")])
    ws_meta.append(["Total person-shifts", ctx.get("grand_total")])
    ws_meta.append(["Total AOC days", ctx.get("aoc_grand_total")])

    ws_summary = wb.create_sheet("Summary", 1)
    ws_summary.append(
        [
            "Manager (last name)",
            "Shifts",
            "AOC days",
            "Annual req.",
            "LT credit (annual, manual)",
            "LT credit note",
            "LT credit applied",
            "LT days on schedule",
            "AOC weeks (days / 7)",
            "AOC credit shifts",
            "Min (prorated)",
            "Delta",
            "Status",
            "% of total",
            "Running total",
        ]
    )
    for row in cumulative_rows:
        ws_summary.append(
            [
                row.get("name"),
                row.get("count"),
                row.get("aoc_count"),
                row.get("annual_requirement"),
                row.get("annual_leave_credit"),
                row.get("leave_note"),
                row.get("leave_credit"),
                row.get("leave_days"),
                row.get("aoc_weeks"),
                row.get("aoc_credit"),
                row.get("target"),
                row.get("delta"),
                row.get("status_label"),
                row.get("pct"),
                row.get("running"),
            ]
        )
    ws_summary.append(
        [
            "Total (all managers)",
            ctx.get("grand_total"),
            ctx.get("aoc_grand_total"),
            *[None] * 10,
            100 if ctx.get("grand_total") else None,
            ctx.get("grand_total"),
        ]
    )

    if period_rows and bucket_labels:
        ws_period = wb.create_sheet("By period")
        ws_period.append(["Manager"] + bucket_labels + ["Total"])
        for row in period_rows:
            counts = cast(list[int], row.get("counts") or [])
            ws_period.append([row.get("name")] + list(counts) + [row.get("total")])

    ws_detail = wb.create_sheet("Detail")
    write_manager_line_shift_sheet(ws_detail, shift_rows, include_week_start=True)

    aoc_rows = cast(
        list[dict[str, object]], ctx.get("all_aoc_rows") or ctx.get("aoc_rows") or []
    )
    if aoc_rows:
        ws_aoc = wb.create_sheet("AOC detail")
        write_manager_aoc_sheet(ws_aoc, aoc_rows, include_week_start=True)

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    filename = (
        f"manager_shifts_{ctx.get('granularity')}_"
        f"{ctx.get('date_start')}_to_{ctx.get('date_end')}.xlsx"
    )
    response = HttpResponse(
        out.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
