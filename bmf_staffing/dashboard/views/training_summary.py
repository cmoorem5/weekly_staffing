"""Training events summary: counts by role, filterable by period."""

import csv
import io
from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from sqlalchemy import func
from staffing_tool.db import session_scope
from staffing_tool.fiscal_year import (
    fy_end_date,
    fy_label_year,
    fy_week1_sunday_containing,
    pay_periods_for_fy,
)
from staffing_tool.models import WeeklyPersonShift
from staffing_tool.time_buckets import bucket_label, buckets_for_range
from staffing_tool.timeutil import utc_now_iso as _utc_now_iso

from .dashboard_filters import (
    fy_choice_rows,
    last_closed_pay_period_end_for_fy,
    parse_date_param,
    parse_fy_week1_from_request,
    serialize_filters_query_from_parts,
)
from .helpers import DB_PATH, _ensure_db

ROLES = ["RN", "MEDIC", "EMT"]


def _week_buckets(date_start: date, date_end: date) -> list[tuple[date, date]]:
    """One bucket per Sunday week_start overlapping the range -- native grain."""
    first_sun = date_start + timedelta(days=(6 - date_start.weekday()) % 7)
    buckets: list[tuple[date, date]] = []
    cur = first_sun
    while cur <= date_end:
        buckets.append((cur, cur + timedelta(days=6)))
        cur += timedelta(days=7)
    return buckets


def _build_training_summary_context(request) -> dict[str, object]:
    _ensure_db()
    if not DB_PATH:
        raise Http404("Database is not configured (STAFFING_DB_PATH).")

    today = date.today()
    fy_start = parse_fy_week1_from_request(request, today)
    fy_end = fy_end_date(fy_start)
    fy_label = fy_label_year(fy_start)
    fy_choices = fy_choice_rows(fy_label_year(fy_week1_sunday_containing(today)))

    granularity = (request.GET.get("granularity") or "pay_period").strip().lower()
    if granularity not in {"week", "pay_period", "month", "quarter"}:
        granularity = "pay_period"

    last_closed_in_fy = last_closed_pay_period_end_for_fy(today, fy_start)
    is_current_fy = fy_start == fy_week1_sunday_containing(today)
    default_end = last_closed_in_fy if is_current_fy else fy_end
    default_start = fy_start

    date_start = parse_date_param(request.GET.get("date_start", ""), default_start)
    date_end = parse_date_param(request.GET.get("date_end", ""), default_end)
    date_start = max(date_start, fy_start)
    date_end = min(date_end, fy_end)
    if date_start > date_end:
        date_start, date_end = default_start, default_end

    periods = pay_periods_for_fy(fy_start)
    end_anchor = default_end
    closed = [p for p in periods if p.end <= end_anchor]
    last6 = closed[-6:] if len(closed) >= 6 else closed
    last6_start = last6[0].start if last6 else fy_start
    last6_end = last6[-1].end if last6 else end_anchor
    preset_links = {
        "fy_ytd": {
            "label": "FY YTD (last closed PP)",
            "qs": serialize_filters_query_from_parts(
                {
                    "fy": str(fy_label),
                    "granularity": "pay_period",
                    "date_start": fy_start.isoformat(),
                    "date_end": end_anchor.isoformat(),
                }
            ),
        },
        "last_6_pp": {
            "label": "Last 6 pay periods",
            "qs": serialize_filters_query_from_parts(
                {
                    "fy": str(fy_label),
                    "granularity": "pay_period",
                    "date_start": last6_start.isoformat(),
                    "date_end": last6_end.isoformat(),
                }
            ),
        },
        "full_fy": {
            "label": "Full FY",
            "qs": serialize_filters_query_from_parts(
                {
                    "fy": str(fy_label),
                    "granularity": "quarter",
                    "date_start": fy_start.isoformat(),
                    "date_end": fy_end.isoformat(),
                }
            ),
        },
    }

    buckets = (
        _week_buckets(date_start, date_end)
        if granularity == "week"
        else buckets_for_range(granularity, date_start, date_end)
    )

    with session_scope(DB_PATH) as session:
        rows = (
            session.query(
                WeeklyPersonShift.week_start,
                WeeklyPersonShift.role,
                func.count(WeeklyPersonShift.id),
            )
            .filter(
                WeeklyPersonShift.event_type == "training",
                WeeklyPersonShift.shift_date >= date_start.isoformat(),
                WeeklyPersonShift.shift_date <= date_end.isoformat(),
            )
            .group_by(WeeklyPersonShift.week_start, WeeklyPersonShift.role)
            .all()
        )
        code_rows = (
            session.query(
                WeeklyPersonShift.raw_value,
                WeeklyPersonShift.role,
                func.count(WeeklyPersonShift.id),
            )
            .filter(
                WeeklyPersonShift.event_type == "training",
                WeeklyPersonShift.shift_date >= date_start.isoformat(),
                WeeklyPersonShift.shift_date <= date_end.isoformat(),
            )
            .group_by(WeeklyPersonShift.raw_value, WeeklyPersonShift.role)
            .all()
        )

    by_week_role: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for week_start, role, count in rows:
        by_week_role[str(week_start)][str(role)] += int(count or 0)

    weeks_by_date = [
        (date.fromisoformat(ws), role_counts)
        for ws, role_counts in by_week_role.items()
    ]

    table_rows: list[dict[str, object]] = []
    totals_by_role = dict.fromkeys(ROLES, 0)
    grand_total = 0
    for b_start, b_end in buckets:
        role_counts = dict.fromkeys(ROLES, 0)
        for ws_date, week_role_counts in weeks_by_date:
            if not (b_start <= ws_date <= b_end):
                continue
            for role, n in week_role_counts.items():
                if role in role_counts:
                    role_counts[role] += n

        bucket_total = sum(role_counts.values())
        for role in ROLES:
            totals_by_role[role] += role_counts[role]
        grand_total += bucket_total

        label = bucket_label(granularity, b_start, b_end, fy_week1=fy_start)
        table_rows.append(
            {
                "label": label,
                "bucket_start": b_start.isoformat(),
                "bucket_end": b_end.isoformat(),
                "rn_count": role_counts["RN"],
                "medic_count": role_counts["MEDIC"],
                "emt_count": role_counts["EMT"],
                "total_count": bucket_total,
            }
        )

    code_by_role: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for raw_value, role, count in code_rows:
        code_by_role[str(raw_value or "(blank)")][str(role)] += int(count or 0)
    code_breakdown_rows = sorted(
        (
            {
                "code": code,
                "rn_count": counts.get("RN", 0),
                "medic_count": counts.get("MEDIC", 0),
                "emt_count": counts.get("EMT", 0),
                "total_count": sum(counts.values()),
            }
            for code, counts in code_by_role.items()
        ),
        key=lambda r: (-r["total_count"], r["code"]),
    )

    return {
        "fy_label": fy_label,
        "fy_choices": fy_choices,
        "fy_start": fy_start.isoformat(),
        "fy_end": fy_end.isoformat(),
        "granularity": granularity,
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "data_through": default_end.isoformat(),
        "is_current_fy": is_current_fy,
        "table_rows": table_rows,
        "totals_by_role": totals_by_role,
        "grand_total": grand_total,
        "code_breakdown_rows": code_breakdown_rows,
        "preset_links": preset_links,
        "filters_qs": serialize_filters_query_from_parts(
            {
                "fy": str(fy_label),
                "granularity": granularity,
                "date_start": date_start.isoformat(),
                "date_end": date_end.isoformat(),
            }
        ),
        "today_iso": today.isoformat(),
    }


def training_summary_report(request):
    """Training event counts by role, filterable by week/pay period/month/quarter."""
    try:
        ctx = _build_training_summary_context(request)
    except Http404 as exc:
        messages.error(request, str(exc))
        return redirect("home")
    return render(request, "dashboard/training_summary.html", ctx)


def training_summary_export_csv(request):
    """Export the currently-selected training summary as CSV."""
    ctx = _build_training_summary_context(request)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Metadata"])
    writer.writerow(["Generated (UTC)", _utc_now_iso()])
    writer.writerow(["Report", "Training events summary"])
    writer.writerow(["FY label", f"FY{ctx.get('fy_label')}"])
    writer.writerow(["Granularity", ctx.get("granularity")])
    writer.writerow(["Date start", ctx.get("date_start")])
    writer.writerow(["Date end", ctx.get("date_end")])
    writer.writerow([])

    writer.writerow(
        ["Period", "Period start", "Period end", "RN", "Medic", "EMT", "Total"]
    )
    for row in ctx["table_rows"]:
        writer.writerow(
            [
                row["label"],
                row["bucket_start"],
                row["bucket_end"],
                row["rn_count"],
                row["medic_count"],
                row["emt_count"],
                row["total_count"],
            ]
        )
    totals = ctx["totals_by_role"]
    writer.writerow(
        [
            "Total",
            "",
            "",
            totals.get("RN", 0),
            totals.get("MEDIC", 0),
            totals.get("EMT", 0),
            ctx["grand_total"],
        ]
    )

    writer.writerow([])
    writer.writerow(["By training code (whole selected range)"])
    writer.writerow(["Code", "RN", "Medic", "EMT", "Total"])
    for row in ctx["code_breakdown_rows"]:
        writer.writerow(
            [
                row["code"],
                row["rn_count"],
                row["medic_count"],
                row["emt_count"],
                row["total_count"],
            ]
        )

    filename = f"training_summary_{ctx.get('granularity')}_{ctx.get('date_start')}_to_{ctx.get('date_end')}.csv"
    response = HttpResponse(
        output.getvalue().encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
