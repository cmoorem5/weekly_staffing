"""Hours / payroll report views: on-screen report + CSV exports for ADP."""

from __future__ import annotations

import csv
import datetime as dt

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from ..payroll import build_hours_report, detail_csv_rows, summary_csv_rows
from .helpers import local_today


def _parse_range(request) -> tuple[dt.date, dt.date, str]:
    """Start/end/person from query params; defaults to month-to-date."""
    today = local_today()
    default_start = today.replace(day=1)

    def _date(param: str, fallback: dt.date) -> dt.date:
        try:
            return dt.date.fromisoformat(request.GET.get(param, ""))
        except ValueError:
            return fallback

    start = _date("start", default_start)
    end = _date("end", today)
    if end < start:
        start, end = end, start
    person = request.GET.get("person", "").strip()
    return start, end, person


@login_required
def hours_report(request):
    start, end, person = _parse_range(request)
    report = build_hours_report(start, end, person)
    return render(
        request,
        "crew_hub/hours_report.html",
        {
            "report": report,
            "start": start,
            "end": end,
            "person": person,
        },
    )


@login_required
def hours_report_csv(request):
    start, end, person = _parse_range(request)
    report = build_hours_report(start, end, person)
    kind = request.GET.get("kind", "summary")
    rows = detail_csv_rows(report) if kind == "detail" else summary_csv_rows(report)

    filename = f"crew_hub_hours_{kind}_{start:%Y%m%d}-{end:%Y%m%d}.csv"
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerows(rows)
    return response
