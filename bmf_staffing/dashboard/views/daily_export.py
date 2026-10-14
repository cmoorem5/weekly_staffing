"""Day-level research export (daily staffing, base coverage, shift detail)."""

import csv
import io
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import func
from staffing_tool.daily_export import (
    BY_BASE_HEADER,
    DAILY_HEADER,
    EXPORT_ROLES,
    NOTES,
    DailyExport,
    load_daily_export,
)
from staffing_tool.db import session_scope
from staffing_tool.fiscal_year import fy_end_date, fy_label_year
from staffing_tool.models import WeeklyPersonShift
from staffing_tool.timeutil import utc_now_iso as _utc_now_iso

from .dashboard_filters import parse_date_param, parse_fy_week1_from_request
from .helpers import DB_PATH, _ensure_db

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DEFAULT_WEEKS_BACK = 8
ROLE_CHOICES = [
    ("", "RN, Medic, EMT"),
    ("RN", "RN"),
    ("MEDIC", "Medic"),
    ("EMT", "EMT"),
]
DATASETS = {
    "daily": "Daily summary",
    "by_base": "By base",
    "shifts": "Shift detail",
}


def _params(request) -> dict:
    today = date.today()
    default_end = today
    default_start = today - timedelta(days=DEFAULT_WEEKS_BACK * 7 - 1)
    date_start = parse_date_param(request.GET.get("date_start", ""), default_start)
    date_end = parse_date_param(request.GET.get("date_end", ""), default_end)
    if date_start > date_end:
        date_start, date_end = default_start, default_end
    role = (request.GET.get("role") or "").strip().upper()
    if role not in EXPORT_ROLES:
        role = ""
    return {
        "date_start": date_start,
        "date_end": date_end,
        "role": role,
        "include_names": request.GET.get("include_names") == "1",
        "today": today,
    }


def _load(p: dict) -> DailyExport:
    return load_daily_export(
        DB_PATH,
        p["date_start"],
        p["date_end"],
        roles=(p["role"],) if p["role"] else EXPORT_ROLES,
        include_names=p["include_names"],
    )


def _qs(p: dict, **extra: str) -> str:
    parts = {
        "date_start": p["date_start"].isoformat(),
        "date_end": p["date_end"].isoformat(),
    }
    if p["role"]:
        parts["role"] = p["role"]
    if p["include_names"]:
        parts["include_names"] = "1"
    parts.update(extra)
    return urlencode(parts)


def daily_export_page(request):
    _ensure_db()
    if not DB_PATH:
        messages.error(request, "Database is not configured (STAFFING_DB_PATH).")
        return redirect("home")
    p = _params(request)
    fy_start = parse_fy_week1_from_request(request, p["today"])
    with session_scope(DB_PATH) as session:
        db_min, db_max = session.query(
            func.min(WeeklyPersonShift.shift_date),
            func.max(WeeklyPersonShift.shift_date),
        ).one()
    export = _load(p)
    return render(
        request,
        "dashboard/daily_export.html",
        {
            "date_start": p["date_start"].isoformat(),
            "date_end": p["date_end"].isoformat(),
            "role_filter": p["role"],
            "role_choices": ROLE_CHOICES,
            "include_names": p["include_names"],
            "export": export,
            "days": len(export.daily),
            "shift_count": len(export.shifts),
            "preview_rows": export.daily[:14],
            "daily_header": DAILY_HEADER,
            "db_date_min": db_min,
            "db_date_max": db_max,
            "fy_start": fy_start.isoformat(),
            "fy_end": fy_end_date(fy_start).isoformat(),
            "fy_label": fy_label_year(fy_start),
            "xlsx_qs": _qs(p),
            "csv_links": [
                (label, _qs(p, dataset=key)) for key, label in DATASETS.items()
            ],
            "notes": NOTES,
        },
    )


def _filename(p: dict, suffix: str, ext: str) -> str:
    return (
        f"daily_staffing{suffix}_{p['date_start'].isoformat()}"
        f"_to_{p['date_end'].isoformat()}.{ext}"
    )


def daily_export_csv(request):
    _ensure_db()
    if not DB_PATH:
        raise Http404("Database is not configured (STAFFING_DB_PATH).")
    p = _params(request)
    export = _load(p)
    dataset = request.GET.get("dataset", "daily")
    if dataset not in DATASETS:
        dataset = "daily"
    header, rows = {
        "daily": (DAILY_HEADER, export.daily),
        "by_base": (BY_BASE_HEADER, export.by_base),
        "shifts": (export.shift_header, export.shifts),
    }[dataset]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    response = HttpResponse(
        out.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8"
    )
    suffix = "" if dataset == "daily" else f"_{dataset}"
    response["Content-Disposition"] = (
        f'attachment; filename="{_filename(p, suffix, "csv")}"'
    )
    return response


def daily_export_xlsx(request):
    _ensure_db()
    if not DB_PATH:
        raise Http404("Database is not configured (STAFFING_DB_PATH).")
    p = _params(request)
    export = _load(p)
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily"
    sheets = [
        (ws, DAILY_HEADER, export.daily),
        (wb.create_sheet("By base"), BY_BASE_HEADER, export.by_base),
        (wb.create_sheet("Shift detail"), export.shift_header, export.shifts),
    ]
    for sheet, header, rows in sheets:
        sheet.append(header)
        for row in rows:
            sheet.append(row)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for col in sheet.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            sheet.column_dimensions[col[0].column_letter].width = min(width + 2, 40)
    notes = wb.create_sheet("Notes")
    notes.append(["Note"])
    notes["A1"].font = Font(bold=True)
    for line in NOTES:
        notes.append([line])
    notes.append(
        [
            f"Range {export.date_start} to {export.date_end}. Generated (UTC) {_utc_now_iso()}"
        ]
    )
    notes.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    response = HttpResponse(buf.getvalue(), content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = (
        f'attachment; filename="{_filename(p, "", "xlsx")}"'
    )
    return response
