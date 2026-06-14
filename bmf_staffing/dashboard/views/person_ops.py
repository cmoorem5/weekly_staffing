"""Person-level ops report (staff detail from schedule imports)."""

import csv
import io
from datetime import date, timedelta

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from sqlalchemy import func
from staffing_tool.db import session_scope
from staffing_tool.fiscal_year import fy_end_date, fy_label_year
from staffing_tool.models import WeeklyPersonShift
from staffing_tool.person_names import person_sort_key
from staffing_tool.person_ops import (
    list_staff_roster_persons,
    load_person_ops_detail,
    load_person_ops_summary,
)

from .dashboard_filters import parse_date_param, parse_fy_week1_from_request
from .helpers import DB_PATH, _ensure_db, _utc_now_iso

DEFAULT_WEEKS_BACK = 4


def _default_date_range(today: date) -> tuple[date, date]:
    """Last four calendar weeks ending today."""
    end = today
    start = end - timedelta(days=DEFAULT_WEEKS_BACK * 7 - 1)
    return start, end


def _serialize_person_ops_query(
    *,
    person: str,
    date_start: date,
    date_end: date,
    fy_label: int | None = None,
    role: str = "",
) -> str:
    from urllib.parse import urlencode

    parts: dict[str, str] = {
        "person": person,
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
    }
    if fy_label is not None:
        parts["fy"] = str(fy_label)
    if role:
        parts["role"] = role
    return urlencode(parts)


def _build_person_ops_context(request) -> dict[str, object]:
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

    roster_pairs = list_staff_roster_persons(DB_PATH, role=role_filter or None)
    person_options = [name for name, _role in roster_pairs]
    person_option_groups: list[dict[str, object]] = []
    by_letter: dict[str, list[str]] = {}
    for name in person_options:
        letter = name[0].upper() if name else "#"
        by_letter.setdefault(letter, []).append(name)
    for letter in sorted(by_letter.keys()):
        person_option_groups.append({"letter": letter, "names": by_letter[letter]})
    selected_person = (request.GET.get("person") or "").strip()
    if selected_person and selected_person not in person_options:
        person_options = sorted(
            set(person_options) | {selected_person},
            key=person_sort_key,
        )
        person_option_groups = [
            {
                "letter": selected_person[0].upper(),
                "names": person_options,
            }
        ]
    if not selected_person and person_options:
        selected_person = person_options[0]

    summary = None
    detail_rows: list[dict[str, object]] = []
    if selected_person:
        summary = load_person_ops_summary(
            DB_PATH,
            selected_person,
            date_start,
            date_end,
            role=role_filter or None,
        )
        for row in load_person_ops_detail(
            DB_PATH,
            selected_person,
            date_start,
            date_end,
            role=role_filter or None,
        ):
            event_label = row.event_type.upper()
            if row.event_type == "leave":
                event_label = row.leave_type or "Leave"
            elif row.event_type == "ot":
                event_label = "OT"
            elif row.event_type == "staffed":
                event_label = "Staffed"
            detail_rows.append(
                {
                    "shift_date": row.shift_date,
                    "week_start": row.week_start,
                    "role": row.role,
                    "event_type": row.event_type,
                    "event_label": event_label,
                    "base_name": row.base_name,
                    "service_type": row.service_type,
                    "day_night": row.day_night,
                    "unit_code": row.unit_code,
                    "leave_type": row.leave_type or "",
                    "overtime": row.overtime,
                    "raw_value": row.raw_value,
                    "source_tab": row.source_tab,
                    "source_cell": row.source_cell,
                }
            )

    db_min = db_max = None
    with session_scope(DB_PATH) as session:
        db_min, db_max = session.query(
            func.min(WeeklyPersonShift.shift_date),
            func.max(WeeklyPersonShift.shift_date),
        ).one()

    filters_qs = _serialize_person_ops_query(
        person=selected_person,
        date_start=date_start,
        date_end=date_end,
        fy_label=fy_label,
        role=role_filter,
    )

    return {
        "person_options": person_options,
        "person_option_groups": person_option_groups,
        "selected_person": selected_person,
        "role_filter": role_filter,
        "role_choices": [
            ("", "All roles"),
            ("RN", "RN"),
            ("MEDIC", "Medic"),
            ("EMT", "EMT"),
        ],
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "summary": summary,
        "detail_rows": detail_rows,
        "detail_count": len(detail_rows),
        "today_iso": today.isoformat(),
        "fy_label": fy_label,
        "fy_start": fy_start.isoformat(),
        "fy_end": fy_end.isoformat(),
        "db_date_min": db_min,
        "db_date_max": db_max,
        "filters_qs": filters_qs,
        "default_weeks_back": DEFAULT_WEEKS_BACK,
    }


def person_ops_report(request):
    """Ops-only person detail: RW/GR mix, shifts, exceptions, OT."""
    try:
        ctx = _build_person_ops_context(request)
    except Http404 as exc:
        messages.error(request, str(exc))
        return redirect("home")
    return render(request, "dashboard/person_ops.html", ctx)


def person_ops_export_csv(request):
    """Export person ops summary and detail for the current filter selection."""
    ctx = _build_person_ops_context(request)
    summary = ctx.get("summary")
    detail_rows = ctx.get("detail_rows") or []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Metadata"])
    writer.writerow(["Generated (UTC)", _utc_now_iso()])
    writer.writerow(["Report", "Staff ops report (person detail)"])
    writer.writerow(["Person", ctx.get("selected_person")])
    writer.writerow(["Date start", ctx.get("date_start")])
    writer.writerow(["Date end", ctx.get("date_end")])
    writer.writerow([])

    writer.writerow(["Summary"])
    if summary:
        writer.writerow(["Staffed shifts", summary.staffed_count])
        writer.writerow(["RW staffed", summary.rw_count])
        writer.writerow(["GR staffed", summary.gr_count])
        writer.writerow(["RW %", summary.rw_pct if summary.rw_pct is not None else "—"])
        writer.writerow(["OT shifts", summary.ot_count])
        writer.writerow(["Leave / exception total", summary.leave_total])
        for lt, n in (summary.leave_counts or {}).items():
            writer.writerow([f"  {lt}", n])
    else:
        writer.writerow(["(no person selected)"])

    writer.writerow([])
    writer.writerow(["Detail"])
    writer.writerow(
        [
            "Shift date",
            "Week start",
            "Role",
            "Event",
            "Base",
            "RW/GR",
            "D/N",
            "Unit",
            "Leave type",
            "OT",
            "Source value",
            "Source tab",
            "Source cell",
        ]
    )
    for row in detail_rows:
        writer.writerow(
            [
                row.get("shift_date"),
                row.get("week_start"),
                row.get("role"),
                row.get("event_label"),
                row.get("base_name"),
                row.get("service_type"),
                row.get("day_night"),
                row.get("unit_code"),
                row.get("leave_type"),
                "Yes" if row.get("overtime") else "",
                row.get("raw_value"),
                row.get("source_tab"),
                row.get("source_cell"),
            ]
        )

    person_slug = (ctx.get("selected_person") or "person").replace(",", "")
    filename = (
        f"staff_ops_{person_slug}_{ctx.get('date_start')}_to_{ctx.get('date_end')}.csv"
    )
    response = HttpResponse(
        output.getvalue().encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
