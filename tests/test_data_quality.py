"""Tests for KPI data-quality audit (and its batched queries)."""

import tempfile
import unittest
from pathlib import Path

from staffing_tool.data_quality import audit_kpi_data_quality
from staffing_tool.db import (
    _get_engine_cached,
    _sessionmaker_for_path,
    get_engine,
    init_db,
    session_scope,
)
from staffing_tool.models import WeeklyStaffing


def _add_week(session, week_start, **overrides):
    fields = {
        "week_start": week_start,
        "day_target": 8,
        "night_min": 4,
        "filled_day": 56,
        "filled_night": 28,
        "ot_rn_day": 0,
        "ot_rn_night": 0,
        "ot_medic_day": 0,
        "ot_medic_night": 0,
        "ot_emt_day": 0,
        "ot_emt_night": 0,
        "ot_rn": 0,
        "ot_medic": 0,
        "ot_emt": 0,
        "leave_at": 0,
        "leave_lt": 0,
        "leave_sick": 0,
        "leave_loa": 0,
        "leave_pfml": 0,
        "leave_jury": 0,
        "leave_brev": 0,
        "overnights_below": 0,
        "pilot_vacancies": 0,
        "notes": "test",
        "entered_by": "test",
        "created_at": "2026-01-01T00:00:00Z",
    }
    fields.update(overrides)
    session.add(WeeklyStaffing(**fields))


class AuditKpiDataQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        init_db(self.db_path)

    def tearDown(self):
        import staffing_tool.db as db_mod

        resolved = db_mod._resolve_db_path(self.db_path)
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        db_mod._DB_READY_PATHS.discard(resolved)
        self.tmp.cleanup()

    def test_clean_weeks_report_all_ok(self):
        with session_scope(self.db_path) as session:
            _add_week(session, "2026-01-04")
            _add_week(session, "2026-01-11")
        with session_scope(self.db_path) as session:
            result = audit_kpi_data_quality(session)
        self.assertEqual(result["weeks_checked"], 2)
        self.assertTrue(result["all_ok"])
        self.assertEqual(result["issue_count"], 0)

    def test_legacy_pfml_is_flagged(self):
        with session_scope(self.db_path) as session:
            _add_week(session, "2026-01-04", leave_pfml=3)
        with session_scope(self.db_path) as session:
            result = audit_kpi_data_quality(session)
        self.assertFalse(result["all_ok"])
        self.assertEqual(result["legacy_pfml_count"], 1)
        self.assertIn("2026-01-04", result["legacy_pfml_samples"])

    def test_ot_component_mismatch_is_flagged(self):
        # day/night OT total (5) disagrees with legacy OT total (9).
        with session_scope(self.db_path) as session:
            _add_week(
                session,
                "2026-01-04",
                ot_rn_day=5,
                ot_rn=9,
            )
        with session_scope(self.db_path) as session:
            result = audit_kpi_data_quality(session)
        self.assertFalse(result["all_ok"])
        self.assertEqual(result["ot_mismatch_count"], 1)


if __name__ == "__main__":
    unittest.main()
