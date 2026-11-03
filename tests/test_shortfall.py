"""Shortfall cause attribution: no crew vs no operator (pilot / EMT)."""

from __future__ import annotations

import unittest
from datetime import date

from staffing_tool.db import session_scope
from staffing_tool.models import WeeklyOpsViewAssignment, WeeklyStaffing
from staffing_tool.shortfall import (
    CAUSE_BOTH,
    CAUSE_CREW,
    CAUSE_OPERATOR,
    find_shortfalls,
    load_shortfalls,
    planned_units,
    slots_per_day,
)
from tests._temp_db import TempDbTestCase

DAY = date(2026, 10, 25)
FULL_RW = {"RN", "Medic", "PIC"}
FULL_GR = {"RN", "Medic", "EMT"}


def _all_staffed(day: date) -> dict[tuple[date, str], set[str]]:
    """Every planned unit fully staffed on ``day``."""
    from staffing_tool.schedule_import import UNIT_MAP

    roles: dict[tuple[date, str], set[str]] = {}
    for units in planned_units().values():
        for unit in units:
            full = FULL_RW if UNIT_MAP[unit][1] == "RW" else FULL_GR
            roles[(day, unit)] = set(full)
    return roles


def _shorts(roles, day=DAY):
    return find_shortfalls(roles, [day]).shortfalls


class SlotTests(unittest.TestCase):
    def test_slots_follow_weekly_caps(self):
        self.assertEqual(slots_per_day("Bedford", "RW"), 2)
        self.assertEqual(slots_per_day("Bedford", "GR"), 2)
        self.assertEqual(slots_per_day("Plymouth", "GR"), 1)
        self.assertEqual(slots_per_day("Manchester", "RW"), 1)
        self.assertEqual(slots_per_day("Manchester", "GR"), 0)

    def test_extra_units_are_never_planned(self):
        codes = {u for units in planned_units().values() for u in units}
        self.assertFalse(codes & {"GR2", "NG2", "G9B"})


class CauseTests(unittest.TestCase):
    def test_fully_staffed_has_no_shortfall(self):
        result = find_shortfalls(_all_staffed(DAY), [DAY])
        self.assertEqual(result.shortfalls, [])
        self.assertEqual(result.planned, result.staffed)

    def test_rw_missing_pilot_is_no_operator(self):
        roles = _all_staffed(DAY)
        roles[(DAY, "D9L")] = {"RN", "Medic"}
        (s,) = _shorts(roles)
        self.assertEqual(s.unit_code, "D9L")
        self.assertEqual(s.cause, CAUSE_OPERATOR)
        self.assertEqual(s.detail, "Pilot")

    def test_rw_missing_clinical_is_no_crew(self):
        roles = _all_staffed(DAY)
        roles[(DAY, "N7B")] = {"Medic", "PIC"}
        (s,) = _shorts(roles)
        self.assertEqual(s.cause, CAUSE_CREW)
        self.assertEqual(s.missing_crew, ("RN",))
        self.assertFalse(s.operator_missing)

    def test_rw_missing_crew_and_pilot_is_both(self):
        roles = _all_staffed(DAY)
        roles[(DAY, "D11M")] = {"Medic"}
        (s,) = _shorts(roles)
        self.assertEqual(s.cause, CAUSE_BOTH)
        self.assertEqual(s.detail, "RN + Pilot")

    def test_gr_operator_is_emt_not_pilot(self):
        roles = _all_staffed(DAY)
        roles[(DAY, "LG")] = {"RN", "Medic"}
        (s,) = _shorts(roles)
        self.assertEqual(s.cause, CAUSE_OPERATOR)
        self.assertEqual(s.detail, "EMT")

    def test_gr_pilot_row_does_not_satisfy_emt(self):
        roles = _all_staffed(DAY)
        roles[(DAY, "MG")] = {"RN", "Medic", "PIC"}
        (s,) = _shorts(roles)
        self.assertEqual(s.cause, CAUSE_OPERATOR)

    def test_wholly_empty_vehicle_is_both(self):
        roles = _all_staffed(DAY)
        del roles[(DAY, "D7P")]
        (s,) = _shorts(roles)
        self.assertEqual(s.cause, CAUSE_BOTH)
        self.assertEqual(s.detail, "RN + Medic + Pilot")


class SharedSlotTests(unittest.TestCase):
    def test_plymouth_night_ground_covers_missing_day_ground(self):
        # One planned Plymouth GR slot a day: staffed NP stands in for PG,
        # matching how the capped coverage count treats it.
        roles = _all_staffed(DAY)
        roles[(DAY, "PG")] = {"RN"}
        self.assertEqual(_shorts(roles), [])

    def test_plymouth_both_short_blames_the_closer_one(self):
        roles = _all_staffed(DAY)
        roles[(DAY, "PG")] = {"RN", "Medic"}  # only EMT missing
        roles[(DAY, "NP")] = {"RN"}  # Medic and EMT missing
        (s,) = _shorts(roles)
        self.assertEqual(s.unit_code, "PG")
        self.assertEqual(s.cause, CAUSE_OPERATOR)

    def test_bedford_ground_has_two_slots(self):
        roles = _all_staffed(DAY)
        roles[(DAY, "NG")] = {"RN", "Medic"}
        (s,) = _shorts(roles)
        self.assertEqual((s.unit_code, s.day_night), ("NG", "N"))
        self.assertEqual(s.cause, CAUSE_OPERATOR)


class LoadShortfallsTests(TempDbTestCase):
    def setUp(self):
        self.db = self.make_temp_db()

    def _week(self, week: str, with_rows: bool, drop: tuple[str, str] | None = None):
        # drop = (unit, role) left out on the Monday after the week starts.
        with session_scope(self.db) as session:
            session.add(WeeklyStaffing(week_start=week, filled_day=0, filled_night=0))
            session.flush()
            if not with_rows:
                return
            start = date.fromisoformat(week)
            from datetime import timedelta

            from staffing_tool.schedule_import import UNIT_MAP

            for i in range(7):
                day = (start + timedelta(days=i)).isoformat()
                for units in planned_units().values():
                    for unit in units:
                        full = FULL_RW if UNIT_MAP[unit][1] == "RW" else FULL_GR
                        for role in full:
                            if drop and i == 1 and (unit, role) == drop:
                                continue
                            session.add(
                                WeeklyOpsViewAssignment(
                                    week_start=week,
                                    day_date=day,
                                    unit_code=unit,
                                    role=role,
                                    person_display="Name",
                                    raw_value="Name",
                                    is_staffed=1,
                                )
                            )

    def test_missing_pilot_row_reads_as_no_operator(self):
        self._week("2026-10-25", True, drop=("D9L", "PIC"))
        result = load_shortfalls(self.db, date(2026, 10, 25), date(2026, 10, 31))
        self.assertEqual(result.days_analyzed, 7)
        (s,) = result.shortfalls
        self.assertEqual((s.day, s.unit_code), (date(2026, 10, 26), "D9L"))
        self.assertEqual(s.cause, CAUSE_OPERATOR)

    def test_week_without_detail_is_listed_not_counted_short(self):
        self._week("2026-10-18", False)
        self._week("2026-10-25", True)
        result = load_shortfalls(self.db, date(2026, 10, 18), date(2026, 10, 31))
        self.assertEqual(result.weeks_without_detail, ["2026-10-18"])
        self.assertEqual(result.days_analyzed, 7)
        self.assertEqual(result.shortfalls, [])

    def test_window_clips_days_inside_a_week(self):
        self._week("2026-09-27", True, drop=("D7B", "RN"))  # short on Mon 9/28
        result = load_shortfalls(self.db, date(2026, 10, 1), date(2026, 10, 3))
        self.assertEqual(result.days_analyzed, 3)
        self.assertEqual(result.shortfalls, [])


if __name__ == "__main__":
    unittest.main()
