"""Monthly report download view."""

import calendar
from datetime import date, timedelta

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from staffing_tool.db import session_scope
from staffing_tool.models import WeeklyStaffing
from staffing_tool.monthly_report import export_monthly_report

from .helpers import DB_PATH, _ensure_db, _resolve_output_dir, serve_download


def _default_previous_calendar_month():
    """First and last day of the previous calendar month (ISO dates)."""
    today = date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return first_prev.isoformat(), last_prev.isoformat()


def _months_with_data(db_path: str) -> list[dict[str, str]]:
    """Calendar months holding at least one week_start, newest first.

    Feeds the month picker; a week belongs to the month its Sunday falls in,
    the same rule the export applies to the date range.
    """
    with session_scope(db_path) as session:
        week_starts = [r[0] for r in session.query(WeeklyStaffing.week_start).all()]
    months = sorted({ws[:7] for ws in week_starts}, reverse=True)
    out = []
    for ym in months:
        year, month = int(ym[:4]), int(ym[5:7])
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        out.append(
            {
                "label": first.strftime("%B %Y"),
                "date_start": first.isoformat(),
                "date_end": last.isoformat(),
            }
        )
    return out


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
        fmt = (request.POST.get("format") or "xlsx").strip().lower()
        try:
            if fmt == "html":
                from staffing_tool.monthly_html_report import (
                    export_monthly_report_html,
                )

                path = export_monthly_report_html(
                    DB_PATH, start, end, _resolve_output_dir()
                )
                content_type = "text/html; charset=utf-8"
            elif fmt == "pdf":
                from staffing_tool.monthly_pdf_report import export_monthly_report_pdf

                path = export_monthly_report_pdf(
                    DB_PATH, start, end, _resolve_output_dir()
                )
                content_type = "application/pdf"
            else:
                path = export_monthly_report(
                    DB_PATH, start, end, output_dir=_resolve_output_dir()
                )
                content_type = None
            return serve_download(path, content_type)
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
                "months": _months_with_data(DB_PATH),
            },
        )

    return render(
        request,
        "dashboard/monthly_report.html",
        {
            "date_start": default_start,
            "date_end": default_end,
            "months": _months_with_data(DB_PATH),
        },
    )
