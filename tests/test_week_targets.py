"""Stored ground-target and no-vehicle figures, and how metrics use them."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from staffing_tool.db import session_scope
from staffing_tool.metrics import compute_week_metrics
from staffing_tool.models import (
    BaseConfig,
    WeeklyBaseCoverage,
    WeeklyOpsViewAssignment,
    WeeklyStaffing,
    WeeklyVehicleOutage,
)
from staffing_tool.schedule_import import UNIT_MAP
from staffing_tool.shortfall import hand_corrections, reconcile_week
from staffing_tool.week_targets import recompute_all_weeks, recompute_week_targets
from tests._temp_db import TempDbTestCase

WEEK = "2026-10-25"
FULL_RW = ("RN", "Medic", "PIC")
FULL_GR = ("RN", "Medic", "EMT")
# Trucks staffed on a normal day: three day trucks and one night truck.
NORMAL_UNITS = ("GR", "LG", "MG", "NG")
ROTOR_UNITS = tuple(
    u for u, (_b, svc, _dn) in UNIT_MAP.items() if svc == "RW" and u != "G9B"
)


def _day(i: int) -> str:
    return (date.fromisoformat(WEEK) + timedelta(days=i)).isoformat()


class WeekTargetTests(TempDbTestCase):
    def setUp(self):
        self.db = self.make_temp_db()
        with session_scope(self.db) as session:
            session.add(WeeklyStaffing(week_start=WEEK, filled_day=0, filled_night=0))
            session.flush()

    def _staff(self, day_index: int, units, *, skip=()):
        with session_scope(self.db) as session:
            for unit in units:
                full = FULL_RW if UNIT_MAP[unit][1] == "RW" else FULL_GR
                for role in full:
                    if (unit, role) in skip:
                        continue
                    session.add(
                        WeeklyOpsViewAssignment(
                            week_start=WEEK,
                            day_date=_day(day_index),
                            unit_code=unit,
                            role=role,
                            person_display="N",
                            raw_value="N",
                            is_staffed=1,
                        )
                    )

    def _metrics(self):
        with session_scope(self.db) as session:
            row = session.query(WeeklyStaffing).one()
            covs = session.query(WeeklyBaseCoverage).all()
            cfgs = session.query(BaseConfig).all()
            return compute_week_metrics(row, covs, cfgs)

    def _cover(self, **per_base):
        """per_base: name=(rw_total, gr_day, gr_night)."""
        with session_scope(self.db) as session:
            for base, (rw, gr_d, gr_n) in per_base.items():
                session.add(
                    WeeklyBaseCoverage(
                        week_start=WEEK,
                        base_name=base,
                        rw_staffed_unit_days=rw,
                        rw_staffed_day=rw,
                        gr_staffed_unit_days=gr_d + gr_n,
                        gr_staffed_day=gr_d,
                        gr_staffed_night=gr_n,
                    )
                )

    def test_five_trucks_one_day_offsets_a_short_day(self):
        # The weekly ground figure is the total of every ground shift staffed:
        # 5 trucks one day and 3 another is the same as 4 and 4.
        for i in range(7):
            self._staff(i, ROTOR_UNITS)
        self._staff(0, ("GR", "LG", "MG", "PG", "NG"))  # a fifth truck
        self._staff(1, ("GR", "LG", "NG"))  # a day truck short
        for i in range(2, 7):
            self._staff(i, NORMAL_UNITS)
        # Day trucks: 4 + 2 + 3x5 = 21, night 7: 28 shifts, the same total as
        # 4 trucks a day, so the same weekly percentage.
        self._cover(
            Bedford=(0, 7, 7),
            Lawrence=(0, 7, 0),
            Mansfield=(0, 7, 0),
            Plymouth=(0, 0, 0),
        )
        with session_scope(self.db) as session:
            self.assertTrue(recompute_week_targets(session, WEEK))
            self.assertEqual(session.query(WeeklyStaffing).one().gr_target_planned, 28)
        m = self._metrics()
        self.assertEqual(m.gr_staffed_unit_days, 28)
        self.assertAlmostEqual(m.system_gr_pct, 1.0)

    def test_more_than_the_plan_is_capped_at_100_percent(self):
        self._cover(
            Bedford=(0, 10, 7),
            Lawrence=(0, 7, 0),
            Mansfield=(0, 7, 0),
            Plymouth=(0, 7, 0),
        )
        m = self._metrics()  # 38 ground shifts staffed against a plan of 28
        self.assertEqual(m.gr_staffed_unit_days, 28)
        self.assertAlmostEqual(m.system_gr_pct, 1.0)

    def test_editing_a_base_changes_that_base_and_the_weekly_total_together(self):
        self._cover(Bedford=(0, 7, 7), Lawrence=(0, 7, 0))
        before = self._metrics()
        with session_scope(self.db) as session:
            lawrence = (
                session.query(WeeklyBaseCoverage).filter_by(base_name="Lawrence").one()
            )
            lawrence.gr_staffed_day = 5
            lawrence.gr_staffed_unit_days = 5
        after = self._metrics()
        self.assertAlmostEqual(before.system_gr_pct, 21 / 28)
        self.assertAlmostEqual(after.system_gr_pct, 19 / 28)

    def test_no_vehicle_without_crew_comes_off_the_rotor_wing_plan(self):
        for i in range(7):
            self._staff(i, ROTOR_UNITS)
        self._staff(2, ("GR",))
        # D9L (Lawrence) has no crew on day 3 and the aircraft is down.
        with session_scope(self.db) as session:
            session.query(WeeklyOpsViewAssignment).filter_by(
                day_date=_day(3), unit_code="D9L"
            ).delete()
            session.add(
                WeeklyVehicleOutage(
                    week_start=WEEK,
                    day_date=_day(3),
                    unit_code="D9L",
                    note="aircraft down",
                    created_at="x",
                )
            )
        self._cover(Lawrence=(13, 0, 0), Bedford=(14, 0, 0))
        with session_scope(self.db) as session:
            recompute_week_targets(session, WEEK)
            removed = {
                c.base_name: c.rw_planned_removed
                for c in session.query(WeeklyBaseCoverage).all()
            }
        self.assertEqual(removed["Lawrence"], 1)
        self.assertEqual(removed["Bedford"], 0)
        m = self._metrics()
        self.assertEqual(m.rw_total_unit_days, 55)  # 56 less the down aircraft
        self.assertAlmostEqual(m.system_rw_pct, 27 / 55)
        self.assertEqual(m.base_metrics["Lawrence"]["rw_planned"], 13.0)

    def test_week_without_detail_is_cleared_not_guessed(self):
        with session_scope(self.db) as session:
            row = session.query(WeeklyStaffing).one()
            row.gr_target_planned = 27
            self.assertFalse(recompute_week_targets(session, WEEK))
            self.assertIsNone(row.gr_target_planned)

    def test_legacy_week_caps_the_per_base_sum_at_the_weekly_target(self):
        # 35 truck-days staffed across bases would read 125% of 28 without a cap.
        self._cover(
            Bedford=(0, 10, 4),
            Lawrence=(0, 5, 2),
            Mansfield=(0, 7, 0),
            Plymouth=(0, 5, 2),
        )
        m = self._metrics()
        self.assertEqual(m.gr_staffed_unit_days, 28)
        self.assertAlmostEqual(m.system_gr_pct, 1.0)

    def test_per_base_share_of_ground_shifts(self):
        self._cover(Bedford=(0, 10, 4), Lawrence=(0, 5, 2), Plymouth=(0, 3, 0))
        shares = {b: v["gr_share"] for b, v in self._metrics().base_metrics.items()}
        self.assertAlmostEqual(shares["Bedford"], 14 / 24)
        self.assertAlmostEqual(shares["Lawrence"], 7 / 24)
        self.assertAlmostEqual(sum(shares.values()), 1.0)

    def _reconcile(self):
        with session_scope(self.db) as session:
            return reconcile_week(session, WEEK)

    def test_reconcile_flags_a_hand_corrected_base(self):
        for i in range(7):
            self._staff(i, ROTOR_UNITS)
        # The schedule implies 14/14/14/7/7 aircraft-days; Lawrence was corrected by hand.
        self._cover(
            Bedford=(14, 0, 0),
            Lawrence=(13, 0, 0),
            Mansfield=(7, 0, 0),
            Manchester=(7, 0, 0),
            Plymouth=(14, 0, 0),
        )
        differing = [r for r in self._reconcile() if r.diff]
        self.assertEqual(
            [(r.scope, r.stored, r.from_schedule) for r in differing],
            [("Lawrence", 13, 14)],
        )

    def test_reconcile_compares_ground_as_one_system_total(self):
        for i in range(7):
            self._staff(i, ROTOR_UNITS + NORMAL_UNITS)
        # 28 ground shifts on the schedule; the stored count was corrected down to 25.
        self._cover(Bedford=(14, 7, 7), Lawrence=(14, 4, 0), Mansfield=(7, 7, 0))
        ground = [r for r in self._reconcile() if r.service_type == "GR"]
        self.assertEqual(
            [(r.scope, r.stored, r.from_schedule) for r in ground],
            [("Ground (system)", 25, 28)],
        )

    def test_hand_corrections_count_only_stored_above_schedule(self):
        for i in range(7):
            self._staff(i, ROTOR_UNITS)
        # Lawrence stored one above the schedule (+1), Bedford one below (-1, ignored).
        self._cover(
            Bedford=(13, 0, 0),
            Lawrence=(15, 0, 0),
            Mansfield=(7, 0, 0),
            Manchester=(7, 0, 0),
            Plymouth=(14, 0, 0),
        )
        with session_scope(self.db) as session:
            self.assertEqual(hand_corrections(session, [WEEK]), (1, 0))

    def test_hand_corrections_ignore_a_week_without_detail(self):
        with session_scope(self.db) as session:
            self.assertEqual(hand_corrections(session, [WEEK]), (0, 0))

    def test_reconcile_skips_a_week_without_detail(self):
        self.assertEqual(self._reconcile(), [])

    def test_recompute_all_weeks_covers_each_week_with_detail(self):
        self._staff(0, NORMAL_UNITS)
        with session_scope(self.db) as session:
            self.assertEqual(recompute_all_weeks(session), {WEEK: True})


if __name__ == "__main__":
    unittest.main()
