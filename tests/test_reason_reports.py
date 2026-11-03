"""'Why coverage fell short' rows and the ground share column in the reports."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from staffing_tool import report_html as rh
from staffing_tool import report_style as style
from staffing_tool.db import session_scope
from staffing_tool.models import (
    WeeklyBaseCoverage,
    WeeklyOpsViewAssignment,
    WeeklyStaffing,
)
from staffing_tool.schedule_import import UNIT_MAP
from staffing_tool.shortfall import (
    REASON_TABLE_HEADERS,
    find_shortfalls,
    planned_groups,
    reason_table_rows,
)
from staffing_tool.weekly_pdf_report import build_html, load_week_report_data
from tests._temp_db import TempDbTestCase

WEEK = "2026-10-25"
DAY = date(2026, 10, 25)
FULL_RW = {"RN", "Medic", "PIC"}
FULL_GR = {"RN", "Medic", "EMT"}


def _all_staffed(day):
    return {
        (day, u): set(FULL_RW if UNIT_MAP[u][1] == "RW" else FULL_GR)
        for g in planned_groups()
        for u in g.units
    }


class ReasonRowTests(unittest.TestCase):
    def test_no_detail_means_no_block(self):
        self.assertEqual(reason_table_rows(None), [])
        self.assertEqual(rh.reasons_block([]), "")
        self.assertEqual(style.reasons_flowables([]), [])

    def test_rows_split_by_vehicle_type_with_dashes_for_absent_seats(self):
        roles = _all_staffed(DAY)
        roles[(DAY, "D9L")] = {"RN", "Medic"}  # rotor wing, no pilot
        del roles[(DAY, "LG")]  # ground day truck gone: 3 left of 4, still met
        del roles[(DAY, "MG")]  # now 2 of 3: one short, closest is empty
        rows = reason_table_rows(find_shortfalls(roles, [DAY]))
        by_label = {r[0]: r[1:] for r in rows}
        self.assertEqual(by_label["Short vehicle-days"], ["1", "1"])
        self.assertEqual(by_label["Short a pilot"], ["1", "—"])
        self.assertEqual(by_label["Short an EMT"], ["—", "1"])
        self.assertEqual(by_label["Short a nurse"], ["0", "1"])

    def test_html_block_has_the_headers_and_the_counting_note(self):
        rows = reason_table_rows(find_shortfalls(_all_staffed(DAY), [DAY]))
        html = rh.reasons_block(rows)
        self.assertIn("WHY COVERAGE FELL SHORT", html)
        self.assertIn("Rotor wing", html)
        self.assertIn("never counted against staffing", html)
        self.assertEqual(REASON_TABLE_HEADERS, ["", "Rotor wing", "Ground"])


class WeeklyReportTests(TempDbTestCase):
    def setUp(self):
        self.db = self.make_temp_db()
        with session_scope(self.db) as session:
            session.add(
                WeeklyStaffing(
                    week_start=WEEK,
                    filled_day=52,
                    filled_night=30,
                    gr_target_planned=28,
                )
            )
            session.flush()
            start = date.fromisoformat(WEEK)
            for i in range(7):
                day = (start + timedelta(days=i)).isoformat()
                for g in planned_groups():
                    for unit in g.units:
                        if unit == "LG" and i < 3:
                            continue  # ground day gaps
                        roles = FULL_RW if UNIT_MAP[unit][1] == "RW" else FULL_GR
                        for role in roles:
                            if unit == "D9L" and i == 1 and role == "PIC":
                                continue  # no pilot Monday
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
            for base, gr in (("Bedford", 14), ("Lawrence", 4), ("Mansfield", 6)):
                session.add(
                    WeeklyBaseCoverage(
                        week_start=WEEK,
                        base_name=base,
                        rw_staffed_unit_days=0,
                        gr_staffed_unit_days=gr,
                        gr_staffed_day=gr,
                    )
                )

    def test_weekly_context_carries_reasons_and_ground_share(self):
        ctx = load_week_report_data(self.db, WEEK)
        by_label = {r[0]: r[1:] for r in ctx.reasons}
        self.assertEqual(by_label["Short a pilot"][0], "1")
        shares = {r[0]: r[4] for r in ctx.base_coverage}
        # 24 ground shifts staffed: Bedford 14, Lawrence 4, Mansfield 6.
        self.assertEqual(shares["Bedford"], "58.3%")
        self.assertEqual(shares["Lawrence"], "16.7%")
        self.assertEqual(shares["Mansfield"], "25.0%")
        self.assertEqual(shares["Manchester"], "—")

    def test_weekly_html_includes_the_block_and_share_header(self):
        import os
        import tempfile

        ctx = load_week_report_data(self.db, WEEK)
        with tempfile.TemporaryDirectory() as tmp:
            path = build_html(ctx, os.path.join(tmp, "w.html"))
            with open(path, encoding="utf-8") as fh:
                html = fh.read()
        self.assertIn("WHY COVERAGE FELL SHORT", html)
        self.assertIn("GR Share %", html)
        self.assertNotIn("GR Avail %", html)


if __name__ == "__main__":
    unittest.main()
