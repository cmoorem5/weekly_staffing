"""Reports landing page — entry point for analytics and exports."""

from datetime import date

from django.shortcuts import render
from staffing_tool.fiscal_year import (
    fy_end_date,
    fy_label_year,
    fy_week1_sunday_containing,
    pay_periods_for_fy,
)

from .dashboard_filters import (
    last_closed_pay_period_end_for_fy,
    serialize_filters_query_from_parts,
)
from .helpers import DB_PATH, FY_AND_PAY_PERIOD_POLICY_NOTE, _ensure_db, staffing_db_snapshot


def _report_card_qs(**parts: str) -> str:
    return serialize_filters_query_from_parts(parts)


def reports_index(request):
    """Hub linking to staffing analytics, manager tracking, and board exports."""
    _ensure_db()
    today = date.today()
    fy_start = fy_week1_sunday_containing(today)
    fy_end = fy_end_date(fy_start)
    fy_label = fy_label_year(fy_start)
    end_anchor = last_closed_pay_period_end_for_fy(today, fy_start)

    latest_week_start = None
    latest_updated_at = None
    last_import_week_start = None
    last_import_updated_at = None
    if DB_PATH:
        snap = staffing_db_snapshot(DB_PATH)
        latest_week_start = snap["latest_week_start"]
        latest_updated_at = snap["latest_updated_at"]
        last_import_week_start = snap["last_import_week_start"]
        last_import_updated_at = snap["last_import_updated_at"]

    fy_ytd_parts = {
        "fy": str(fy_label),
        "granularity": "pay_period",
        "date_start": fy_start.isoformat(),
        "date_end": end_anchor.isoformat(),
    }
    mgr_ytd_parts = dict(fy_ytd_parts)

    report_cards = [
        {
            "title": "Staffing dashboard",
            "description": (
                "FY trends for staffing rate, OT dependency, shift exceptions, "
                "RW/GR coverage, and manager line-shift counts by pay period, month, or quarter."
            ),
            "open_url_name": "staffing_dashboard",
            "open_qs": _report_card_qs(**fy_ytd_parts),
            "exports": [
                {
                    "label": "Export CSV",
                    "url_name": "staffing_dashboard_export_csv",
                    "qs": _report_card_qs(**fy_ytd_parts),
                },
                {
                    "label": "Export Excel",
                    "url_name": "staffing_dashboard_export_xlsx",
                    "qs": _report_card_qs(**fy_ytd_parts),
                },
            ],
        },
        {
            "title": "Manager line shifts",
            "description": (
                "Per-manager FY shift counts vs the 52-shift annual minimum, "
                "AOC day totals, period breakdown, and progress charts."
            ),
            "open_url_name": "manager_shifts",
            "open_qs": _report_card_qs(**mgr_ytd_parts),
            "exports": [
                {
                    "label": "Export CSV",
                    "url_name": "manager_shifts_export_csv",
                    "qs": _report_card_qs(**mgr_ytd_parts),
                },
                {
                    "label": "Export Excel",
                    "url_name": "manager_shifts_export_xlsx",
                    "qs": _report_card_qs(**mgr_ytd_parts),
                },
            ],
        },
        {
            "title": "Weekly staffing report",
            "description": (
                "Polished PDF and HTML email summary for one week — KPIs, 8-week trend, "
                "exception breakdown (AT/LT/SICK/LOA/JURY/BREV), and base coverage from staffing.db."
            ),
            "open_url_name": "weekly_staffing_report",
            "open_qs": "",
            "exports": [],
        },
        {
            "title": "Quarterly staffing report",
            "description": (
                "Fiscal-quarter PDF — weekly trend, exception breakdown (AT/LT/SICK/LOA/JURY/BREV), "
                "period volumes, base coverage, and week-by-week detail from staffing.db."
            ),
            "open_url_name": "quarterly_staffing_report",
            "open_qs": "",
            "exports": [],
        },
        {
            "title": "Monthly board report",
            "description": (
                "Aggregate selected weeks into the Boston MedFlight monthly Excel layout "
                "(Board Summary + Weekly Detail)."
            ),
            "open_url_name": "monthly_report",
            "open_qs": "",
            "exports": [],
        },
    ]

    return render(
        request,
        "dashboard/reports.html",
        {
            "fy_label": fy_label,
            "fy_start": fy_start.isoformat(),
            "fy_end": fy_end.isoformat(),
            "data_through": end_anchor.isoformat(),
            "latest_week_start": latest_week_start,
            "latest_updated_at": latest_updated_at,
            "last_import_week_start": last_import_week_start,
            "last_import_updated_at": last_import_updated_at,
            "report_cards": report_cards,
            "fy_policy_note": FY_AND_PAY_PERIOD_POLICY_NOTE,
            "pp_count": len(pay_periods_for_fy(fy_start)),
        },
    )
