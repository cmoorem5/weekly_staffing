"""Tests for compute_week_metrics and period rollups."""

import unittest
from types import SimpleNamespace

from staffing_tool.metrics import (
    REQUIRED_TOTAL,
    TOTAL_PERSON_SHIFTS,
    compute_period_rollups,
    compute_week_metrics,
)


def _week_row(**kwargs):
    defaults = {
        "week_start": "2026-01-04",
        "filled_day": 56,
        "filled_night": 28,
        "ot_rn_day": 0,
        "ot_rn_night": 0,
        "ot_medic_day": 0,
        "ot_medic_night": 0,
        "ot_emt_day": 0,
        "ot_emt_night": 0,
        "ot_rn": 0,
        "ot_medic": 0,
        "ot_emt": 0,
        "ot_shifts": 0,
        "leave_at": 0,
        "leave_lt": 0,
        "leave_sick": 0,
        "leave_loa": 0,
        "leave_pfml": 0,
        "leave_jury": 0,
        "leave_brev": 0,
        "overnights_below": 0,
        "pilot_vacancies": 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class ComputeWeekMetricsTests(unittest.TestCase):
    def test_staffing_rate_and_ot_dependency(self):
        row = _week_row(
            filled_day=50,
            filled_night=20,
            ot_rn_day=2,
            ot_medic_day=1,
        )
        m = compute_week_metrics(row, [], [])
        self.assertEqual(m.filled_total, 70)
        self.assertAlmostEqual(m.staffing_rate, 70 / REQUIRED_TOTAL)
        self.assertEqual(m.ot_shifts, 3)
        self.assertAlmostEqual(m.ot_dependency, 3 / 70)

    def test_ot_legacy_fallback_when_day_night_zero(self):
        row = _week_row(
            filled_day=40,
            filled_night=20,
            ot_rn=2,
            ot_medic=1,
            ot_emt=1,
        )
        m = compute_week_metrics(row, [], [])
        self.assertEqual(m.ot_shifts, 4)
        self.assertAlmostEqual(m.ot_dependency, 4 / 60)

    def test_leave_total_includes_pfml_jury_brev(self):
        row = _week_row(
            leave_at=1,
            leave_lt=2,
            leave_sick=3,
            leave_loa=4,
            leave_pfml=5,
            leave_jury=6,
            leave_brev=7,
        )
        m = compute_week_metrics(row, [], [])
        self.assertEqual(m.leave_total, 28)
        self.assertAlmostEqual(m.leave_exposure, 28 / TOTAL_PERSON_SHIFTS)

    def test_zero_filled_yields_zero_ot_dependency(self):
        row = _week_row(filled_day=0, filled_night=0, ot_rn_day=3)
        m = compute_week_metrics(row, [], [])
        self.assertEqual(m.ot_shifts, 3)
        self.assertEqual(m.ot_dependency, 0.0)


class ComputePeriodRollupsTests(unittest.TestCase):
    def test_pooled_ot_differs_from_average_when_denominators_differ(self):
        low = compute_week_metrics(
            _week_row(filled_day=40, filled_night=0, ot_rn_day=2),
            [],
            [],
        )
        high = compute_week_metrics(
            _week_row(filled_day=80, filled_night=0, ot_rn_day=16),
            [],
            [],
        )
        rollups = compute_period_rollups([low, high])
        assert rollups is not None
        self.assertAlmostEqual(rollups.avg_ot_dependency, (0.05 + 0.20) / 2)
        self.assertAlmostEqual(rollups.pooled_ot_dependency, 18 / 120)
        self.assertNotAlmostEqual(
            rollups.avg_ot_dependency, rollups.pooled_ot_dependency, places=5
        )

    def test_pooled_leave_uses_period_person_shifts(self):
        w1 = compute_week_metrics(_week_row(leave_at=10), [], [])
        w2 = compute_week_metrics(_week_row(leave_at=20), [], [])
        rollups = compute_period_rollups([w1, w2])
        assert rollups is not None
        self.assertEqual(rollups.leave_total, 30)
        self.assertAlmostEqual(
            rollups.pooled_leave_exposure, 30 / (2 * TOTAL_PERSON_SHIFTS)
        )


if __name__ == "__main__":
    unittest.main()
