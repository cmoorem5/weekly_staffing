"""Tests for person-level ops reporting."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from staffing_tool.db import (
    _get_engine_cached,
    _sessionmaker_for_path,
    get_engine,
    init_db,
    session_scope,
)
from staffing_tool.models import StaffRosterEntry, WeeklyPersonShift, WeeklyStaffing
from staffing_tool.person_names import person_sort_key
from staffing_tool.person_ops import (
    list_distinct_persons,
    list_staff_roster_persons,
    load_person_ops_detail,
    load_person_ops_summary,
)
from staffing_tool.schedule_import import ShiftRecord, weekly_person_shift_mappings
from staffing_tool.staff_roster import staff_roster_index_from_session


def _staffed(
    *,
    person: str = "Smith, Jane",
    shift_date: date | None = None,
    service_type: str = "RW",
    unit: str = "D7B",
    overtime: bool = False,
) -> ShiftRecord:
    d = shift_date or date(2026, 5, 25)
    return ShiftRecord(
        date=d,
        base="Bedford",
        service_type=service_type,
        day_night="D",
        role="RN",
        filled=True,
        overtime=overtime,
        leave_type=None,
        source_tab="RN & Medic",
        source_cell="C5",
        raw_value=unit + ("c" if overtime else ""),
        unit_code=unit,
        person_display=person,
    )


class WeeklyPersonShiftMappingsTests(unittest.TestCase):
    def test_maps_staffed_leave_and_ot(self):
        records = [
            _staffed(person="Smith, Jane", service_type="RW"),
            _staffed(
                person="Smith, Jane",
                shift_date=date(2026, 5, 26),
                service_type="GR",
                unit="GR",
            ),
            _staffed(
                person="Smith, Jane",
                shift_date=date(2026, 5, 27),
                service_type="RW",
                overtime=True,
            ),
            ShiftRecord(
                date=date(2026, 5, 28),
                base="",
                service_type="",
                day_night="D",
                role="RN",
                filled=False,
                overtime=False,
                leave_type="SICK",
                source_tab="RN & Medic",
                source_cell="C8",
                raw_value="SICK",
                person_display="Smith, Jane",
            ),
            ShiftRecord(
                date=date(2026, 5, 29),
                base="Bedford",
                service_type="RW",
                day_night="D",
                role="RN",
                filled=True,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic",
                source_cell="C9",
                raw_value="D7B",
                unit_code="D7B",
                person_display="",
            ),
        ]
        rows = weekly_person_shift_mappings("2026-05-25", records)
        self.assertEqual(len(rows), 4)
        events = {r["event_type"] for r in rows}
        self.assertEqual(events, {"staffed", "ot", "leave"})

    def test_paired_emt_row_splits_persons(self):
        records = [
            ShiftRecord(
                date=date(2026, 5, 25),
                base="Bedford",
                service_type="GR",
                day_night="D",
                role="EMT",
                filled=True,
                overtime=False,
                leave_type=None,
                source_tab="EMT",
                source_cell="C6",
                raw_value="GR",
                unit_code="GR",
                person_display="Deptula, Thomas",
                person_displays=("Deptula, Thomas", "Feddersen"),
            ),
        ]
        rows = weekly_person_shift_mappings("2026-05-25", records)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {r["person_display"] for r in rows},
            {"Deptula, Thomas", "Feddersen"},
        )

    def test_skips_non_clinical_roles(self):
        records = [
            ShiftRecord(
                date=date(2026, 5, 25),
                base="Bedford",
                service_type="RW",
                day_night="D",
                role="PILOT",
                filled=True,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic",
                source_cell="C5",
                raw_value="D7B",
                unit_code="D7B",
                person_display="Pilot Person",
            ),
        ]
        self.assertEqual(weekly_person_shift_mappings("2026-05-25", records), [])


class PersonOpsQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        init_db(self.db_path)
        week = "2026-05-25"
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=week,
                    filled_day=1,
                    filled_night=0,
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Smith",
                    first_name="Jane",
                    role="RN",
                    active=1,
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Jones",
                    first_name="Bob",
                    role="RN",
                    active=1,
                )
            )
            session.flush()
            roster_index = staff_roster_index_from_session(session)
            for row in weekly_person_shift_mappings(
                week,
                [
                    _staffed(person="Smith, Jane", service_type="RW"),
                    _staffed(
                        person="Smith, Jane",
                        shift_date=date(2026, 5, 26),
                        service_type="GR",
                        unit="GR",
                    ),
                    _staffed(
                        person="Smith, Jane",
                        shift_date=date(2026, 5, 27),
                        overtime=True,
                    ),
                    ShiftRecord(
                        date=date(2026, 5, 28),
                        base="",
                        service_type="",
                        day_night="D",
                        role="RN",
                        filled=False,
                        overtime=False,
                        leave_type="AT",
                        source_tab="RN & Medic",
                        source_cell="D8",
                        raw_value="AT",
                        person_display="Smith, Jane",
                    ),
                    _staffed(person="Jones, Bob"),
                ],
                staff_roster_index=roster_index,
            ):
                session.add(WeeklyPersonShift(**row))

    def tearDown(self):
        import staffing_tool.db as db_mod

        resolved = db_mod._resolve_db_path(self.db_path)
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        db_mod._DB_READY_PATHS.discard(resolved)
        self.tmp.cleanup()

    def test_summary_rw_gr_and_exceptions(self):
        summary = load_person_ops_summary(
            self.db_path,
            "Smith, Jane",
            date(2026, 5, 25),
            date(2026, 5, 31),
        )
        self.assertEqual(summary.staffed_count, 3)
        self.assertEqual(summary.rw_count, 2)
        self.assertEqual(summary.gr_count, 1)
        self.assertEqual(summary.rw_pct, 66.7)
        self.assertEqual(summary.ot_count, 1)
        self.assertEqual(summary.leave_total, 1)
        self.assertEqual(summary.leave_counts.get("AT"), 1)

    def test_list_distinct_persons(self):
        names = list_distinct_persons(
            self.db_path, date(2026, 5, 25), date(2026, 5, 31)
        )
        self.assertEqual(names, sorted(["Jones, Bob", "Smith, Jane"], key=person_sort_key))

    def test_list_staff_roster_persons(self):
        roster = list_staff_roster_persons(self.db_path)
        self.assertEqual(
            [name for name, _role in roster],
            sorted(["Jones, Bob", "Smith, Jane"], key=person_sort_key),
        )

    def test_list_distinct_persons_filters_legacy_garbage(self):
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-25",
                    person_display="D, Cowart",
                    shift_date="2026-05-25",
                    role="RN",
                    event_type="staffed",
                    base_name="Bedford",
                    service_type="RW",
                    day_night="D",
                    unit_code="D7B",
                    overtime=0,
                    raw_value="D7B",
                    source_tab="RN",
                    source_cell="C7",
                )
            )
        names = list_distinct_persons(
            self.db_path, date(2026, 5, 25), date(2026, 5, 31)
        )
        self.assertIn("Cowart", names)
        self.assertNotIn("D, Cowart", names)

    def test_detail_matches_legacy_person_label(self):
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-25",
                    person_display="m, Ahlstedt",
                    shift_date="2026-05-30",
                    role="MEDIC",
                    event_type="staffed",
                    base_name="Bedford",
                    service_type="RW",
                    day_night="D",
                    unit_code="D7B",
                    overtime=0,
                    raw_value="D7B",
                    source_tab="RN",
                    source_cell="C52",
                )
            )
        rows = load_person_ops_detail(
            self.db_path,
            "Ahlstedt",
            date(2026, 5, 25),
            date(2026, 5, 31),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source_cell, "C52")

    def test_detail_rows(self):
        rows = load_person_ops_detail(
            self.db_path,
            "Smith, Jane",
            date(2026, 5, 25),
            date(2026, 5, 31),
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            {r.event_type for r in rows},
            {"staffed", "ot", "leave"},
        )


if __name__ == "__main__":
    unittest.main()
