"""Tests for staff roster matching and import integration."""

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
from staffing_tool.schedule_import import ShiftRecord, weekly_person_shift_mappings
from staffing_tool.schedule_persistence import persist_schedule_import_detail
from staffing_tool.staff_roster import (
    StaffRosterMatchIndex,
    add_roster_entries,
    canonical_display,
    find_roster_duplicate_candidates,
    list_roster_import_weeks,
    match_parsed_person_to_roster,
    merge_roster_entries,
    parse_roster_import_form_key,
    staff_roster_index_from_session,
    suggest_roster_imports,
    sync_roster_from_import,
)


def _staffed(
    *,
    person: str = "Smith",
    role: str = "RN",
    shift_date: date | None = None,
) -> ShiftRecord:
    d = shift_date or date(2026, 5, 25)
    return ShiftRecord(
        date=d,
        base="Bedford",
        service_type="RW",
        day_night="D",
        role=role,
        filled=True,
        overtime=False,
        leave_type=None,
        source_tab="RN & Medic",
        source_cell="C5",
        raw_value="D7B",
        unit_code="D7B",
        person_display=person,
    )


class StaffRosterMatchTests(unittest.TestCase):
    def test_canonical_display_with_and_without_first(self):
        with_first = StaffRosterEntry(
            last_name="Smith", first_name="Jane", role="RN", active=1
        )
        self.assertEqual(canonical_display(with_first), "Smith, Jane")
        last_only = StaffRosterEntry(
            last_name="Cowart", first_name="", role="RN", active=1
        )
        self.assertEqual(canonical_display(last_only), "Cowart")

    def test_match_by_last_name_on_rn_sheet(self):
        entry = StaffRosterEntry(
            id=1, last_name="Cowart", first_name="", role="RN", active=1
        )
        index = StaffRosterMatchIndex(
            entries=[entry],
            by_role_last={("RN", "COWART"): [entry]},
        )
        matched = match_parsed_person_to_roster("Cowart", "RN", index)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.id, 1)

    def test_persists_unmatched_with_null_roster_id(self):
        entry = StaffRosterEntry(
            id=1, last_name="Smith", first_name="Jane", role="RN", active=1
        )
        index = StaffRosterMatchIndex(
            entries=[entry],
            by_role_last={("RN", "SMITH"): [entry]},
        )
        rows = weekly_person_shift_mappings(
            "2026-05-25",
            [_staffed(person="Jones, Bob"), _staffed(person="Smith, Jane")],
            staff_roster_index=index,
        )
        self.assertEqual(len(rows), 2)
        by_name = {r["person_display"]: r for r in rows}
        self.assertIsNone(by_name["Jones, Bob"]["staff_member_id"])
        self.assertEqual(by_name["Smith, Jane"]["staff_member_id"], 1)

    def test_deactivate_excludes_from_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            init_db(db_path)
            with session_scope(db_path) as session:
                session.add(
                    StaffRosterEntry(
                        last_name="Smith",
                        first_name="Jane",
                        role="RN",
                        active=1,
                    )
                )
                session.flush()
                index = staff_roster_index_from_session(session)
                self.assertEqual(len(index.entries), 1)
                session.query(StaffRosterEntry).update({StaffRosterEntry.active: 0})
                session.flush()
                index2 = staff_roster_index_from_session(session)
                self.assertTrue(index2.is_empty())
            get_engine(db_path).dispose()
            _get_engine_cached.cache_clear()
            _sessionmaker_for_path.cache_clear()


class StaffRosterImportNamesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        init_db(self.db_path)
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start="2026-05-31",
                    filled_day=1,
                    filled_night=0,
                    entered_by="import",
                )
            )
            session.flush()
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-31",
                    person_display="Cowart",
                    role="RN",
                    shift_date="2026-05-31",
                    event_type="staffed",
                    base_name="Bedford",
                    service_type="RW",
                    day_night="D",
                )
            )
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-31",
                    person_display="Smith, Jane",
                    role="RN",
                    shift_date="2026-05-31",
                    event_type="staffed",
                    base_name="Bedford",
                    service_type="RW",
                    day_night="D",
                )
            )
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-31",
                    person_display="Chatigny, Aaron",
                    role="EMT",
                    shift_date="2026-05-31",
                    event_type="staffed",
                    base_name="Bedford",
                    service_type="GR",
                    day_night="D",
                )
            )
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-31",
                    person_display="Chatigny",
                    role="EMT",
                    shift_date="2026-06-01",
                    event_type="staffed",
                    base_name="Bedford",
                    service_type="GR",
                    day_night="D",
                )
            )

    def tearDown(self):
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        self.tmp.cleanup()

    def test_suggest_roster_imports_dedupes_last_name_variants(self):
        with session_scope(self.db_path) as session:
            weeks = list_roster_import_weeks(session)
            self.assertEqual(weeks, ["2026-05-31"])
            suggestions = suggest_roster_imports(session, "2026-05-31")
        displays = {(s.role, s.display) for s in suggestions}
        self.assertIn(("RN", "Cowart"), displays)
        self.assertIn(("RN", "Smith, Jane"), displays)
        self.assertIn(("EMT", "Chatigny, Aaron"), displays)
        self.assertNotIn(("EMT", "Chatigny"), displays)

    def test_add_roster_entries_and_skip_duplicates(self):
        with session_scope(self.db_path) as session:
            added, updated, skipped = add_roster_entries(
                session,
                [("RN", "Cowart", ""), ("RN", "Cowart", "")],
                created_at="2026-06-10T00:00:00Z",
            )
            self.assertEqual(added, 1)
            self.assertEqual(updated, 0)
            self.assertEqual(skipped, 1)
            suggestions = suggest_roster_imports(session, "2026-05-31")
        self.assertFalse(
            any(s.display == "Cowart" and s.role == "RN" for s in suggestions)
        )

    def test_parse_roster_import_form_key(self):
        self.assertEqual(
            parse_roster_import_form_key("RN|Smith|Jane"),
            ("RN", "Smith", "Jane"),
        )
        self.assertIsNone(parse_roster_import_form_key("bad"))

    def test_suggest_roster_imports_excludes_junk_labels(self):
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-31",
                    person_display="Phillips K.",
                    role="RN",
                    shift_date="2026-05-31",
                    event_type="staffed",
                    base_name="Bedford",
                    service_type="RW",
                    day_night="D",
                )
            )
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-31",
                    person_display="RAL, Orientee /",
                    role="MEDIC",
                    shift_date="2026-05-31",
                    event_type="staffed",
                    base_name="Bedford",
                    service_type="RW",
                    day_night="D",
                )
            )
            suggestions = suggest_roster_imports(session, "2026-05-31")
        displays = {(s.role, s.display) for s in suggestions}
        self.assertNotIn(("RN", "K., Phillips"), displays)
        self.assertNotIn(("RN", "Phillips K."), displays)
        self.assertFalse(any("orientee" in s.display.lower() for s in suggestions))


class SyncRosterFromImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        init_db(self.db_path)

    def tearDown(self):
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        self.tmp.cleanup()

    def test_sync_adds_new_person_and_links_on_persist(self):
        week = "2026-05-25"
        records = [
            _staffed(person="Jones, Bob"),
            _staffed(person="Smith, Jane", role="MEDIC"),
        ]
        with session_scope(self.db_path) as session:
            session.add(WeeklyStaffing(week_start=week, filled_day=1, filled_night=0))
            session.flush()
            upload = Path(self.tmp.name) / "sample.xlsx"
            upload.write_bytes(b"x")
            _imp, added = persist_schedule_import_detail(
                session,
                week_start=week,
                upload_path=str(upload),
                source_filename="test.xlsx",
                records=records,
                issues=[],
                archive_raw_cells=False,
            )
            self.assertEqual(added, 2)
            shifts = (
                session.query(WeeklyPersonShift)
                .filter(WeeklyPersonShift.week_start == week)
                .all()
            )
        by_display = {s.person_display: s for s in shifts}
        self.assertIsNotNone(by_display["Jones, Bob"].staff_member_id)
        self.assertIsNotNone(by_display["Smith, Jane"].staff_member_id)

    def test_sync_does_not_readd_deactivated_person(self):
        with session_scope(self.db_path) as session:
            session.add(
                StaffRosterEntry(
                    last_name="Jones",
                    first_name="Bob",
                    role="RN",
                    active=0,
                    created_at="2026-06-10T00:00:00Z",
                )
            )
            session.flush()
            added, index = sync_roster_from_import(
                session,
                [_staffed(person="Jones, Bob")],
                created_at="2026-06-10T00:00:00Z",
            )
            self.assertEqual(added, 0)
            self.assertTrue(index.is_empty())

    def test_sync_no_duplicate_for_existing_active_member(self):
        with session_scope(self.db_path) as session:
            session.add(
                StaffRosterEntry(
                    last_name="Smith",
                    first_name="Jane",
                    role="RN",
                    active=1,
                    created_at="2026-06-10T00:00:00Z",
                )
            )
            session.flush()
            added, index = sync_roster_from_import(
                session,
                [_staffed(person="Smith, Jane")],
                created_at="2026-06-10T00:00:00Z",
            )
            self.assertEqual(added, 0)
            self.assertEqual(len(index.entries), 1)
            count = session.query(StaffRosterEntry).count()
            self.assertEqual(count, 1)

    def test_sync_skips_junk_and_manager_rows(self):
        records = [
            _staffed(person="TRAINING"),
            _staffed(person="OPEN"),
            ShiftRecord(
                date=date(2026, 5, 25),
                base="Bedford",
                service_type="RW",
                day_night="D",
                role="RN",
                filled=True,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic",
                source_cell="C5",
                raw_value="D7B",
                unit_code="D7B",
                person_display="Real, Person",
                is_manager_row=True,
            ),
        ]
        with session_scope(self.db_path) as session:
            added, _index = sync_roster_from_import(
                session,
                records,
                created_at="2026-06-10T00:00:00Z",
            )
            self.assertEqual(added, 0)
            self.assertEqual(session.query(StaffRosterEntry).count(), 0)

    def test_fills_in_first_name_on_existing_bare_last_name_instead_of_duplicating(
        self,
    ):
        # Reproduces the "Prins" + "Prins, Jonathan" bug: a bare last-name
        # entry already exists (e.g. from an earlier import with no first
        # name on that row), and a later import provides the full name.
        with session_scope(self.db_path) as session:
            session.add(
                StaffRosterEntry(
                    last_name="Prins",
                    first_name="",
                    role="EMT",
                    active=1,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.flush()
            added, _index = sync_roster_from_import(
                session,
                [_staffed(person="Prins, Jonathan", role="EMT")],
                created_at="2026-06-10T00:00:00Z",
            )
            self.assertEqual(added, 0)

            rows = session.query(StaffRosterEntry).filter_by(role="EMT").all()
            self.assertEqual(len(rows), 1, "must not create a second Prins row")
            self.assertEqual(rows[0].first_name, "Jonathan")
            self.assertEqual(rows[0].active, 1)

    def test_fills_in_first_name_without_reactivating_inactive_entry(self):
        # An inactive bare-last-name row must stay inactive when its first
        # name is filled in -- filling in a name must not resurrect someone
        # who was deliberately deactivated.
        with session_scope(self.db_path) as session:
            session.add(
                StaffRosterEntry(
                    last_name="Deptula",
                    first_name="",
                    role="EMT",
                    active=0,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.flush()
            sync_roster_from_import(
                session,
                [_staffed(person="Deptula, Thomas", role="EMT")],
                created_at="2026-06-10T00:00:00Z",
            )
            rows = session.query(StaffRosterEntry).filter_by(role="EMT").all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].first_name, "Thomas")
            self.assertEqual(rows[0].active, 0)

    def test_bare_last_name_skipped_when_fuller_record_already_exists(self):
        # The reverse direction: a fuller record already exists, and a later
        # import only has the bare last name -- no new bare row should
        # appear alongside it.
        with session_scope(self.db_path) as session:
            session.add(
                StaffRosterEntry(
                    last_name="Prins",
                    first_name="Jonathan",
                    role="EMT",
                    active=1,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.flush()
            added, _index = sync_roster_from_import(
                session,
                [_staffed(person="Prins", role="EMT")],
                created_at="2026-06-10T00:00:00Z",
            )
            self.assertEqual(added, 0)
            rows = session.query(StaffRosterEntry).filter_by(role="EMT").all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].first_name, "Jonathan")


class RosterMergeTests(unittest.TestCase):
    """Cleanup tool for duplicates that already exist (pre-fix data)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        init_db(self.db_path)

    def tearDown(self):
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        self.tmp.cleanup()

    def test_finds_bare_and_full_name_pair(self):
        with session_scope(self.db_path) as session:
            session.add(
                StaffRosterEntry(
                    last_name="Prins",
                    first_name="",
                    role="EMT",
                    active=1,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Prins",
                    first_name="Jonathan",
                    role="EMT",
                    active=1,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.flush()
            candidates = find_roster_duplicate_candidates(session)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].role, "EMT")
        self.assertEqual(candidates[0].last_name, "Prins")
        self.assertEqual(candidates[0].full_name, "Prins, Jonathan")

    def test_does_not_flag_two_full_names_or_different_roles(self):
        with session_scope(self.db_path) as session:
            session.add(
                StaffRosterEntry(
                    last_name="Cowart",
                    first_name="Max",
                    role="RN",
                    active=1,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Cowart",
                    first_name="",
                    role="MEDIC",
                    active=0,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Smith",
                    first_name="Jane",
                    role="RN",
                    active=1,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Smith",
                    first_name="John",
                    role="RN",
                    active=1,
                    created_at="2026-06-01T00:00:00Z",
                )
            )
            session.flush()
            candidates = find_roster_duplicate_candidates(session)
        # Cowart is split across two different roles (RN vs MEDIC) -- not a
        # same-role duplicate. Both Smiths have first names -- ambiguous,
        # not auto-flagged.
        self.assertEqual(candidates, [])

    def test_merge_reassigns_shift_history_and_removes_bare_entry(self):
        with session_scope(self.db_path) as session:
            bare = StaffRosterEntry(
                last_name="Prins",
                first_name="",
                role="EMT",
                active=1,
                created_at="2026-06-01T00:00:00Z",
            )
            full = StaffRosterEntry(
                last_name="Prins",
                first_name="Jonathan",
                role="EMT",
                active=0,
                created_at="2026-06-01T00:00:00Z",
            )
            session.add(bare)
            session.add(full)
            session.add(
                WeeklyStaffing(week_start="2026-05-25", filled_day=1, filled_night=0)
            )
            session.flush()
            bare_id, full_id = bare.id, full.id
            session.add(
                WeeklyPersonShift(
                    week_start="2026-05-25",
                    person_display="Prins",
                    staff_member_id=bare_id,
                    shift_date="2026-05-25",
                    role="EMT",
                    event_type="staffed",
                )
            )
            session.flush()

            ok = merge_roster_entries(session, keep_id=full_id, remove_id=bare_id)
            self.assertTrue(ok)

            remaining = session.query(StaffRosterEntry).filter_by(role="EMT").all()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].id, full_id)
            # Bare entry was active -- merged entry must end up active too.
            self.assertEqual(remaining[0].active, 1)

            shift = (
                session.query(WeeklyPersonShift)
                .filter_by(shift_date="2026-05-25")
                .first()
            )
            self.assertEqual(shift.staff_member_id, full_id)

    def test_merge_rejects_mismatched_roles(self):
        with session_scope(self.db_path) as session:
            a = StaffRosterEntry(
                last_name="Cowart",
                first_name="",
                role="MEDIC",
                active=1,
                created_at="2026-06-01T00:00:00Z",
            )
            b = StaffRosterEntry(
                last_name="Cowart",
                first_name="Max",
                role="RN",
                active=1,
                created_at="2026-06-01T00:00:00Z",
            )
            session.add(a)
            session.add(b)
            session.flush()
            ok = merge_roster_entries(session, keep_id=b.id, remove_id=a.id)
            self.assertFalse(ok)
            self.assertEqual(session.query(StaffRosterEntry).count(), 2)


if __name__ == "__main__":
    unittest.main()
