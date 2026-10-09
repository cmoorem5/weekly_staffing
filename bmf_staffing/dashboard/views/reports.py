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
from .helpers import (
    DB_PATH,
    FY_AND_PAY_PERIOD_POLICY_NOTE,
    _ensure_db,
    staffing_db_snapshot,
)


def _report_card_qs(**parts: str) -> str:
    return serialize_filters_query_from_parts(parts)


def reports_index(request):
    """Hub linking to leadership reports, staffing analytics, and manager tracking."""
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
            "group": "analytics",
            "title": "Staffing dashboard",
            "description": (
                "FY trends for staffing rate, OT dependency, shift exceptions, "
                "RW/GR coverage (system and per-base), role fill, and manager line-shift "
                "counts by week, pay period, month, or quarter — CSV/Excel export for "
                "research and analysis."
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
            "group": "analytics",
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
            "group": "leadership",
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
            "group": "leadership",
            "title": "Monthly staffing report",
            "description": (
                "One month (or any date range) as an HTML/PDF summary with change vs the "
                "prior period, or the full Excel workbook with weekly, base, and exception detail."
            ),
            "open_url_name": "monthly_report",
            "open_qs": "",
            "exports": [],
        },
        {
            "group": "leadership",
            "title": "Quarterly & annual staffing report",
            "description": (
                "Fiscal-year or fiscal-quarter PDF/HTML — KPI averages, "
                "trend, exception breakdown (AT/LT/SICK/LOA/JURY/BREV), role volumes, and "
                "base coverage. The annual report runs month to month with change vs the prior FY."
            ),
            "open_url_name": "quarterly_staffing_report",
            "open_qs": "",
            "exports": [],
        },
    ]

    report_sections = [
        {
            "title": "Leadership reports",
            "subtitle": "Polished PDF / HTML summaries for leadership: one week, one month, one quarter, or a full fiscal year.",
            "cards": [c for c in report_cards if c["group"] == "leadership"],
        },
        {
            "title": "Analytics",
            "subtitle": "Interactive trends and exports for digging into the numbers.",
            "cards": [c for c in report_cards if c["group"] == "analytics"],
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
            "report_sections": report_sections,
            "fy_policy_note": FY_AND_PAY_PERIOD_POLICY_NOTE,
            "pp_count": len(pay_periods_for_fy(fy_start)),
        },
    )
