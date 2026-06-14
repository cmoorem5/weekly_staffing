"""Smoke tests for quarterly PDF export from staffing.db."""

import os
import tempfile
import unittest

from staffing_tool.db import get_engine, init_db, session_scope
from staffing_tool.models import WeeklyStaffing
from staffing_tool.quarterly_pdf_report import (
    export_quarterly_staffing_pdf,
    list_fiscal_quarters,
    load_quarter_report_data,
)


class QuarterlyPdfReportTests(unittest.TestCase):
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

    def test_list_and_load_quarter(self):
        quarters = list_fiscal_quarters(self.db_path)
        self.assertTrue(
            any(q["fy_label_year"] == 2026 and q["quarter"] == 2 for q in quarters)
        )
        ctx = load_quarter_report_data(self.db_path, 2026, 2)
        self.assertEqual(ctx.period, "FY2026 Q2")
        self.assertEqual(ctx.weeks_count, 1)

    def test_export_quarterly_pdf(self):
        path = export_quarterly_staffing_pdf(self.db_path, 2026, 2, self.out_dir)
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(os.path.getsize(path), 0)


if __name__ == "__main__":
    unittest.main()
