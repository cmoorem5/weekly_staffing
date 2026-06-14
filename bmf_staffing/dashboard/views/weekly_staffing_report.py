"""Weekly staffing PDF + HTML report download."""

import os

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

from .helpers import DB_PATH, _ensure_db, _resolve_output_dir


def _pdf_exports():
    """Lazy import so Django starts even if reportlab/matplotlib are not installed yet."""
    from staffing_tool.weekly_pdf_report import (
        export_weekly_staffing_html,
        export_weekly_staffing_pdf,
        list_week_starts,
    )

    return export_weekly_staffing_html, export_weekly_staffing_pdf, list_week_starts


def _serve_weekly_export(week_start: str, fmt: str) -> FileResponse:
    export_html, export_pdf, list_week_starts = _pdf_exports()
    weeks = list_week_starts(DB_PATH)
    if week_start not in weeks:
        raise Http404(f"Week {week_start} is not in the database.")

    output_dir = _resolve_output_dir()
    if fmt == "html":
        path = export_html(DB_PATH, week_start, output_dir)
        content_type = "text/html; charset=utf-8"
    else:
        path = export_pdf(DB_PATH, week_start, output_dir)
        content_type = "application/pdf"
    if not path or not os.path.isfile(path):
        raise Http404("Export file not found")
    return FileResponse(
        open(path, "rb"),
        as_attachment=True,
        filename=os.path.basename(path),
        content_type=content_type,
    )


def weekly_report_download_pdf(request, week_start: str):
    """Direct PDF download for a week (e.g. from week edit after import)."""
    _ensure_db()
    if not DB_PATH:
        raise Http404("Database not configured")
    try:
        return _serve_weekly_export(week_start, "pdf")
    except ImportError as exc:
        raise Http404("PDF dependencies not installed") from exc
    except ValueError as exc:
        raise Http404(str(exc)) from exc


def weekly_report_download_html(request, week_start: str):
    """Direct HTML download for a week (e.g. from week edit after import)."""
    _ensure_db()
    if not DB_PATH:
        raise Http404("Database not configured")
    try:
        return _serve_weekly_export(week_start, "html")
    except ImportError as exc:
        raise Http404("PDF dependencies not installed") from exc
    except ValueError as exc:
        raise Http404(str(exc)) from exc


def weekly_staffing_report(request):
    """Pick a week from staffing.db and download PDF or HTML."""
    _ensure_db()
    if not DB_PATH:
        messages.error(request, "Database is not configured (STAFFING_DB_PATH).")
        return redirect("home")

    try:
        export_html, export_pdf, list_week_starts = _pdf_exports()
    except ImportError:
        messages.error(
            request,
            "PDF report dependencies missing. Run: pip install reportlab matplotlib",
        )
        return redirect("reports_index")

    weeks = list_week_starts(DB_PATH)
    default_week = weeks[0] if weeks else ""

    if request.method == "POST":
        week_start = (request.POST.get("week_start") or "").strip()
        fmt = (request.POST.get("format") or "pdf").strip().lower()
        if not week_start:
            messages.error(request, "Select a week.")
            return redirect("weekly_staffing_report")
        if week_start not in weeks:
            messages.error(request, f"Week {week_start} is not in the database.")
            return redirect("weekly_staffing_report")

        try:
            return _serve_weekly_export(week_start, fmt)
        except ValueError as exc:
            messages.error(request, str(exc))
        except Http404:
            raise
        except Exception as exc:
            messages.error(request, f"Export failed: {exc}")
        return redirect("weekly_staffing_report")

    return render(
        request,
        "dashboard/weekly_staffing_report.html",
        {
            "weeks": weeks,
            "selected_week": default_week,
        },
    )
