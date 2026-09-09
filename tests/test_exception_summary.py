"""Tests for the roster-wide staff exception summary."""

from datetime import date

from staffing_tool.db import session_scope
from staffing_tool.exception_summary import (
    load_clinical_exception_summary,
    load_manager_exception_summary,
)
from staffing_tool.models import (
    StaffRosterEntry,
    WeeklyManagerShift,
    WeeklyPersonShift,
    WeeklyStaffing,
)
from staffing_tool.schedule_import import ShiftRecord, weekly_person_shift_mappings
from staffing_tool.staff_roster import staff_roster_index_from_session
from tests._temp_db import TempDbTestCase


def _shift(
    *,
    person: str,
    shift_date: date,
    role: str = "RN",
    filled: bool = True,
    overtime: bool = False,
    leave_type: str | None = None,
    skip_reason: str | None = None,
    base: str = "Bedford",
    service_type: str = "RW",
    unit: str = "D7B",
) -> ShiftRecord:
    return ShiftRecord(
        date=shift_date,
        base=base if filled else "",
        service_type=service_type if filled else "",
        day_night="D",
        role=role,
        filled=filled,
        overtime=overtime,
        leave_type=leave_type,
        source_tab="RN & Medic",
        source_cell="C5",
        raw_value=leave_type or unit,
        unit_code=unit if filled else "",
        person_display=person,
        skip_reason=skip_reason,
        included_in_aggregates=skip_reason is None,
    )


class ClinicalExceptionSummaryTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        week = "2026-05-25"
        with session_scope(self.db_path) as session:
            session.add(WeeklyStaffing(week_start=week, filled_day=1, filled_night=0))
            session.add(
                StaffRosterEntry(
                    last_name="Smith", first_name="Jane", role="RN", active=1
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Jones", first_name="Bob", role="RN", active=1
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Retired", first_name="Old", role="RN", active=0
                )
            )
            session.flush()
            roster_index = staff_roster_index_from_session(session)
            records = [
                _shift(person="Smith, Jane", shift_date=date(2026, 5, 25)),
                _shift(
                    person="Smith, Jane",
                    shift_date=date(2026, 5, 26),
                    overtime=True,
                ),
                _shift(
                    person="Smith, Jane",
                    shift_date=date(2026, 5, 27),
                    filled=False,
                    leave_type="SICK",
                ),
                _shift(
                    person="Smith, Jane",
                    shift_date=date(2026, 5, 28),
                    filled=False,
                    leave_type="PFML",
                ),
                _shift(
                    person="Smith, Jane",
                    shift_date=date(2026, 5, 29),
                    filled=False,
                    skip_reason="admin",
                ),
                # A non-admin skip (e.g. an OPEN row) must not be counted.
                _shift(
                    person="Smith, Jane",
                    shift_date=date(2026, 5, 30),
                    filled=False,
                    skip_reason="open",
                ),
                _shift(person="Jones, Bob", shift_date=date(2026, 5, 25)),
            ]
            for row in weekly_person_shift_mappings(
                week, records, staff_roster_index=roster_index
            ):
                session.add(WeeklyPersonShift(**row))

    def test_summary_totals_and_leave_folding(self):
        rows = load_clinical_exception_summary(
            self.db_path, date(2026, 5, 25), date(2026, 5, 31)
        )
        by_name = {r.person_display: r for r in rows}

        self.assertIn("Smith, Jane", by_name)
        self.assertIn("Jones, Bob", by_name)
        self.assertNotIn("Retired, Old", by_name)

        smith = by_name["Smith, Jane"]
        self.assertEqual(smith.staffed_count, 2)  # 1 regular + 1 OT
        self.assertEqual(smith.ot_count, 1)
        self.assertEqual(smith.leave_total, 2)  # SICK + PFML
        self.assertEqual(smith.leave_counts.get("SICK"), 1)
        self.assertEqual(smith.leave_counts.get("LOA"), 1)  # PFML folded into LOA
        self.assertEqual(smith.admin_other_count, 1)
        self.assertEqual(smith.total_exceptions, 3)  # 2 leave + 1 admin

        jones = by_name["Jones, Bob"]
        self.assertEqual(jones.staffed_count, 1)
        self.assertEqual(jones.leave_total, 0)

    def test_role_filter(self):
        rows = load_clinical_exception_summary(
            self.db_path, date(2026, 5, 25), date(2026, 5, 31), role="MEDIC"
        )
        self.assertEqual(rows, [])


class ManagerExceptionSummaryTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(week_start="2026-05-25", filled_day=1, filled_night=0)
            )
            session.flush()
            session.add_all(
                [
                    WeeklyManagerShift(
                        week_start="2026-05-25",
                        person_display="Ender",
                        role="RN",
                        shift_date="2026-05-25",
                        event_type="line_shift",
                        base_name="Bedford",
                        service_type="RW",
                        day_night="D",
                        unit_code="D7B",
                        overtime=0,
                    ),
                    WeeklyManagerShift(
                        week_start="2026-05-25",
                        person_display="m, Ender",
                        role="RN",
                        shift_date="2026-05-26",
                        event_type="line_shift",
                        base_name="Bedford",
                        service_type="RW",
                        day_night="D",
                        unit_code="D7B",
                        overtime=1,
                    ),
                    WeeklyManagerShift(
                        week_start="2026-05-25",
                        person_display="Ender",
                        role="RN",
                        shift_date="2026-05-27",
                        event_type="aoc",
                        base_name="",
                        service_type="",
                        day_night="",
                        overtime=0,
                    ),
                ]
            )

    def test_manager_totals_and_name_folding(self):
        rows = load_manager_exception_summary(
            self.db_path, date(2026, 5, 25), date(2026, 5, 31)
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.person_display, "Ender")
        self.assertTrue(row.is_manager)
        self.assertEqual(row.staffed_count, 2)
        self.assertEqual(row.ot_count, 1)
        self.assertEqual(row.admin_other_count, 1)
        self.assertEqual(row.leave_total, 0)
