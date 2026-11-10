"""Shift table: required / OOS / net plan / staffed / extra / gap, availability, flags."""

from __future__ import annotations

import unittest
from datetime import date

from staffing_tool.metrics import WeekMetrics, vehicle_filled_shifts
from staffing_tool.schedule_import import UNIT_MAP
from staffing_tool.shift_table import (
    SHIFT_TABLE_HEADERS,
    build_shift_table,
    shift_table_rows,
)
from staffing_tool.shortfall import find_shortfalls, planned_groups

DAY = date(2026, 10, 26)
FULL_RW = {"RN", "Medic", "PIC"}
FULL_GR = {"RN", "Medic", "EMT"}


def _metrics(rw_d, rw_n, gr_d, gr_n, *, rw_plan=56, gr_plan=28, removed_night=0):
    """WeekMetrics for given staffed counts, built the way compute_week_metrics does."""
    filled_day, filled_night = vehicle_filled_shifts(
        rw_d, rw_n, gr_d, gr_n, rw_plan, gr_plan
    )
    removed = (56 - rw_plan) + (28 - gr_plan)
    req_night = 28 - removed_night
    req_day = 56 - (removed - removed_night)
    total = req_day + req_night
    return WeekMetrics(
        week_start="2026-10-25",
        required_day=req_day,
        required_night=req_night,
        required_total=total,
        filled_day=filled_day,
        filled_night=filled_night,
        filled_total=filled_day + filled_night,
        vacancies=max(0, total - filled_day - filled_night),
        staffing_rate=(filled_day + filled_night) / total,
        ot_shifts=0,
        ot_dependency=0.0,
        leave_total=0,
        leave_exposure=0.0,
        overnights_below=0,
        pilot_vacancies=0,
        rw_total_unit_days=rw_plan,
        gr_total_unit_days=28,
        rw_staffed_unit_days=min(rw_d + rw_n, rw_plan),
        gr_staffed_unit_days=min(gr_d + gr_n, gr_plan),
        system_rw_pct=0.0,
        system_gr_pct=0.0,
        rw_day_staffed=rw_d,
        rw_night_staffed=rw_n,
        gr_day_staffed=gr_d,
        gr_night_staffed=gr_n,
    )


def _roles(*, without=(), extra_ground=()):
    roles = {}
    for g in planned_groups():
        # Ground staffs exactly its daily plan; a spare truck is passed in
        # ``extra_ground``. (Rotor units equal the base's daily plan.)
        units = g.units[: g.slots] if g.service_type == "GR" else g.units
        for unit in units:
            if unit in without:
                continue
            roles[(DAY, unit)] = set(FULL_RW if UNIT_MAP[unit][1] == "RW" else FULL_GR)
    for unit in extra_ground:
        roles[(DAY, unit)] = set(FULL_GR)
    return roles


class CellTests(unittest.TestCase):
    def test_five_rotor_days_down_with_the_rest_staffed_is_full_fill(self):
        # 30 day rotor shifts staffed, 5 with no aircraft and the crew released.
        from staffing_tool.shortfall import ShortfallResult

        result = ShortfallResult()
        result.planned_removed_cell[("RW", "D")] = 5
        t = build_shift_table(_metrics(30, 21, 21, 7, rw_plan=51), result)
        day_rotor = t.cells[0]
        self.assertEqual(
            (day_rotor.required, day_rotor.removed, day_rotor.net_plan), (35, 5, 30)
        )
        self.assertEqual(
            (day_rotor.staffed, day_rotor.extra, day_rotor.gap), (30, 0, 0)
        )
        self.assertEqual(day_rotor.fill, 1.0)
        self.assertEqual(t.net_plan, 79)
        self.assertAlmostEqual(t.staffing_rate, 1.0)

    def test_cells_cover_the_full_plan(self):
        t = build_shift_table(_metrics(0, 0, 0, 0), find_shortfalls({}, []))
        self.assertEqual([c.required for c in t.cells], [35, 21, 21, 7])
        self.assertEqual(sum(c.required for c in t.cells), 84)

    def test_spare_night_truck_is_extra_and_caps_the_cell_at_100(self):
        # Day trucks short by one, one spare night truck: the pooled ground total
        # is full, but the night cell reads 100% with +1 extra, never 114%.
        t = build_shift_table(_metrics(35, 21, 20, 8), find_shortfalls({}, []))
        gr_day, gr_night = t.cells[1], t.cells[3]
        self.assertEqual((gr_day.gap, gr_day.fill), (1, 20 / 21))
        self.assertEqual((gr_night.extra, gr_night.fill), (1, 1.0))
        self.assertTrue(
            any("Ground: 1 staffed above plan (night +1)" in f for f in t.flags)
        )
        self.assertTrue(any("offset 1 of 1 short ground" in f for f in t.flags))
        # The total row nets the extra against the gap: 84 of 84.
        total = shift_table_rows(t)[-1]
        self.assertEqual(total[0], "Total")
        self.assertEqual(
            (total[4], total[5], total[6], total[7]), ("84", "0", "0", "100.0%")
        )

    def test_total_row_reports_extra_that_was_not_needed(self):
        t = build_shift_table(_metrics(35, 21, 22, 8), find_shortfalls({}, []))
        total = shift_table_rows(t)[-1]
        self.assertEqual((total[4], total[5], total[6]), ("86", "2", "0"))

    def test_rows_match_headers(self):
        t = build_shift_table(_metrics(35, 21, 21, 7), find_shortfalls({}, []))
        rows = shift_table_rows(t)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(len(r) == len(SHIFT_TABLE_HEADERS) for r in rows))

    def test_fill_is_none_when_nothing_was_planned(self):
        from staffing_tool.shortfall import ShortfallResult

        result = ShortfallResult()
        result.planned_removed_cell[("GR", "N")] = 7
        t = build_shift_table(
            _metrics(35, 21, 21, 0, gr_plan=21, removed_night=7), result
        )
        self.assertIsNone(t.cells[3].fill)
        self.assertEqual(shift_table_rows(t)[3][-1], "—")


class OutOfServiceTests(unittest.TestCase):
    def test_shift_level_removal_comes_off_that_cell_only(self):
        # Plymouth night aircraft down, crew released: night rotor loses one,
        # day rotor is untouched.
        roles = _roles(without=("N7P",))
        result = find_shortfalls(roles, [DAY], {(DAY, "N7P"): "aircraft down"})
        self.assertEqual(result.planned_removed_cell[("RW", "N")], 1)
        self.assertEqual(result.planned_removed_cell[("RW", "D")], 0)

    def test_crewed_but_down_counts_staffed_but_not_available(self):
        roles = _roles()
        result = find_shortfalls(roles, [DAY], {(DAY, "D7P"): "aircraft down"})
        self.assertEqual(sum(1 for n in result.no_vehicle if n.crew_present), 1)
        t = build_shift_table(_metrics(35, 21, 21, 7), result)
        self.assertEqual(t.counted, 84)
        self.assertEqual(t.crewed_out_of_service, 1)
        self.assertEqual(t.available, 83)
        self.assertAlmostEqual(t.availability_rate, 83 / 84)
        self.assertIn("83 of 84", t.availability_line)
        self.assertTrue(any("kept the crew" in f for f in t.flags))

    def test_released_crew_is_not_double_subtracted_from_availability(self):
        roles = _roles(without=("D7P",))
        result = find_shortfalls(roles, [DAY], {(DAY, "D7P"): "aircraft down"})
        t = build_shift_table(_metrics(34, 21, 21, 7, rw_plan=55), result)
        self.assertEqual(t.crewed_out_of_service, 0)
        self.assertEqual(t.available, t.counted)

    def test_no_marks_means_no_out_of_service_flag(self):
        t = build_shift_table(_metrics(35, 21, 21, 7), find_shortfalls(_roles(), [DAY]))
        self.assertEqual(t.flags, [])


class WeatherSwapFlagTests(unittest.TestCase):
    def test_rotor_short_on_a_day_ground_had_a_spare_crew_is_flagged(self):
        # A rotor day unit is unstaffed while a fourth day truck is staffed.
        roles = _roles(without=("D7P",), extra_ground=("PG",))
        result = find_shortfalls(roles, [DAY])
        self.assertEqual([e[3] for e in result.extra_days], [1])
        t = build_shift_table(_metrics(34, 21, 22, 7), result)
        flags = " ".join(t.flags)
        self.assertIn("Mon Oct 26: rotor was short and ground had a spare crew", flags)

    def test_spare_ground_alone_is_not_a_weather_flag(self):
        result = find_shortfalls(_roles(extra_ground=("PG",)), [DAY])
        t = build_shift_table(_metrics(35, 21, 22, 7), result)
        self.assertFalse(any("rotor was short" in f for f in t.flags))

    def test_hand_correction_notes_pass_through(self):
        t = build_shift_table(
            _metrics(35, 21, 21, 7),
            find_shortfalls(_roles(), [DAY]),
            ["Bedford rotor wing: stored 14, schedule shows 13."],
        )
        self.assertIn("Bedford rotor wing: stored 14, schedule shows 13.", t.flags)


class DayNightRateCapTests(unittest.TestCase):
    def test_spare_night_truck_no_longer_reads_over_100_percent(self):
        from staffing_tool.db import session_scope  # noqa: F401  (import guard)
        from staffing_tool.metrics import compute_week_metrics
        from staffing_tool.models import BaseConfig, WeeklyBaseCoverage, WeeklyStaffing

        row = WeeklyStaffing(
            week_start="2026-10-25",
            filled_day=0,
            filled_night=0,
            overnights_below=0,
            pilot_vacancies=0,
        )
        covs = [
            WeeklyBaseCoverage(
                week_start="2026-10-25",
                base_name="Bedford",
                rw_staffed_unit_days=56,
                rw_staffed_day=35,
                rw_staffed_night=21,
                gr_staffed_unit_days=28,
                gr_staffed_day=20,
                gr_staffed_night=8,
            )
        ]
        cfgs = [
            BaseConfig(
                base_name="Bedford",
                rw_total_unit_days=56,
                gr_total_unit_days=28,
            )
        ]
        m = compute_week_metrics(row, covs, cfgs)
        self.assertLessEqual(m.night_staffing_rate, 1.0)
        self.assertLessEqual(m.day_staffing_rate, 1.0)
        self.assertEqual(m.filled_total, 84)
        self.assertEqual(
            (
                m.rw_day_staffed,
                m.rw_night_staffed,
                m.gr_day_staffed,
                m.gr_night_staffed,
            ),
            (35, 21, 20, 8),
        )


class ReportRenderTests(unittest.TestCase):
    def _table(self):
        return build_shift_table(_metrics(35, 21, 20, 8), find_shortfalls({}, []))

    def test_html_block_has_every_row_and_the_availability_line(self):
        from staffing_tool import report_html as rh

        t = self._table()
        html = rh.shift_table_block(shift_table_rows(t), t.availability_line)
        for label in (
            "Day rotor",
            "Day ground",
            "Night rotor",
            "Night ground",
            "Total",
        ):
            self.assertIn(label, html)
        self.assertIn("Available for calls", html)

    def test_html_block_is_empty_without_detail(self):
        from staffing_tool import report_html as rh

        self.assertEqual(rh.shift_table_block([], ""), "")

    def test_pdf_flowables_build_and_skip_without_detail(self):
        from staffing_tool import report_style as style

        t = self._table()
        self.assertEqual(style.shift_table_flowables([], ""), [])
        self.assertEqual(
            len(style.shift_table_flowables(shift_table_rows(t), t.availability_line)),
            5,
        )


if __name__ == "__main__":
    unittest.main()
