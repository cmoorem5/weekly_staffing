"""Staffing rate is vehicles fully staffed; pilot vacancies and overnights are derived."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from staffing_tool.db import session_scope
from staffing_tool.metrics import (
    compute_week_metrics,
    uses_current_definitions,
    vehicle_filled_shifts,
)
from staffing_tool.models import (
    BaseConfig,
    WeeklyBaseCoverage,
    WeeklyOpsViewAssignment,
    WeeklyStaffing,
)
from staffing_tool.schedule_import import UNIT_MAP, _parse_ops_view_worksheet
from staffing_tool.shortfall import (
    analyze_range,
    analyze_week,
    find_shortfalls,
    planned_groups,
)
from staffing_tool.week_targets import recompute_week_targets
from tests._temp_db import TempDbTestCase

WEEK = "2026-10-25"
CONFIGS = [
    BaseConfig(base_name="Bedford", rw_total_unit_days=14, gr_total_unit_days=14),
    BaseConfig(base_name="Lawrence", rw_total_unit_days=14, gr_total_unit_days=7),
    BaseConfig(base_name="Mansfield", rw_total_unit_days=7, gr_total_unit_days=7),
    BaseConfig(base_name="Manchester", rw_total_unit_days=7, gr_total_unit_days=0),
    BaseConfig(base_name="Plymouth", rw_total_unit_days=14, gr_total_unit_days=7),
]
# Every planned vehicle staffed: rotor wing 56, ground 28.
FULL_RW = {
    "Bedford": (7, 7),
    "Lawrence": (7, 7),
    "Mansfield": (7, 0),
    "Manchester": (7, 0),
    "Plymouth": (7, 7),
}
FULL_GR = {"Bedford": (7, 7), "Lawrence": (7, 0), "Mansfield": (7, 0)}


def _cov(rw=FULL_RW, gr=FULL_GR, removed=None):
    removed = removed or {}
    rows = []
    for cfg in CONFIGS:
        rd, rn = rw.get(cfg.base_name, (0, 0))
        gd, gn = gr.get(cfg.base_name, (0, 0))
        rows.append(
            WeeklyBaseCoverage(
                week_start=WEEK,
                base_name=cfg.base_name,
                rw_staffed_day=rd,
                rw_staffed_night=rn,
                gr_staffed_day=gd,
                gr_staffed_night=gn,
                rw_staffed_unit_days=rd + rn,
                gr_staffed_unit_days=gd + gn,
                rw_planned_removed=removed.get(cfg.base_name, 0),
            )
        )
    return rows


def _week(**kw):
    base = dict(
        week_start=WEEK,
        filled_day=99,  # stale stored values must be ignored when coverage exists
        filled_night=99,
        overnights_below=0,
        pilot_vacancies=0,
        ot_shifts=0,
        ot_rn=0,
        ot_medic=0,
        ot_emt=0,
        ot_rn_day=0,
        ot_rn_night=0,
        ot_medic_day=0,
        ot_medic_night=0,
        ot_emt_day=0,
        ot_emt_night=0,
        leave_at=0,
        leave_lt=0,
        leave_sick=0,
        leave_loa=0,
        leave_pfml=0,
        leave_jury=0,
        leave_brev=0,
    )
    base.update(kw)
    return WeeklyStaffing(**base)


class VehicleFilledTests(unittest.TestCase):
    def test_each_type_is_capped_at_its_plan(self):
        # 70 rotor wing and 40 ground staffed against plans of 56 and 28.
        day, night = vehicle_filled_shifts(40, 30, 25, 15, 56, 28)
        self.assertEqual(day + night, 56 + 28)

    def test_day_plus_night_always_equals_what_is_counted(self):
        for rw in ((10, 3), (35, 21)):
            for gr in ((0, 0), (5, 2), (30, 10)):
                day, night = vehicle_filled_shifts(*rw, *gr, 56, 28)
                counted = min(sum(rw), 56) + min(sum(gr), 28)
                self.assertEqual(day + night, counted)


class StaffingRateTests(unittest.TestCase):
    def _m(self, **cov):
        return compute_week_metrics(_week(), _cov(**cov), CONFIGS)

    def test_fully_staffed_week_is_100_percent_of_84(self):
        m = self._m()
        self.assertEqual((m.filled_total, m.required_total), (84, 84))
        self.assertAlmostEqual(m.staffing_rate, 1.0)

    def test_extra_trucks_cannot_push_it_past_100(self):
        gr = {"Bedford": (14, 14), "Lawrence": (7, 7), "Mansfield": (7, 7)}
        m = self._m(gr=gr)  # 56 ground shifts staffed against a plan of 28
        self.assertEqual(m.filled_total, 84)
        self.assertAlmostEqual(m.staffing_rate, 1.0)

    def test_a_missing_vehicle_lowers_it_by_one_of_84(self):
        rw = dict(FULL_RW, Mansfield=(6, 0))
        m = self._m(rw=rw)
        self.assertEqual(m.filled_total, 83)
        self.assertAlmostEqual(m.staffing_rate, 83 / 84)

    def test_stale_stored_filled_counts_are_ignored(self):
        self.assertEqual(self._m().filled_day + self._m().filled_night, 84)

    def test_aircraft_out_of_service_with_no_crew_is_off_the_plan(self):
        # Mansfield's aircraft was down one day with no crew: 6 staffed of a plan of 6.
        rw = dict(FULL_RW, Mansfield=(6, 0))
        m = self._m(rw=rw, removed={"Mansfield": 1})
        self.assertEqual((m.filled_total, m.required_total), (83, 83))
        self.assertAlmostEqual(m.staffing_rate, 1.0)
        self.assertEqual(m.plan_removed_unit_days, 1)

    def test_ot_dependency_uses_vehicles_staffed(self):
        rw = dict(FULL_RW, Mansfield=(4, 0))  # 80 vehicles staffed
        m = compute_week_metrics(_week(ot_rn_day=8), _cov(rw=rw), CONFIGS)
        self.assertEqual(m.filled_total, 81)
        self.assertAlmostEqual(m.ot_dependency, 8 / 81)

    def test_a_week_with_no_coverage_keeps_its_stored_counts(self):
        m = compute_week_metrics(_week(filled_day=50, filled_night=20), [], CONFIGS)
        self.assertEqual(m.filled_total, 70)
        self.assertAlmostEqual(m.staffing_rate, 70 / 84)


def _ops_rw_sheet(roles):
    wb = Workbook()
    ws = wb.active
    ws.title = "OPS View"
    start = datetime.strptime(WEEK, "%Y-%m-%d")
    for i in range(7):
        ws.cell(row=1, column=3 + i, value=start + timedelta(days=i))
    ws.cell(row=4, column=1, value="D7B")
    for offset, role in enumerate(roles):
        ws.cell(row=4 + offset, column=2, value=role)
        for i in range(7):
            ws.cell(row=4 + offset, column=3 + i, value=f"{role}{offset}")
    return ws


class RotorWingPilotRuleTests(unittest.TestCase):
    def test_an_emt_never_substitutes_for_a_pilot_on_the_ops_view(self):
        rw_day, *_ = _parse_ops_view_worksheet(
            _ops_rw_sheet(["RN", "Medic", "EMT"]), WEEK
        )
        self.assertEqual(rw_day.get("Bedford", 0), 0)

    def test_a_pilot_row_staffs_the_aircraft(self):
        rw_day, *_ = _parse_ops_view_worksheet(
            _ops_rw_sheet(["RN", "Medic", "PIC"]), WEEK
        )
        self.assertEqual(rw_day.get("Bedford"), 7)

    def test_shortfall_treats_an_emt_as_no_pilot(self):
        day = date(2026, 10, 25)
        roles = {
            (day, u): {"RN", "Medic", "PIC"}
            if UNIT_MAP[u][1] == "RW"
            else {"RN", "Medic", "EMT"}
            for g in planned_groups()
            for u in g.units
        }
        roles[(day, "D9L")] = {"RN", "Medic", "EMT"}
        (s,) = find_shortfalls(roles, [day]).shortfalls
        self.assertEqual((s.unit_code, s.operator_missing), ("D9L", True))


class AuditNameCheckTests(unittest.TestCase):
    @staticmethod
    def _check():
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "audit_numbers.py"
        spec = importlib.util.spec_from_file_location("audit_numbers", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.looks_like_a_name

    def test_names_pass_and_codes_do_not(self):
        looks = self._check()
        for name in ("Smith", "O'Brien", "De La Cruz", "Li", "Mary-Ann", "Huron"):
            self.assertTrue(looks(name), name)
        for code in ("", "0", "12", "LT", "sick", "OPEN", "N/A", "X", "AT8", "--"):
            self.assertFalse(looks(code), code)


class LastFiscalYearUntouchedTests(TempDbTestCase):
    """Weeks before FY2027 keep the numbers exactly as they were reported."""

    OLD = "2026-09-20"  # last week of FY2026
    NEW = "2026-09-27"  # first week of FY2027

    def test_the_cutoff_is_week_one_of_fy2027(self):
        self.assertFalse(uses_current_definitions(self.OLD))
        self.assertTrue(uses_current_definitions(self.NEW))
        self.assertFalse(uses_current_definitions("2025-10-05"))

    def test_an_old_week_keeps_its_stored_crew_counts(self):
        cov = _cov()
        for c in cov:
            c.week_start = self.OLD
        w = _week(week_start=self.OLD, filled_day=50, filled_night=24)
        m = compute_week_metrics(w, cov, CONFIGS)
        self.assertEqual((m.filled_day, m.filled_night), (50, 24))
        self.assertEqual(m.required_total, 84)
        self.assertAlmostEqual(m.staffing_rate, 74 / 84)
        self.assertEqual(m.plan_removed_unit_days, 0)

    def test_the_first_week_of_fy2027_uses_vehicles_fully_staffed(self):
        cov = _cov()
        for c in cov:
            c.week_start = self.NEW
        m = compute_week_metrics(_week(week_start=self.NEW), cov, CONFIGS)
        self.assertEqual(m.filled_total, 84)

    def test_recompute_never_touches_an_old_week(self):
        db = self.make_temp_db()
        with session_scope(db) as session:
            session.add(
                _week(
                    week_start=self.OLD,
                    filled_day=50,
                    filled_night=24,
                    pilot_vacancies=0,
                    overnights_below=0,
                )
            )
            session.flush()
            day = date.fromisoformat(self.OLD).isoformat()
            session.add(
                WeeklyOpsViewAssignment(
                    week_start=self.OLD,
                    day_date=day,
                    unit_code="D9L",
                    role="RN",
                    person_display="N",
                    raw_value="N",
                    is_staffed=1,
                )
            )
        with session_scope(db) as session:
            self.assertFalse(recompute_week_targets(session, self.OLD))
            w = session.query(WeeklyStaffing).one()
            self.assertEqual(
                (w.filled_day, w.filled_night, w.pilot_vacancies, w.overnights_below),
                (50, 24, 0, 0),
            )
            self.assertIsNone(w.gr_target_planned)

    def test_reasons_are_not_offered_for_last_fy(self):
        db = self.make_temp_db()
        with session_scope(db) as session:
            session.add(_week(week_start=self.OLD))
            session.flush()
            session.add(
                WeeklyOpsViewAssignment(
                    week_start=self.OLD,
                    day_date=self.OLD,
                    unit_code="D9L",
                    role="RN",
                    person_display="N",
                    raw_value="N",
                    is_staffed=1,
                )
            )
        with session_scope(db) as session:
            self.assertIsNone(analyze_week(session, self.OLD, tracked_only=True))
            # Untracked (the shortfall script) can still inspect any week.
            self.assertIsNotNone(analyze_week(session, self.OLD))
            empty = analyze_range(
                session, date(2026, 9, 1), date(2026, 9, 26), tracked_only=True
            )
            self.assertEqual(empty.days_analyzed, 0)


class DerivedKpiTests(TempDbTestCase):
    """Pilot vacancies and overnights below coverage come from the schedule."""

    def setUp(self):
        self.db = self.make_temp_db()
        with session_scope(self.db) as session:
            session.add(_week())
            session.flush()
            start = date.fromisoformat(WEEK)
            for i in range(7):
                day = (start + timedelta(days=i)).isoformat()
                for g in planned_groups():
                    for unit in g.units:
                        rw = UNIT_MAP[unit][1] == "RW"
                        for role in ("RN", "Medic", "PIC" if rw else "EMT"):
                            # No pilot on D9L on days 1 and 2; no night RN on N7B day 4.
                            if (unit, role, i) in (
                                ("D9L", "PIC", 1),
                                ("D9L", "PIC", 2),
                                ("N7B", "RN", 4),
                            ):
                                continue
                            session.add(
                                WeeklyOpsViewAssignment(
                                    week_start=WEEK,
                                    day_date=day,
                                    unit_code=unit,
                                    role=role,
                                    person_display="N",
                                    raw_value="N",
                                    is_staffed=1,
                                )
                            )
            for cov in _cov(rw={}, gr={}):
                session.add(cov)

    def _stored(self):
        with session_scope(self.db) as session:
            w = session.query(WeeklyStaffing).one()
            return w.pilot_vacancies, w.overnights_below, w.filled_day, w.filled_night

    def test_counts_pilot_days_and_nights_short(self):
        with session_scope(self.db) as session:
            self.assertTrue(recompute_week_targets(session, WEEK))
        pilot, overnights, *_ = self._stored()
        self.assertEqual(pilot, 2)  # D9L with no pilot twice
        self.assertEqual(overnights, 1)  # N7B short one night

    def test_a_day_fixed_by_hand_is_no_longer_a_vacancy(self):
        # Lawrence stored one above the schedule: one of the two pilot days was fixed.
        with session_scope(self.db) as session:
            lawrence = (
                session.query(WeeklyBaseCoverage).filter_by(base_name="Lawrence").one()
            )
            lawrence.rw_staffed_day = 5  # schedule supports 5 + 7 = 12 of 14
            lawrence.rw_staffed_night = 8
            lawrence.rw_staffed_unit_days = 13
            recompute_week_targets(session, WEEK)
        pilot, *_ = self._stored()
        self.assertEqual(pilot, 1)

    def test_a_week_without_detail_keeps_its_stored_values(self):
        with session_scope(self.db) as session:
            session.query(WeeklyOpsViewAssignment).delete()
            w = session.query(WeeklyStaffing).one()
            w.pilot_vacancies = 3
            w.overnights_below = 2
            self.assertFalse(recompute_week_targets(session, WEEK))
        pilot, overnights, *_ = self._stored()
        self.assertEqual((pilot, overnights), (3, 2))


if __name__ == "__main__":
    unittest.main()
