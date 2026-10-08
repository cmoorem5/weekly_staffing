"""Quarterly & annual report page: full-FY option and annual download."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bmf_staffing"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmf_staffing.settings")

import django

django.setup()

import importlib
from datetime import date

from django.test import RequestFactory
from staffing_tool.db import session_scope
from staffing_tool.models import WeeklyStaffing
from tests._temp_db import TempDbTestCase

from dashboard.views import helpers

# ``dashboard.views`` re-exports the view function under the module's name.
view = importlib.import_module("dashboard.views.quarterly_staffing_report")


class AnnualReportViewTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(week_start="2025-10-05", filled_day=50, filled_night=20)
            )
            session.add(
                WeeklyStaffing(week_start="2026-09-27", filled_day=50, filled_night=20)
            )
        out = tempfile.TemporaryDirectory()
        self.addCleanup(out.cleanup)
        for p in (
            patch.object(view, "DB_PATH", self.db_path),
            patch.object(helpers, "DB_PATH", self.db_path),
            patch.object(helpers, "OUTPUT_DIR", out.name),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_default_is_latest_closed_fy(self):
        years = [
            {"fy_label_year": 2027, "date_end": "2027-09-25"},
            {"fy_label_year": 2026, "date_end": "2026-09-26"},
        ]
        self.assertEqual(view._default_fy(years, date(2026, 10, 8)), 2026)
        self.assertEqual(view._default_fy(years[:1], date(2026, 10, 8)), 2027)

    def _post(self, **data):
        request = RequestFactory().post("/report/quarterly/", data)
        request._dont_enforce_csrf_checks = True
        with patch.object(view.messages, "error") as err:
            resp = view.quarterly_staffing_report(request)
        return resp, err

    def test_annual_pdf_download(self):
        resp, err = self._post(fy_label_year="2026", quarter="0", format="pdf")
        err.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("BMF_Annual_Staffing_FY2026.pdf", resp["Content-Disposition"])

    def test_annual_rejects_fy_without_data(self):
        resp, err = self._post(fy_label_year="2024", quarter="0", format="pdf")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("FY2024 has no data", err.call_args.args[1])
