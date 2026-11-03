"""Shortfall cause attribution: no crew vs no operator, ground target, no vehicle."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from staffing_tool.db import session_scope
from staffing_tool.models import (
    WeeklyOpsViewAssignment,
    WeeklyStaffing,
    WeeklyVehicleOutage,
)
from staffing_tool.schedule_import import UNIT_MAP
from staffing_tool.shortfall import (
    CAUSE_BOTH,
    CAUSE_CREW,
    CAUSE_OPERATOR,
    GROUND_DAY_SCOPE,
    GROUND_NIGHT_SCOPE,
    SYSTEM_GROUND_PER_DAY,
    find_shortfalls,
    load_shortfalls,
    planned_groups,
)
from tests._temp_db import TempDbTestCase

DAY = date(2026, 10, 25)
FULL_RW = {"RN", "Medic", "PIC"}
FULL_GR = {"RN", "Medic", "EMT"}


def _all_staffed(day: date, *, without: tuple[str, ...] = ()):
    """Every planned unit fully staffed on ``day``, except ``without``."""
    roles: dict[tuple[date, str], set[str]] = {}
    for g in planned_groups():
        for unit in g.units:
            if unit in without:
                continue
            roles[(day, unit)] = set(FULL_RW if UNIT_MAP[unit][1] == "RW" else FULL_GR)
    return roles


def _shorts(roles, day=DAY, outages=None):
    return find_shortfalls(roles, [day], outages).shortfalls


class PlanTests(unittest.TestCase):
    def test_rotor_wing_slots_follow_base_caps(self):
        slots = {g.scope: g.slots for g in planned_groups() if g.service_type == "RW"}
        self.assertEqual(
            slots,
            {
                "Bedford": 2,
                "Plymouth": 2,
                "Lawrence": 2,
                "Mansfield": 1,
                "Manchester": 1,
            },
        )

    def test_ground_is_three_day_plus_one_night_for_the_system(self):
        ground = {g.scope: g for g in planned_groups() if g.service_type == "GR"}
        self.assertEqual(set(ground), {GROUND_DAY_SCOPE, GROUND_NIGHT_SCOPE})
        self.assertEqual(ground[GROUND_DAY_SCOPE].slots, 3)
        self.assertEqual(ground[GROUND_NIGHT_SCOPE].slots, 1)
        self.assertEqual(SYSTEM_GROUND_PER_DAY, 4)
        # Any base's truck can fill a slot, Plymouth included.
        self.assertEqual(set(ground[GROUND_DAY_SCOPE].units), {"GR", "LG", "MG", "PG"})
        self.assertEqual(set(ground[GROUND_NIGHT_SCOPE].units), {"NG", "NP"})

    def test_extra_units_are_never_planned(self):
        codes = {u for g in planned_groups() for u in g.units}
        self.assertFalse(codes & {"GR2", "NG2", "G9B"})


class CauseTests(unittest.TestCase):
    def test_fully_staffed_has_no_shortfall(self):
        result = find_shortfalls(_all_staffed(DAY), [DAY])
        self.assertEqual(result.shortfalls, [])
        self.assertEqual(result.planned, result.staffed)
        self.assertEqual(result.planned[(GROUND_DAY_SCOPE, "GR")], 3)
        self.assertEqual(result.planned[(GROUND_NIGHT_SCOPE, "GR")], 1)

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

    def test_wholly_empty_rotor_wing_is_both(self):
        roles = _all_staffed(DAY)
        del roles[(DAY, "D7P")]
        (s,) = _shorts(roles)
        self.assertEqual(s.cause, CAUSE_BOTH)
        self.assertEqual(s.detail, "RN + Medic + Pilot")


class GroundTargetTests(unittest.TestCase):
    def test_any_three_day_trucks_meet_the_target(self):
        # Four day trucks exist; losing one still leaves the 3 that are wanted.
        roles = _all_staffed(DAY, without=("LG",))
        self.assertEqual(_shorts(roles), [])

    def test_plymouth_counts_like_any_other_truck(self):
        roles = _all_staffed(DAY, without=("LG", "MG"))  # GR + PG + ... only 2
        (s,) = _shorts(roles)
        self.assertEqual(s.day_night, "D")
        roles = _all_staffed(DAY, without=("LG",))
        self.assertEqual(
            find_shortfalls(roles, [DAY]).staffed[(GROUND_DAY_SCOPE, "GR")], 3
        )

    def test_night_truck_can_be_either_base(self):
        self.assertEqual(_shorts(_all_staffed(DAY, without=("NG",))), [])
        self.assertEqual(_shorts(_all_staffed(DAY, without=("NP",))), [])

    def test_no_night_truck_is_one_night_shortfall(self):
        (s,) = _shorts(_all_staffed(DAY, without=("NG", "NP")))
        self.assertEqual(s.day_night, "N")
        self.assertEqual(s.cause, CAUSE_BOTH)

    def test_two_day_trucks_is_one_shortfall_not_per_base(self):
        shorts = _shorts(_all_staffed(DAY, without=("LG", "MG")))
        self.assertEqual(len(shorts), 1)
        self.assertEqual(shorts[0].day_night, "D")

    def test_gr_operator_is_emt_not_pilot(self):
        roles = _all_staffed(DAY, without=("MG",))
        roles[(DAY, "LG")] = {"RN", "Medic"}  # GR staffed, LG missing only EMT
        (s,) = _shorts(roles)
        self.assertEqual(s.unit_code, "LG")
        self.assertEqual(s.cause, CAUSE_OPERATOR)
        self.assertEqual(s.detail, "EMT")

    def test_gr_pilot_row_does_not_satisfy_emt(self):
        roles = _all_staffed(DAY, without=("MG",))
        roles[(DAY, "LG")] = {"RN", "Medic", "PIC"}
        (s,) = _shorts(roles)
        self.assertEqual(s.cause, CAUSE_OPERATOR)

    def test_blames_the_truck_closest_to_staffed(self):
        roles = _all_staffed(DAY, without=("MG",))
        roles[(DAY, "LG")] = {"RN", "Medic"}  # only EMT missing
        (s,) = _shorts(roles)
        self.assertEqual(s.unit_code, "LG")

    def test_planned_and_staffed_are_counted_by_shift(self):
        result = find_shortfalls(
            _all_staffed(DAY, without=("NG", "NP", "LG", "MG")), [DAY]
        )
        self.assertEqual(result.planned[(GROUND_DAY_SCOPE, "GR")], 3)
        self.assertEqual(result.staffed[(GROUND_DAY_SCOPE, "GR")], 2)
        self.assertEqual(result.planned[(GROUND_NIGHT_SCOPE, "GR")], 1)
        self.assertEqual(result.staffed[(GROUND_NIGHT_SCOPE, "GR")], 0)


class NoVehicleTests(unittest.TestCase):
    def test_marked_with_full_crew_counts_as_staffed(self):
        roles = _all_staffed(DAY)
        result = find_shortfalls(roles, [DAY], {(DAY, "D7B"): "aircraft down"})
        self.assertEqual(result.shortfalls, [])
        (nv,) = result.no_vehicle
        self.assertTrue(nv.crew_present)
        self.assertEqual(nv.treatment, "Staffed")
        self.assertEqual(nv.note, "aircraft down")
        # Planned is unchanged: it was staffed all along.
        self.assertEqual(result.planned[("Bedford", "RW")], 2)
        self.assertEqual(result.staffed[("Bedford", "RW")], 2)

    def test_marked_without_crew_comes_off_the_plan(self):
        roles = _all_staffed(DAY)
        del roles[(DAY, "D9L")]
        result = find_shortfalls(roles, [DAY], {(DAY, "D9L"): ""})
        self.assertEqual(result.shortfalls, [])
        (nv,) = result.no_vehicle
        self.assertFalse(nv.crew_present)
        self.assertEqual(nv.treatment, "Removed from planned")
        self.assertEqual(result.planned[("Lawrence", "RW")], 1)
        self.assertEqual(result.staffed[("Lawrence", "RW")], 1)

    def test_unmarked_gap_is_still_reported(self):
        roles = _all_staffed(DAY)
        del roles[(DAY, "D9L")]
        roles[(DAY, "N7B")] = {"Medic", "PIC"}
        (s,) = _shorts(roles, outages={(DAY, "D9L"): ""})
        self.assertEqual(s.unit_code, "N7B")

    def test_one_of_four_day_trucks_down_does_not_lower_the_target(self):
        # Three other day trucks can still be staffed, so the target stays 3.
        roles = _all_staffed(DAY, without=("MG",))
        result = find_shortfalls(roles, [DAY], {(DAY, "MG"): "truck down"})
        self.assertEqual(result.shortfalls, [])
        self.assertEqual(result.planned[(GROUND_DAY_SCOPE, "GR")], 3)

    def test_ground_target_drops_when_too_few_trucks_remain(self):
        roles = _all_staffed(DAY, without=("MG", "LG"))
        outages = {(DAY, "MG"): "", (DAY, "LG"): ""}
        result = find_shortfalls(roles, [DAY], outages)
        self.assertEqual(result.shortfalls, [])
        self.assertEqual(result.planned[(GROUND_DAY_SCOPE, "GR")], 2)
        self.assertEqual(len(result.no_vehicle), 2)

    def test_down_truck_does_not_hide_another_truck_gap(self):
        # NG is down; NP is the only night truck left and it has no crew.
        roles = _all_staffed(DAY, without=("NG", "NP"))
        (s,) = _shorts(roles, outages={(DAY, "NG"): "truck down"})
        self.assertEqual((s.unit_code, s.day_night), ("NP", "N"))

    def test_marked_only_on_its_own_day(self):
        other = DAY + timedelta(days=1)
        roles = {**_all_staffed(DAY), **_all_staffed(other)}
        del roles[(DAY, "D9L")]
        del roles[(other, "D9L")]
        result = find_shortfalls(roles, [DAY, other], {(DAY, "D9L"): ""})
        (s,) = result.shortfalls
        self.assertEqual(s.day, other)


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
            for i in range(7):
                day = (start + timedelta(days=i)).isoformat()
                for g in planned_groups():
                    for unit in g.units:
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

    def test_stored_outage_removes_the_shortfall_and_is_listed(self):
        self._week("2026-10-25", True, drop=("D9L", "PIC"))
        with session_scope(self.db) as session:
            session.add(
                WeeklyVehicleOutage(
                    week_start="2026-10-25",
                    day_date="2026-10-26",
                    unit_code="D9L",
                    note="aircraft in maintenance",
                    created_at="2026-10-27T00:00:00Z",
                )
            )
        result = load_shortfalls(self.db, date(2026, 10, 25), date(2026, 10, 31))
        self.assertEqual(result.shortfalls, [])
        (nv,) = result.no_vehicle
        self.assertEqual((nv.day, nv.unit_code), (date(2026, 10, 26), "D9L"))
        self.assertEqual(nv.note, "aircraft in maintenance")
        self.assertFalse(nv.crew_present)


if __name__ == "__main__":
    unittest.main()
