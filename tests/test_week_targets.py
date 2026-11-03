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

    def test_extra_truck_one_day_does_not_hide_a_gap_another_day(self):
        for i in range(7):
            self._staff(i, ROTOR_UNITS)
        # Day 0: a fifth truck (all four day trucks). Day 1: only two day trucks.
        self._staff(0, ("GR", "LG", "MG", "PG", "NG"))
        self._staff(1, ("GR", "LG", "NG"))
        for i in range(2, 7):
            self._staff(i, NORMAL_UNITS)
        with session_scope(self.db) as session:
            self.assertTrue(recompute_week_targets(session, WEEK))
            row = session.query(WeeklyStaffing).one()
            # Day trucks capped at 3 a day: 3 + 2 + 3*5 = 20. A raw sum would be 22.
            self.assertEqual(row.gr_target_staffed_day, 20)
            self.assertEqual(row.gr_target_staffed_night, 7)
            self.assertEqual(row.gr_target_planned, 28)
        self._cover(Bedford=(14, 0, 0))
        self.assertAlmostEqual(self._metrics().system_gr_pct, 27 / 28)

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
            row.gr_target_staffed_day = 20
            row.gr_target_staffed_night = 7
            row.gr_target_planned = 28
            self.assertFalse(recompute_week_targets(session, WEEK))
            self.assertIsNone(row.gr_target_staffed_day)
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

    def test_recompute_all_weeks_covers_each_week_with_detail(self):
        self._staff(0, NORMAL_UNITS)
        with session_scope(self.db) as session:
            self.assertEqual(recompute_all_weeks(session), {WEEK: True})


if __name__ == "__main__":
    unittest.main()
