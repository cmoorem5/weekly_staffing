"""Smoke tests for the monthly and quarterly HTML report exports."""

import os
import tempfile
import unittest
from pathlib import Path

from staffing_tool.db import get_engine, init_db, session_scope
from staffing_tool.models import WeeklyStaffing
from staffing_tool.monthly_html_report import (
    export_monthly_report_html,
    load_monthly_board_data,
)
from staffing_tool.quarterly_pdf_report import export_quarterly_staffing_html


class HtmlReportExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.out_dir = os.path.join(self.tmp.name, "output")
        init_db(self.db_path)
        with session_scope(self.db_path) as session:
            # Two months so the December board report has a prior period.
            for ws, filled in [
                ("2025-11-02", 48),
                ("2025-11-09", 47),
                ("2025-12-07", 50),
                ("2025-12-14", 52),
            ]:
                session.add(
                    WeeklyStaffing(
                        week_start=ws,
                        filled_day=filled,
                        filled_night=20,
                        ot_rn=2,
                        ot_medic=1,
                        ot_emt=0,
                        leave_at=3,
                        leave_sick=1,
                    )
                )
            session.commit()

    def tearDown(self):
        get_engine(self.db_path).dispose()
        self.tmp.cleanup()

    def test_monthly_board_data_includes_prior_period(self):
        data = load_monthly_board_data(self.db_path, "2025-12-01", "2025-12-31")
        self.assertEqual(data.weeks_count, 2)
        self.assertIsNotNone(data.prior_rollups)
        self.assertEqual(len(data.weekly_detail), 2)

    def test_monthly_html_export(self):
        path = export_monthly_report_html(
            self.db_path, "2025-12-01", "2025-12-31", self.out_dir
        )
        self.assertTrue(os.path.isfile(path))
        html = Path(path).read_text(encoding="utf-8")
        self.assertIn("MONTHLY STAFFING REPORT", html)
        self.assertIn("KEY PERFORMANCE INDICATORS", html)
        self.assertIn("data:image/png;base64,", html)  # embedded charts
        self.assertIn("pts</span>", html)  # prior-period delta rendered

    def test_monthly_html_rejects_empty_range(self):
        with self.assertRaises(ValueError):
            export_monthly_report_html(
                self.db_path, "2020-01-01", "2020-01-31", self.out_dir
            )

    def test_quarterly_html_export(self):
        path = export_quarterly_staffing_html(self.db_path, 2026, 2, self.out_dir)
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(path.endswith(".html"))
        html = Path(path).read_text(encoding="utf-8")
        self.assertIn("QUARTERLY STAFFING REPORT", html)
        self.assertIn("FY2026 Q2", html)
        self.assertIn("PERIOD VOLUMES BY ROLE", html)
        self.assertIn("data:image/png;base64,", html)


if __name__ == "__main__":
    unittest.main()
