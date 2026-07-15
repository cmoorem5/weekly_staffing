"""Smoke tests for weekly PDF/HTML export from staffing.db."""

import os
import tempfile
import unittest

from staffing_tool.db import get_engine, init_db, session_scope
from staffing_tool.models import WeeklyStaffing
from staffing_tool.weekly_pdf_report import export_weekly_staffing_both


class WeeklyPdfReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.out_dir = os.path.join(self.tmp.name, "output")
        init_db(self.db_path)
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start="2025-12-07",
                    filled_day=50,
                    filled_night=20,
                    ot_rn_day=1,
                    ot_medic_day=0,
                    ot_emt_day=0,
                    ot_rn_night=0,
                    ot_medic_night=0,
                    ot_emt_night=0,
                    ot_rn=1,
                    ot_medic=0,
                    ot_emt=0,
                    leave_at=2,
                    leave_lt=1,
                    training_shifts=5,
                )
            )
            session.commit()

    def tearDown(self):
        get_engine(self.db_path).dispose()
        self.tmp.cleanup()

    def test_export_weekly_pdf_and_html(self):
        pdf_path, html_path = export_weekly_staffing_both(
            self.db_path, "2025-12-07", self.out_dir
        )
        self.assertTrue(os.path.isfile(pdf_path))
        self.assertTrue(os.path.isfile(html_path))
        self.assertGreater(os.path.getsize(pdf_path), 0)
        self.assertGreater(os.path.getsize(html_path), 0)

    def test_training_events_kpi_in_html(self):
        _pdf_path, html_path = export_weekly_staffing_both(
            self.db_path, "2025-12-07", self.out_dir
        )
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("Training Events", html)
        self.assertIn(">5<", html)
        self.assertNotIn("Person-Shifts", html)


if __name__ == "__main__":
    unittest.main()
