"""Unit tests for staffing_tool.fiscal_year."""

import unittest
from datetime import date, timedelta

from staffing_tool.fiscal_year import (
    fy_end_date,
    fy_label_year,
    fy_week1_for_label_year,
    fy_week1_sunday_containing,
    next_fy_week1_sunday,
    pay_period_count_for_fy,
    pay_period_index_overlapping,
    pay_periods_for_fy,
    sunday_on_or_before_sept_28,
)


class FiscalYearTests(unittest.TestCase):
    def test_sunday_on_or_before_sept_28_examples(self):
        self.assertEqual(sunday_on_or_before_sept_28(2025), date(2025, 9, 28))
        self.assertEqual(sunday_on_or_before_sept_28(2026), date(2026, 9, 27))

    def test_fy_containing_january_uses_prior_september_anchor(self):
        self.assertEqual(
            fy_week1_sunday_containing(date(2026, 1, 15)), date(2025, 9, 28)
        )

    def test_fy_end_day_before_next_fy_start(self):
        w1 = date(2025, 9, 28)
        w2 = next_fy_week1_sunday(w1)
        self.assertEqual(w2, date(2026, 9, 27))
        self.assertEqual(fy_end_date(w1), w2 - timedelta(days=1))
        self.assertEqual(fy_end_date(w1), date(2026, 9, 26))

    def test_pay_periods_are_fourteen_day_sunday_starts(self):
        fy = date(2025, 9, 28)
        periods = pay_periods_for_fy(fy)
        self.assertEqual(periods[0].start.weekday(), 6)
        self.assertEqual((periods[0].end - periods[0].start).days, 13)
        self.assertEqual(periods[0].start, fy)
        self.assertEqual(periods[1].start, fy + timedelta(days=14))

    def test_fy_length_matches_pay_period_coverage(self):
        fy = date(2025, 9, 28)
        end = fy_end_date(fy)
        periods = pay_periods_for_fy(fy)
        self.assertEqual(periods[0].start, fy)
        self.assertEqual(periods[-1].end, end)
        self.assertEqual(pay_period_count_for_fy(fy), len(periods))

    def test_fy_label_year_matches_end_calendar_year(self):
        fy = date(2025, 9, 28)
        self.assertEqual(fy_label_year(fy), fy_end_date(fy).year)
        self.assertEqual(fy_label_year(fy), 2026)

    def test_fy_week1_for_label_year_inverse(self):
        self.assertEqual(fy_week1_for_label_year(2026), date(2025, 9, 28))

    def test_pay_period_index_overlapping(self):
        fy = date(2025, 9, 28)
        periods = pay_periods_for_fy(fy)
        p2 = periods[1]
        self.assertEqual(pay_period_index_overlapping(fy, p2.start, p2.end), 2)


if __name__ == "__main__":
    unittest.main()
