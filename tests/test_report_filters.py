"""Report filter cleanup: FY-driven date window, monthly month picker, hub sections."""

import importlib
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bmf_staffing"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmf_staffing.settings")

import django

django.setup()

import unittest

from django.test import RequestFactory
from staffing_tool.db import session_scope
from staffing_tool.models import WeeklyStaffing
from tests._temp_db import TempDbTestCase

from dashboard.views import helpers
from dashboard.views.dashboard_filters import resolve_fy_date_window

# ``dashboard.views`` re-exports view functions under their modules' names.
monthly = importlib.import_module("dashboard.views.monthly_report")
reports = importlib.import_module("dashboard.views.reports")

TODAY = date(2026, 10, 8)  # FY2027 week 2; FY2026 ran 2025-09-28 .. 2026-09-26


def _window(**params):
    return resolve_fy_date_window(RequestFactory().get("/", params), TODAY)


class FyDateWindowTests(unittest.TestCase):
    def test_no_params_is_current_fy_to_date(self):
        w = _window()
        self.assertEqual(w.fy_label, 2027)
        self.assertTrue(w.is_current_fy)
        self.assertEqual(w.date_start, date(2026, 9, 27))

    def test_closed_fy_defaults_to_full_year(self):
        w = _window(fy="2026")
        self.assertFalse(w.is_current_fy)
        self.assertEqual(
            (w.date_start, w.date_end), (date(2025, 9, 28), date(2026, 9, 26))
        )

    def test_dates_for_same_fy_are_kept(self):
        w = _window(
            fy="2026", dates_fy="2026", date_start="2025-12-01", date_end="2026-02-28"
        )
        self.assertEqual(
            (w.date_start, w.date_end), (date(2025, 12, 1), date(2026, 2, 28))
        )

    def test_switching_fy_drops_the_old_fys_dates(self):
        # Dates were for FY2027; user switched the dropdown to FY2026. Clamping
        # alone would have kept a 1-day sliver or fallen back by accident.
        w = _window(
            fy="2026", dates_fy="2027", date_start="2026-09-27", date_end="2026-10-03"
        )
        self.assertEqual(
            (w.date_start, w.date_end), (date(2025, 9, 28), date(2026, 9, 26))
        )

    def test_links_without_dates_fy_still_clamp(self):
        w = _window(fy="2026", date_start="2025-01-01", date_end="2026-03-31")
        self.assertEqual(
            (w.date_start, w.date_end), (date(2025, 9, 28), date(2026, 3, 31))
        )


class MonthlyMonthPickerTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        with session_scope(self.db_path) as session:
            for ws in ("2026-08-30", "2026-09-06", "2026-09-27"):
                session.add(
                    WeeklyStaffing(week_start=ws, filled_day=50, filled_night=20)
                )

    def test_months_newest_first_with_full_month_bounds(self):
        months = monthly._months_with_data(self.db_path)
        self.assertEqual(
            [(m["label"], m["date_start"], m["date_end"]) for m in months],
            [
                ("September 2026", "2026-09-01", "2026-09-30"),
                ("August 2026", "2026-08-01", "2026-08-31"),
            ],
        )

    def test_page_renders_picker(self):
        with (
            patch.object(monthly, "DB_PATH", self.db_path),
            patch.object(helpers, "DB_PATH", self.db_path),
        ):
            resp = monthly.monthly_report(RequestFactory().get("/report/monthly/"))
        html = resp.content.decode()
        self.assertIn('id="id_month_pick"', html)
        self.assertIn("2026-09-01|2026-09-30", html)


class ReportsHubSectionTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()

    def test_cards_grouped_into_two_sections(self):
        with (
            patch.object(reports, "DB_PATH", self.db_path),
            patch.object(helpers, "DB_PATH", self.db_path),
        ):
            resp = reports.reports_index(RequestFactory().get("/reports/"))
        html = resp.content.decode()
        positions = [
            html.index(t)
            for t in (
                "Leadership reports",
                "Monthly staffing report",
                "Quarterly &amp; annual staffing report",
                "Analytics",
                "Manager line shifts",
            )
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("Board pack", html)


if __name__ == "__main__":
    unittest.main()
