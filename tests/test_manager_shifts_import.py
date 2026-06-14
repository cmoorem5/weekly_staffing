"""Tests for manager shift import mapping and DB init behavior."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from staffing_tool.db import (
    _get_engine_cached,
    _sessionmaker_for_path,
    ensure_db_ready,
    get_engine,
    init_db,
)
from staffing_tool.manager_names import backfill_canonical_manager_shift_names
from staffing_tool.manager_roster import default_manager_last_names_upper
from staffing_tool.models import WeeklyManagerShift, WeeklyStaffing
from staffing_tool.schedule_import import ShiftRecord, weekly_manager_shift_mappings


def _manager_shift_record(
    *,
    person_display: str = "Ender",
    is_manager_row: bool = True,
    role: str = "RN",
    shift_date: date | None = None,
) -> ShiftRecord:
    d = shift_date or date(2025, 12, 7)
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
        person_display=person_display,
        is_manager_row=is_manager_row,
    )


class WeeklyManagerShiftMappingsTests(unittest.TestCase):
    def test_maps_filled_manager_clinical_roles_only(self):
        records = [
            _manager_shift_record(person_display="Ender", role="RN"),
            _manager_shift_record(person_display="Ender", role="MEDIC"),
            _manager_shift_record(person_display="Ender", role="PILOT"),
            _manager_shift_record(person_display="Ender", role="EMT"),
            ShiftRecord(
                date=date(2025, 12, 7),
                base="Bedford",
                service_type="RW",
                day_night="D",
                role="RN",
                filled=False,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic",
                source_cell="C6",
                raw_value="",
                unit_code="",
                person_display="Ender",
                is_manager_row=True,
            ),
            _manager_shift_record(person_display="Guest", is_manager_row=False),
        ]
        rows = weekly_manager_shift_mappings("2025-12-07", records)
        self.assertEqual(len(rows), 3)
        roles = {r["role"] for r in rows}
        self.assertEqual(roles, {"RN", "MEDIC", "EMT"})
        self.assertTrue(all(r["person_display"] == "Ender" for r in rows))
        self.assertTrue(all(r["event_type"] == "line_shift" for r in rows))

    def test_maps_manager_aoc_cells(self):
        records = [
            ShiftRecord(
                date=date(2025, 12, 8),
                base="",
                service_type="",
                day_night="",
                role="RN",
                filled=False,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic (RN)",
                source_cell="D5",
                raw_value="AOC",
                person_display="Bowman",
                is_manager_row=True,
                included_in_aggregates=False,
                manager_event_type="aoc",
                excel_row=5,
                excel_col=4,
            ),
            ShiftRecord(
                date=date(2025, 12, 9),
                base="",
                service_type="",
                day_night="",
                role="RN",
                filled=False,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic (RN)",
                source_cell="E6",
                raw_value="AOC",
                person_display="Guest",
                is_manager_row=False,
                included_in_aggregates=False,
                manager_event_type="aoc",
            ),
            _manager_shift_record(
                person_display="Bowman", shift_date=date(2025, 12, 7)
            ),
        ]
        rows = weekly_manager_shift_mappings("2025-12-07", records)
        self.assertEqual(len(rows), 2)
        aoc_rows = [r for r in rows if r["event_type"] == "aoc"]
        line_rows = [r for r in rows if r["event_type"] == "line_shift"]
        self.assertEqual(len(aoc_rows), 1)
        self.assertEqual(aoc_rows[0]["person_display"], "Bowman")
        self.assertEqual(aoc_rows[0]["shift_date"], "2025-12-08")
        self.assertEqual(aoc_rows[0]["raw_value"], "AOC")
        self.assertEqual(len(line_rows), 1)

    def test_legacy_display_name_preserved_until_backfill(self):
        records = [_manager_shift_record(person_display="m, Ender")]
        rows = weekly_manager_shift_mappings("2025-12-07", records)
        self.assertEqual(rows[0]["person_display"], "m, Ender")


class BackfillManagerNamesTests(unittest.TestCase):
    def _dispose_test_db(self, db_path: str) -> None:
        import staffing_tool.db as db_mod

        resolved = db_mod._resolve_db_path(db_path)
        get_engine(db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        db_mod._DB_READY_PATHS.discard(resolved)

    def test_backfill_only_touches_comma_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            try:
                init_db(db_path)
                from staffing_tool.db import session_scope

                with session_scope(db_path) as session:
                    session.add(
                        WeeklyStaffing(
                            week_start="2025-12-07",
                            filled_day=0,
                            filled_night=0,
                        )
                    )
                    session.flush()
                    session.add(
                        WeeklyManagerShift(
                            week_start="2025-12-07",
                            person_display="m, Ender",
                            role="RN",
                            shift_date="2025-12-07",
                            base_name="Bedford",
                            service_type="RW",
                            day_night="D",
                        )
                    )
                    session.add(
                        WeeklyManagerShift(
                            week_start="2025-12-07",
                            person_display="Holst",
                            role="RN",
                            shift_date="2025-12-08",
                            base_name="Bedford",
                            service_type="RW",
                            day_night="D",
                        )
                    )
                with session_scope(db_path) as session:
                    updated = backfill_canonical_manager_shift_names(session)
                    self.assertEqual(updated, 1)
                    names = {
                        r[0]
                        for r in session.query(WeeklyManagerShift.person_display).all()
                    }
                self.assertIn("Ender", names)
                self.assertIn("Holst", names)
                self.assertNotIn("m, Ender", names)
            finally:
                self._dispose_test_db(db_path)

    def test_canonical_name_uses_roster(self):
        from staffing_tool.manager_names import canonical_manager_name

        roster = default_manager_last_names_upper()
        self.assertEqual(canonical_manager_name("P, Doherty", roster), "Doherty")


class ManagerAocAggregationTests(unittest.TestCase):
    def _dispose_test_db(self, db_path: str) -> None:
        import staffing_tool.db as db_mod

        resolved = db_mod._resolve_db_path(db_path)
        get_engine(db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        db_mod._DB_READY_PATHS.discard(resolved)

    def test_aoc_count_per_manager_for_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "aoc.db")
            try:
                init_db(db_path)
                from staffing_tool.db import session_scope

                week = "2025-12-07"
                with session_scope(db_path) as session:
                    session.add(
                        WeeklyStaffing(
                            week_start=week,
                            filled_day=0,
                            filled_night=0,
                        )
                    )
                    session.flush()
                    session.bulk_insert_mappings(
                        WeeklyManagerShift,
                        [
                            {
                                "week_start": week,
                                "person_display": "Bowman",
                                "role": "RN",
                                "shift_date": "2025-12-08",
                                "event_type": "aoc",
                                "base_name": "",
                                "service_type": "",
                                "day_night": "",
                                "raw_value": "AOC",
                            },
                            {
                                "week_start": week,
                                "person_display": "Bowman",
                                "role": "RN",
                                "shift_date": "2025-12-09",
                                "event_type": "aoc",
                                "base_name": "",
                                "service_type": "",
                                "day_night": "",
                                "raw_value": "AOC",
                            },
                            {
                                "week_start": week,
                                "person_display": "Holst",
                                "role": "RN",
                                "shift_date": "2025-12-08",
                                "event_type": "line_shift",
                                "base_name": "Bedford",
                                "service_type": "RW",
                                "day_night": "D",
                                "raw_value": "D7B",
                            },
                        ],
                    )
                with session_scope(db_path) as session:
                    from sqlalchemy import func

                    aoc_counts = (
                        session.query(
                            WeeklyManagerShift.person_display,
                            func.count(WeeklyManagerShift.id),
                        )
                        .filter(WeeklyManagerShift.week_start == week)
                        .filter(WeeklyManagerShift.event_type == "aoc")
                        .group_by(WeeklyManagerShift.person_display)
                        .all()
                    )
                self.assertEqual(dict(aoc_counts), {"Bowman": 2})
            finally:
                self._dispose_test_db(db_path)


class EnsureDbReadyTests(unittest.TestCase):
    def test_ensure_db_ready_is_idempotent_per_process(self):
        import staffing_tool.db as db_mod

        db_mod._DB_READY_PATHS.clear()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "ready.db")
            try:
                ensure_db_ready(db_path)
                self.assertIn(db_mod._resolve_db_path(db_path), db_mod._DB_READY_PATHS)
                engine = get_engine(db_path)
                ensure_db_ready(db_path)
                self.assertIs(get_engine(db_path), engine)
            finally:
                get_engine(db_path).dispose()
                _get_engine_cached.cache_clear()
                _sessionmaker_for_path.cache_clear()
                db_mod._DB_READY_PATHS.clear()


if __name__ == "__main__":
    unittest.main()
