"""Roster-wide staff exception summary: shifts, OT, and leave/exceptions by person."""

import csv
import io
from datetime import date, timedelta

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from staffing_tool.exception_summary import (
    LEAVE_DISPLAY_COLUMNS,
    load_clinical_exception_summary,
    load_manager_exception_summary,
)
from staffing_tool.fiscal_year import fy_end_date, fy_label_year
from staffing_tool.timeutil import utc_now_iso as _utc_now_iso

from .dashboard_filters import parse_date_param, parse_fy_week1_from_request
from .helpers import DB_PATH, _ensure_db

DEFAULT_WEEKS_BACK = 4


def _default_date_range(today: date) -> tuple[date, date]:
    """Last four calendar weeks ending today."""
    end = today
    start = end - timedelta(days=DEFAULT_WEEKS_BACK * 7 - 1)
    return start, end


def _build_staff_exceptions_context(request) -> dict[str, object]:
    _ensure_db()
    if not DB_PATH:
        raise Http404("Database is not configured (STAFFING_DB_PATH).")

    today = date.today()
    default_start, default_end = _default_date_range(today)
    fy_start = parse_fy_week1_from_request(request, today)
    fy_end = fy_end_date(fy_start)
    fy_label = fy_label_year(fy_start)
    date_start = parse_date_param(request.GET.get("date_start", ""), default_start)
    date_end = parse_date_param(request.GET.get("date_end", ""), default_end)
    if date_start > date_end:
        date_start, date_end = default_start, default_end

    role_filter = (request.GET.get("role") or "").strip().upper()
    if role_filter not in {"", "RN", "MEDIC", "EMT"}:
        role_filter = ""

    clinical_summaries = load_clinical_exception_summary(
        DB_PATH, date_start, date_end, role=role_filter or None
    )
    # Managers aren't on the RN/Medic/EMT roster, so a role filter has
    # nothing to match them against — show them only on the "all roles" view.
    manager_summaries = (
        []
        if role_filter
        else load_manager_exception_summary(DB_PATH, date_start, date_end)
    )

    clinical_rows = [
        {
            "person_display": row.person_display,
            "role": row.role,
            "staffed_count": row.staffed_count,
            "ot_count": row.ot_count,
            # Aligned to leave_columns order so the template can zip them.
            "leave_values": [
                row.leave_counts.get(col, 0) for col in LEAVE_DISPLAY_COLUMNS
            ],
            "admin_other_count": row.admin_other_count,
            "total_exceptions": row.total_exceptions,
        }
        for row in clinical_summaries
    ]
    manager_rows = [
        {
            "person_display": row.person_display,
            "staffed_count": row.staffed_count,
            "ot_count": row.ot_count,
            "admin_other_count": row.admin_other_count,
        }
        for row in manager_summaries
    ]

    return {
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "role_filter": role_filter,
        "role_choices": [
            ("", "All roles"),
            ("RN", "RN"),
            ("MEDIC", "Medic"),
            ("EMT", "EMT"),
        ],
        "clinical_rows": clinical_rows,
        "manager_rows": manager_rows,
        "leave_columns": LEAVE_DISPLAY_COLUMNS,
        "today_iso": today.isoformat(),
        "fy_label": fy_label,
        "fy_start": fy_start.isoformat(),
        "fy_end": fy_end.isoformat(),
        "default_weeks_back": DEFAULT_WEEKS_BACK,
    }


def staff_exceptions_report(request):
    """Roster-wide exception summary: total shifts, OT, and leave by person."""
    try:
        ctx = _build_staff_exceptions_context(request)
    except Http404 as exc:
        messages.error(request, str(exc))
        return redirect("home")
    return render(request, "dashboard/staff_exceptions.html", ctx)


def staff_exceptions_export_csv(request):
    """Export the roster-wide exception summary for the current filter selection."""
    ctx = _build_staff_exceptions_context(request)
    leave_columns = ctx["leave_columns"]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Metadata"])
    writer.writerow(["Generated (UTC)", _utc_now_iso()])
    writer.writerow(["Report", "Staff exception summary"])
    writer.writerow(["Date start", ctx.get("date_start")])
    writer.writerow(["Date end", ctx.get("date_end")])
    writer.writerow([])

    writer.writerow(
        ["Person", "Role", "Staffed shifts", "OT shifts"]
        + list(leave_columns)
        + ["Admin/Other", "Total exceptions"]
    )
    for row in ctx["clinical_rows"]:
        writer.writerow(
            [row["person_display"], row["role"], row["staffed_count"], row["ot_count"]]
            + row["leave_values"]
            + [row["admin_other_count"], row["total_exceptions"]]
        )

    if ctx["manager_rows"]:
        writer.writerow([])
        writer.writerow(["Managers (aggregate: line shifts, OT, AOC days)"])
        writer.writerow(["Person", "Line shifts", "OT shifts", "AOC days"])
        for row in ctx["manager_rows"]:
            writer.writerow(
                [
                    row["person_display"],
                    row["staffed_count"],
                    row["ot_count"],
                    row["admin_other_count"],
                ]
            )

    filename = f"staff_exceptions_{ctx.get('date_start')}_to_{ctx.get('date_end')}.csv"
    response = HttpResponse(
        output.getvalue().encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
