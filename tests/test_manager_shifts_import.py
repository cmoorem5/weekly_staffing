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
