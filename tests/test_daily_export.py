"""Day-level research export: seat dedupe, OT split, leave, de-identification."""

import unittest
from datetime import date

from staffing_tool.daily_export import (
    DAILY_HEADER,
    load_daily_export,
    staff_pseudonym,
)
from staffing_tool.db import session_scope
from staffing_tool.models import WeeklyPersonShift, WeeklyStaffing
from tests._temp_db import TempDbTestCase

WEEK = "2026-07-05"  # Sunday
DAY = "2026-07-06"  # Monday


def _shift(**kw):
    base = dict(
        week_start=WEEK,
        person_display="Doe, Jane",
        shift_date=DAY,
        role="EMT",
        event_type="staffed",
        base_name="Bedford",
        service_type="RW",
        day_night="D",
        unit_code="D7B",
        source_tab="EMT",
        source_cell="C5",
        included_in_aggregates=1,
    )
    base.update(kw)
    return WeeklyPersonShift(**base)


class DailyExportTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        with session_scope(self.db_path) as s:
            s.add(WeeklyStaffing(week_start=WEEK, filled_day=0, filled_night=0))
            s.flush()
            s.add_all(
                [
                    # Pair-staffed EMT seat: two people, one grid cell.
                    _shift(person_display="Doe, Jane"),
                    _shift(person_display="Roe, Rick"),
                    _shift(role="RN", day_night="N", source_tab="RN", source_cell="D9"),
                    _shift(
                        role="RN",
                        event_type="ot",
                        source_tab="RN",
                        source_cell="D10",
                        overtime=1,
                    ),
                    _shift(
                        role="RN",
                        event_type="leave",
                        leave_type="LT-D",
                        service_type="",
                        day_night="",
                        unit_code="",
                        source_cell="D11",
                        source_tab="RN",
                    ),
                    # Extra unit stays out of seat totals but is listed.
                    _shift(
                        role="RN", unit_code="GR2", source_tab="RN", source_cell="D12"
                    ),
                ]
            )

    def _row(self, export, header_name):
        monday = next(r for r in export.daily if r[0] == DAY)
        return monday[DAILY_HEADER.index(header_name)]

    def test_seats_dedupe_and_split(self):
        e = load_daily_export(self.db_path, date(2026, 7, 5), date(2026, 7, 11))
        self.assertEqual(self._row(e, "emt_worked_day"), 1)
        self.assertEqual(self._row(e, "rn_worked_night"), 1)
        self.assertEqual(self._row(e, "rn_ot_day"), 1)
        self.assertEqual(self._row(e, "rn_worked_day"), 0)
        self.assertEqual(self._row(e, "leave_lt"), 1)
        self.assertEqual(self._row(e, "leave_total"), 1)
        self.assertEqual(self._row(e, "rw_seats_worked"), 2)

    def test_shift_rows_keep_every_person_and_times(self):
        e = load_daily_export(self.db_path, date(2026, 7, 5), date(2026, 7, 11))
        self.assertEqual(len(e.shifts), 6)
        night = next(r for r in e.shifts if r[8] == "N")
        self.assertEqual(night[9:12], ["19:00", "07:00", "Y"])
        self.assertEqual(sum(1 for r in e.shifts if r[-1] == "Y"), 1)

    def test_deidentified_by_default(self):
        e = load_daily_export(self.db_path, date(2026, 7, 5), date(2026, 7, 11))
        flat = {str(v) for r in e.shifts for v in r}
        self.assertNotIn("Doe, Jane", flat)
        e2 = load_daily_export(
            self.db_path, date(2026, 7, 5), date(2026, 7, 11), include_names=True
        )
        self.assertEqual(e2.shift_header[0], "person")
        self.assertEqual(e2.shifts[0][0], "Doe, Jane")

    def test_weeks_without_detail_are_blank_not_zero(self):
        e = load_daily_export(self.db_path, date(2026, 6, 28), date(2026, 7, 11))
        prior = next(r for r in e.daily if r[0] == "2026-06-29")
        self.assertEqual(prior[DAILY_HEADER.index("has_person_detail")], "")
        self.assertEqual(prior[DAILY_HEADER.index("emt_worked_day")], "")
        self.assertEqual(len(e.daily), 14)

    def test_role_filter(self):
        e = load_daily_export(
            self.db_path, date(2026, 7, 5), date(2026, 7, 11), roles=("EMT",)
        )
        self.assertTrue(all(r[4] == "EMT" for r in e.shifts))

    def test_pseudonym_stable(self):
        self.assertEqual(staff_pseudonym(7, "x"), "S0007")
        self.assertEqual(staff_pseudonym(None, "A B"), staff_pseudonym(None, "a b"))


if __name__ == "__main__":
    unittest.main()
