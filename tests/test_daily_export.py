"""Day-level research export: seat counts, open seats, fatigue, de-identification."""

import unittest
from datetime import date

from staffing_tool.daily_export import (
    REQUIRED_SEATS_PER_DAY,
    SHIFT_HEADER,
    daily_header,
    load_daily_export,
    shift_window,
)
from staffing_tool.db import session_scope
from staffing_tool.metrics import ROLE_CAPACITY_PER_WEEK
from staffing_tool.models import StaffRosterEntry, WeeklyPersonShift, WeeklyStaffing
from tests._temp_db import TempDbTestCase

WEEK = "2026-07-05"  # Sunday
DAY = "2026-07-06"  # Monday
SALT = b"test-salt"


def _shift(**kw):
    base = dict(
        week_start=WEEK,
        person_display="Doe, Jane",
        shift_date=DAY,
        role="EMT",
        event_type="staffed",
        base_name="Bedford",
        service_type="GR",
        day_night="D",
        unit_code="GR",
        source_tab="EMT",
        source_cell="C5",
        included_in_aggregates=1,
    )
    base.update(kw)
    return WeeklyPersonShift(**base)


def _ann(**kw):
    return _shift(
        role="RN",
        person_display="Nurse, Ann",
        staff_member_id=3,
        service_type="RW",
        unit_code="D7B",
        source_tab="RN",
        **kw,
    )


def _col(export, day, name):
    row = next(r for r in export.daily if r[0] == day)
    return row[export.daily_header.index(name)]


def _sc(row, name):
    return row[SHIFT_HEADER.index(name)]


class DailyExportTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        with session_scope(self.db_path) as s:
            for ws in ("2026-06-28", WEEK):
                s.add(WeeklyStaffing(week_start=ws, filled_day=0, filled_night=0))
            for sid, last, role in (
                (1, "Doe", "EMT"),
                (2, "Roe", "EMT"),
                (3, "Nurse", "RN"),
                (4, "Out", "RN"),
                (5, "Extra", "RN"),
            ):
                s.add(StaffRosterEntry(id=sid, last_name=last, role=role))
            s.flush()
            s.add_all(
                [
                    # Pair-staffed EMT seat: two people, one grid cell.
                    _shift(person_display="Doe, Jane", staff_member_id=1),
                    _shift(person_display="Roe, Rick", staff_member_id=2),
                    # Ann: Saturday (lookback), Sunday, then Monday OT day + night.
                    _ann(
                        week_start="2026-06-28",
                        shift_date="2026-07-04",
                        source_cell="B1",
                    ),
                    _ann(shift_date=WEEK, source_cell="B2"),
                    _ann(
                        event_type="ot",
                        overtime=1,
                        source_cell="D10",
                    ),
                    _ann(day_night="N", source_cell="D9"),
                    _shift(
                        role="RN",
                        person_display="Out, Otto",
                        staff_member_id=4,
                        event_type="leave",
                        leave_type="LT-D",
                        base_name="",
                        service_type="",
                        day_night="",
                        unit_code="",
                        source_tab="RN",
                        source_cell="D11",
                    ),
                    # Extra unit stays out of seat totals but is listed.
                    _shift(
                        role="RN",
                        person_display="Extra, Ed",
                        staff_member_id=5,
                        unit_code="GR2",
                        source_tab="RN",
                        source_cell="D12",
                    ),
                    # Manager rows are dropped from everything.
                    _shift(
                        role="RN",
                        person_display="Boss, Bea",
                        is_manager_row=1,
                        included_in_aggregates=0,
                        source_tab="RN",
                        source_cell="D13",
                    ),
                    _shift(
                        role="RN",
                        person_display="Boss, Bea",
                        is_manager_row=1,
                        event_type="leave",
                        leave_type="AT",
                        source_tab="RN",
                        source_cell="D14",
                    ),
                ]
            )

    def _load(self, **kw):
        kw.setdefault("salt", SALT)
        return load_daily_export(
            self.db_path, date(2026, 7, 5), date(2026, 7, 11), **kw
        )

    def test_required_seats_match_weekly_capacity(self):
        for role, slots in REQUIRED_SEATS_PER_DAY.items():
            self.assertEqual(7 * sum(slots.values()), ROLE_CAPACITY_PER_WEEK[role])

    def test_seats_dedupe_split_and_open(self):
        e = self._load()
        self.assertEqual(_col(e, DAY, "emt_worked_day"), 1)
        self.assertEqual(_col(e, DAY, "emt_open_day"), 3)
        self.assertEqual(_col(e, DAY, "rn_worked_night"), 1)
        self.assertEqual(_col(e, DAY, "rn_open_night"), 3)
        self.assertEqual(_col(e, DAY, "rn_ot_day"), 1)
        self.assertEqual(_col(e, DAY, "rn_worked_day"), 0)
        self.assertEqual(_col(e, DAY, "rn_required_day"), 8)
        self.assertEqual(_col(e, DAY, "rn_open_day"), 7)

    def test_managers_excluded_everywhere(self):
        e = self._load()
        self.assertEqual(_col(e, DAY, "leave_at"), 0)
        self.assertEqual(_col(e, DAY, "leave_lt"), 1)
        self.assertEqual(_col(e, DAY, "rn_leave"), 1)
        self.assertEqual(_col(e, DAY, "leave_total"), 1)
        self.assertNotIn("Boss, Bea", [k[1] for k in e.key])

    def test_shift_rows_times_and_extra_flag(self):
        e = self._load()
        monday = [r for r in e.shifts if r[1] == DAY]
        self.assertEqual(len(monday), 6)
        night = next(r for r in monday if _sc(r, "day_night") == "N")
        self.assertEqual(
            [_sc(night, c) for c in ("shift_start", "shift_end", "ends_next_day")],
            ["19:00", "07:00", "Y"],
        )
        self.assertEqual(sum(1 for r in monday if _sc(r, "extra_unit")), 1)
        self.assertFalse(any(r[1] == "2026-07-04" for r in e.shifts))

    def test_fatigue_uses_lookback_and_start_order(self):
        e = self._load()
        names = {k[0]: k[1] for k in e.key}
        ann = [r for r in e.shifts if names[r[0]] == "Nurse, Ann"]
        sunday = next(r for r in ann if r[1] == WEEK)
        self.assertEqual(_sc(sunday, "consecutive_days"), 2)
        self.assertEqual(_sc(sunday, "hours_since_prev_shift"), 12.0)
        mon_day = next(r for r in ann if r[1] == DAY and _sc(r, "day_night") == "D")
        mon_night = next(r for r in ann if r[1] == DAY and _sc(r, "day_night") == "N")
        self.assertEqual(_sc(mon_day, "hours_since_prev_shift"), 12.0)
        self.assertEqual(_sc(mon_night, "hours_since_prev_shift"), 0.0)
        self.assertEqual(_sc(mon_night, "consecutive_days"), 3)
        leave = next(r for r in e.shifts if _sc(r, "event") == "leave")
        self.assertEqual(_sc(leave, "hours_since_prev_shift"), "")

    def test_ids_random_per_salt_and_keyed(self):
        a = self._load(salt=b"one")
        b = self._load(salt=b"two")
        ids_a = {r[0] for r in a.shifts}
        self.assertTrue(ids_a)
        self.assertFalse(ids_a & {r[0] for r in b.shifts})
        flat = {str(v) for r in a.shifts for v in r}
        self.assertNotIn("Nurse, Ann", flat)
        names = {k[0]: k[1] for k in a.key}
        self.assertEqual(
            len({r[0] for r in a.shifts if names[r[0]] == "Nurse, Ann"}), 1
        )

    def test_weeks_without_detail_are_blank_not_zero(self):
        e = load_daily_export(
            self.db_path, date(2026, 6, 21), date(2026, 7, 11), salt=SALT
        )
        prior = next(r for r in e.daily if r[0] == "2026-06-22")
        self.assertEqual(prior[e.daily_header.index("has_person_detail")], "")
        self.assertEqual(prior[e.daily_header.index("rn_required_day")], "")
        self.assertEqual(e.weeks_with_detail, 2)
        self.assertEqual(e.weeks_in_range, 3)

    def test_role_filter_drops_other_role_columns(self):
        e = self._load(roles=("EMT",))
        self.assertEqual(e.daily_header, daily_header(("EMT",)))
        self.assertFalse(any(h.startswith("rn_") for h in e.daily_header))
        self.assertTrue(all(_sc(r, "role") == "EMT" for r in e.shifts))

    def test_start_times_follow_base(self):
        self.assertEqual(shift_window("Lawrence", "D"), ("09:00", "21:00"))
        self.assertEqual(shift_window("Lawrence", "N"), ("21:00", "09:00"))
        self.assertEqual(shift_window("Mansfield", "D"), ("11:00", "23:00"))
        self.assertEqual(shift_window("Manchester", "N"), ("", ""))
        self.assertEqual(shift_window("Bedford", "D", "G9B"), ("09:00", "21:00"))
        self.assertEqual(shift_window("Bedford", "D", "GR2"), ("07:00", "19:00"))
        self.assertEqual(shift_window("Bedford", "N", "NG2"), ("19:00", "07:00"))


if __name__ == "__main__":
    unittest.main()
