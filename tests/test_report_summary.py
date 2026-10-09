"""Summary lines, hidden empty sections, and leadership report layout."""

import os
import tempfile
import unittest
from pathlib import Path

from staffing_tool.db import session_scope
from staffing_tool.metrics import RoleFill
from staffing_tool.models import WeeklyStaffing
from staffing_tool.report_summary import summary_lines
from tests._temp_db import TempDbTestCase

TARGETS = {
    "Staffing Rate": 0.95,
    "OT Dependency": 0.08,
    "System RW Coverage %": 0.95,
    "System GR Coverage %": 0.92,
}
BUCKETS = [
    ("Oct 2025", 96.0, 7.0, 4.0),
    ("Nov 2025", 93.0, 12.6, 5.0),
    ("Dec 2025", 95.0, 9.0, 6.0),
]
BASES = [
    ("Bedford", "7", "100.0%", "7", "50.0%"),
    ("Manchester", "6", "85.7%", "—", "—"),
]


def _lines(**kw):
    args = dict(
        unit="month",
        buckets=BUCKETS,
        avg_staffing=0.947,
        avg_ot=0.095,
        targets=TARGETS,
        base_coverage=BASES,
    )
    args.update(kw)
    return summary_lines(**args)


class SummaryLineTests(unittest.TestCase):
    def test_staffing_line_counts_periods_meeting_target(self):
        self.assertEqual(
            _lines()[0],
            "Staffing averaged 94.7% against a ≥ 95% target; 2 of 3 months met it.",
        )

    def test_ot_line_names_the_peak_period(self):
        self.assertEqual(
            _lines()[1],
            "OT dependency averaged 9.5% (target ≤ 8%), highest in Nov 2025 at 12.6%.",
        )
        weekly = _lines(unit="week", buckets=[("Aug 2", 95.0, 11.0, 3.0)])
        self.assertIn("highest in the week of Aug 2 at 11.0%", weekly[1])

    def test_coverage_line_lists_bases_below_target_and_skips_unplanned(self):
        self.assertEqual(
            _lines()[2],
            "Below the availability target: Bedford GR 50.0%, Manchester RW 85.7%.",
        )
        many = _lines(
            base_coverage=[(f"B{i}", "1", f"{60 + i}.0%", "—", "—") for i in range(5)]
        )
        self.assertEqual(
            many[2],
            "Below the availability target: B0 RW 60.0%, B1 RW 61.0%, B2 RW 62.0%, "
            "and 2 more.",
        )
        ok = _lines(base_coverage=[("Bedford", "7", "100.0%", "7", "100.0%")])
        self.assertEqual(ok[2], "Every base met its RW and GR availability targets.")

    def test_no_targets_states_facts_without_grading(self):
        lines = _lines(targets={})
        self.assertEqual(lines[0], "Staffing averaged 94.7%.")
        self.assertEqual(len(lines), 2)  # no coverage line without targets


class WeeklyMissingSectionTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        self.out = tempfile.TemporaryDirectory()
        self.addCleanup(self.out.cleanup)
        with session_scope(self.db_path) as session:
            # Exceptions in the week totals, but no per-role or per-day rows.
            session.add(
                WeeklyStaffing(
                    week_start="2025-12-07", filled_day=50, filled_night=20, leave_at=3
                )
            )

    def test_sections_without_data_show_a_note_not_zeros(self):
        from staffing_tool import weekly_pdf_report as W

        ctx = W.load_week_report_data(self.db_path, "2025-12-07")
        self.assertFalse(ctx.has_daily_detail)
        self.assertFalse(ctx.has_role_exceptions)
        html = Path(
            W.export_weekly_staffing_html(self.db_path, "2025-12-07", self.out.name)
        ).read_text(encoding="utf-8")
        self.assertIn("Per-day detail isn't available for this week", html)
        self.assertIn("Exceptions by role aren't available for this week", html)
        pdf = W.export_weekly_staffing_pdf(self.db_path, "2025-12-07", self.out.name)
        self.assertGreater(os.path.getsize(pdf), 0)

    def test_a_week_with_no_exceptions_keeps_its_role_table(self):
        from staffing_tool import weekly_pdf_report as W

        with session_scope(self.db_path) as session:
            session.query(WeeklyStaffing).update({"leave_at": 0})
        ctx = W.load_week_report_data(self.db_path, "2025-12-07")
        self.assertTrue(ctx.has_role_exceptions)


class AnnualLayoutTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        self.out = tempfile.TemporaryDirectory()
        self.addCleanup(self.out.cleanup)
        with session_scope(self.db_path) as session:
            for ws in ("2025-10-05", "2025-11-02"):
                session.add(
                    WeeklyStaffing(
                        week_start=ws, filled_day=80, filled_night=30, ot_rn=3
                    )
                )

    def test_summary_first_coverage_before_exceptions_and_no_duplicate_table(self):
        from staffing_tool.annual_report import export_annual_staffing_html

        html = Path(
            export_annual_staffing_html(self.db_path, 2026, self.out.name)
        ).read_text(encoding="utf-8")
        order = [
            html.index(t)
            for t in (
                ">SUMMARY<",
                "KEY PERFORMANCE INDICATORS",
                "MONTHLY TREND",
                "MONTH-BY-MONTH DETAIL",
                "COVERAGE BY BASE",
                "EXCEPTION BREAKDOWN",
                "ANNUAL VOLUMES BY ROLE",
            )
        ]
        self.assertEqual(order, sorted(order))
        self.assertIn("Staffing averaged", html)

    def test_role_fill_section_only_with_person_rows(self):
        from staffing_tool import quarterly_pdf_report as Q
        from staffing_tool.annual_report import load_annual_report_data

        ctx = load_annual_report_data(self.db_path, 2026)
        self.assertEqual(Q._role_fill_html(ctx.window), "")  # aggregate-only weeks
        ctx.window.role_fill = [RoleFill("RN", "RN", 80, 84, 80 / 84)]
        html = Q._role_fill_html(ctx.window)
        self.assertIn("FILL RATE BY ROLE", html)
        self.assertIn("95.2%", html)


if __name__ == "__main__":
    unittest.main()
