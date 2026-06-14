"""Quarterly staffing PDF report download."""

import os

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

from .helpers import DB_PATH, _ensure_db, _resolve_output_dir


def _pdf_exports():
    from staffing_tool.quarterly_pdf_report import (
        export_quarterly_staffing_pdf,
        list_fiscal_quarters,
    )

    return export_quarterly_staffing_pdf, list_fiscal_quarters


def quarterly_staffing_report(request):
    """Pick a fiscal quarter from staffing.db and download PDF."""
    _ensure_db()
    if not DB_PATH:
        messages.error(request, "Database is not configured (STAFFING_DB_PATH).")
        return redirect("home")

    try:
        export_pdf, list_fiscal_quarters = _pdf_exports()
    except ImportError:
        messages.error(
            request,
            "PDF report dependencies missing. Run: pip install reportlab matplotlib",
        )
        return redirect("reports_index")

    quarters = list_fiscal_quarters(DB_PATH)
    default_fy = quarters[0]["fy_label_year"] if quarters else ""
    default_q = quarters[0]["quarter"] if quarters else ""

    if request.method == "POST":
        fy_raw = (request.POST.get("fy_label_year") or "").strip()
        q_raw = (request.POST.get("quarter") or "").strip()
        try:
            fy_label_year = int(fy_raw)
            quarter = int(q_raw)
        except ValueError:
            messages.error(request, "Select a valid fiscal year and quarter.")
            return redirect("quarterly_staffing_report")

        valid = any(
            q["fy_label_year"] == fy_label_year and q["quarter"] == quarter
            for q in quarters
        )
        if not valid:
            messages.error(
                request, f"FY{fy_label_year} Q{quarter} has no data in the database."
            )
            return redirect("quarterly_staffing_report")

        output_dir = _resolve_output_dir()
        try:
            path = export_pdf(DB_PATH, fy_label_year, quarter, output_dir)
            if not path or not os.path.isfile(path):
                raise Http404("Export file not found")
            return FileResponse(
                open(path, "rb"),
                as_attachment=True,
                filename=os.path.basename(path),
                content_type="application/pdf",
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        except Http404:
            raise
        except Exception as exc:
            messages.error(request, f"Export failed: {exc}")
        return redirect("quarterly_staffing_report")

    return render(
        request,
        "dashboard/quarterly_staffing_report.html",
        {
            "quarters": quarters,
            "selected_fy": default_fy,
            "selected_quarter": default_q,
        },
    )
