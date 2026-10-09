"""Annual (fiscal-year) staffing report: month bucketing, completeness, exports."""

import math
import os
import tempfile
import unittest
from pathlib import Path

from staffing_tool.annual_report import (
    export_annual_staffing_html,
    export_annual_staffing_pdf,
    list_fiscal_years,
    load_annual_report_data,
)
from staffing_tool.db import init_db, session_scope
from staffing_tool.models import WeeklyStaffing
from tests._temp_db import TempDbTestCase


def _week(week_start: str, filled_day: int, **kw) -> WeeklyStaffing:
    return WeeklyStaffing(
        week_start=week_start, filled_day=filled_day, filled_night=20, **kw
    )


class AnnualReportTests(TempDbTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.out_dir = os.path.join(self.tmp.name, "output")
        init_db(self.db_path)
        with session_scope(self.db_path) as session:
            # FY2026 runs 2025-09-28 .. 2026-09-26 (52 weeks).
            session.add(_week("2025-09-28", 40, leave_at=1))  # Sep 2025 edge month
            session.add(_week("2025-10-05", 50, ot_rn=2, leave_sick=1))
            session.add(_week("2025-10-26", 60))
            session.add(_week("2026-09-20", 55))  # Sep 2026 edge month
            session.add(_week("2024-10-06", 45))  # FY2025, for the prior-year delta
            session.commit()

    def test_lists_fiscal_years_newest_first(self):
        years = list_fiscal_years(self.db_path)
        self.assertEqual([y["fy_label_year"] for y in years], [2026, 2025])
        self.assertEqual(years[0]["date_start"], "2025-09-28")
        self.assertEqual(years[0]["date_end"], "2026-09-26")

    def test_weeks_bucket_by_sunday_month(self):
        ctx = load_annual_report_data(self.db_path, 2026)
        months = [(r[0], r[1]) for r in ctx.monthly_detail]
        self.assertEqual(
            months, [("Sep 2025", "1"), ("Oct 2025", "2"), ("Sep 2026", "1")]
        )
        self.assertEqual(len(ctx.monthly_trend), 3)
        # FY2025 week is outside the window.
        self.assertEqual(ctx.weeks_count, 4)

    def test_month_value_is_mean_of_weekly_rates(self):
        ctx = load_annual_report_data(self.db_path, 2026)
        oct_row = ctx.window.week_metrics[1:3]
        expected = sum(m.staffing_rate for m in oct_row) / 2 * 100
        self.assertAlmostEqual(ctx.monthly_trend[1][1], expected)

    def test_prior_year_aligns_by_fiscal_month(self):
        ctx = load_annual_report_data(self.db_path, 2026)
        self.assertEqual(ctx.prior.period, "FY2025")
        aligned = ctx.prior_trend_aligned()
        self.assertEqual(len(aligned), len(ctx.monthly_trend))
        # FY2025 only has an October week: it lands in October's slot (index
        # 1, after the Sep 2025 edge month); the other slots are empty.
        self.assertEqual(aligned[1][0], "Oct 24")
        self.assertTrue(math.isnan(aligned[0][1]))
        self.assertTrue(math.isnan(aligned[2][1]))

    def test_trend_chart_draws_the_prior_year(self):
        from staffing_tool import annual_report as A
        from staffing_tool import report_style as style

        fig = A._build_trend_fig(load_annual_report_data(self.db_path, 2026))
        try:
            self.assertTrue(
                any(ln.get_color() == style.C_PRIOR for ln in fig.axes[0].get_lines())
            )
        finally:
            import matplotlib.pyplot as plt

            plt.close(fig)

    def test_month_cells_shaded_by_status(self):
        from staffing_tool import annual_report as A
        from staffing_tool.report_data import STATUS_TINTS

        ctx = load_annual_report_data(self.db_path, 2026)
        # Default thresholds: every month here is far under 95% staffing.
        self.assertTrue(all(s.get(2) == "Red" for s in ctx.monthly_status))
        self.assertEqual(len(ctx.monthly_status), len(ctx.monthly_detail))
        bg = A._monthly_cell_bg(ctx)
        self.assertEqual(bg[(0, 2)], STATUS_TINTS["Red"])
        self.assertNotIn((0, 0), bg)  # month label and week count stay plain
        self.assertNotIn((0, 1), bg)

    def test_completeness_note_flags_missing_weeks(self):
        ctx = load_annual_report_data(self.db_path, 2026)
        self.assertEqual(ctx.weeks_expected, 52)
        self.assertIn("4 of 52 weeks", ctx.completeness_note)

    def test_no_data_raises(self):
        with self.assertRaises(ValueError):
            load_annual_report_data(self.db_path, 2030)

    def test_exports(self):
        pdf = export_annual_staffing_pdf(self.db_path, 2026, self.out_dir)
        self.assertTrue(pdf.endswith("BMF_Annual_Staffing_FY2026.pdf"))
        self.assertGreater(os.path.getsize(pdf), 0)

        html = Path(
            export_annual_staffing_html(self.db_path, 2026, self.out_dir)
        ).read_text(encoding="utf-8")
        self.assertIn("ANNUAL STAFFING REPORT", html)
        self.assertIn("MONTH-BY-MONTH DETAIL", html)
        self.assertIn("Oct 2025", html)
        self.assertIn("Change shown vs FY2025", html)
        self.assertNotIn("Manager", html)


if __name__ == "__main__":
    unittest.main()
