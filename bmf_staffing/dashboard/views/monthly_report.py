"""Monthly report download view."""

import os
from datetime import date, timedelta

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from staffing_tool.monthly_report import export_monthly_report

from .helpers import DB_PATH, _ensure_db, _resolve_output_dir


def _default_previous_calendar_month():
    """First and last day of the previous calendar month (ISO dates)."""
    today = date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return first_prev.isoformat(), last_prev.isoformat()


def monthly_report(request):
    """Pick a date range and download a BMF-styled monthly Excel aggregate."""
    _ensure_db()
    default_start, default_end = _default_previous_calendar_month()
    if not DB_PATH:
        messages.error(request, "Database is not configured (STAFFING_DB_PATH).")
        return redirect("home")

    if request.method == "POST":
        start = (request.POST.get("date_start") or "").strip()
        end = (request.POST.get("date_end") or "").strip()
        try:
            path = export_monthly_report(
                DB_PATH, start, end, output_dir=_resolve_output_dir()
            )
            if not path or not os.path.isfile(path):
                raise Http404("Export file not found")
            return FileResponse(
                open(path, "rb"), as_attachment=True, filename=os.path.basename(path)
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        except Http404:
            raise
        except Exception as exc:
            messages.error(request, f"Export failed: {exc}")
        return render(
            request,
            "dashboard/monthly_report.html",
            {
                "date_start": start or default_start,
                "date_end": end or default_end,
            },
        )

    return render(
        request,
        "dashboard/monthly_report.html",
        {
            "date_start": default_start,
            "date_end": default_end,
        },
    )
