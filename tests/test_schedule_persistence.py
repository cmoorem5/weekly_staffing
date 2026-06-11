"""Tests for full schedule import persistence."""

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
from staffing_tool.models import (
    ScheduleImport,
    ScheduleParseIssue,
    ScheduleRawCell,
    WeeklyOpsViewAssignment,
    WeeklyOpsViewDay,
    WeeklyPersonShift,
    WeeklyStaffing,
)
from staffing_tool.schedule_data import (
    get_week_all_cells,
    get_week_ops_view_assignments,
    get_week_ops_view_days,
    get_week_parse_issues,
    get_week_person_events,
    list_imports_for_week,
)
from staffing_tool.schedule_import import (
    PARSER_VERSION,
    ShiftRecord,
    aggregate_week_from_records,
    parse_schedule_workbook,
    weekly_person_shift_mappings,
)
from staffing_tool.schedule_persistence import persist_schedule_import_detail


def _staffed(**kwargs) -> ShiftRecord:
    defaults = {
        "date": date(2026, 5, 25),
        "base": "Bedford",
        "service_type": "RW",
        "day_night": "D",
        "role": "RN",
        "filled": True,
        "overtime": False,
        "leave_type": None,
        "source_tab": "RN & Medic (RN)",
        "source_cell": "C5",
        "raw_value": "D7B",
        "unit_code": "D7B",
        "person_display": "Smith, Jane",
        "excel_row": 5,
        "excel_col": 3,
    }
    defaults.update(kwargs)
    return ShiftRecord(**defaults)


class WeeklyPersonShiftPersistenceMappingsTests(unittest.TestCase):
    def test_skipped_cells_map_with_skip_reason(self):
        records = [
            ShiftRecord(
                date=date(2026, 5, 25),
                base="",
                service_type="",
                day_night="D",
                role="RN",
                filled=False,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic (RN)",
                source_cell="D5",
                raw_value="SIM",
                person_display="Smith, Jane",
                skip_reason="training",
                included_in_aggregates=False,
                excel_row=5,
                excel_col=4,
            ),
            _staffed(),
        ]
        rows = weekly_person_shift_mappings("2026-05-25", records)
        self.assertEqual(len(rows), 2)
        skipped = [r for r in rows if r["event_type"] == "skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["skip_reason"], "training")
        self.assertEqual(skipped[0]["included_in_aggregates"], 0)

    def test_aggregate_ignores_skipped_records(self):
        records = [
            _staffed(role="RN"),
            _staffed(role="MEDIC", person_display="Jones, Bob"),
            ShiftRecord(
                date=date(2026, 5, 25),
                base="",
                service_type="",
                day_night="D",
                role="RN",
                filled=False,
                overtime=False,
                leave_type="SICK",
                source_tab="RN & Medic (RN)",
                source_cell="E5",
                raw_value="SICK",
                person_display="Other, Person",
                skip_reason="manager_row",
                included_in_aggregates=False,
            ),
        ]
        agg = aggregate_week_from_records("2026-05-25", records, None)
        self.assertEqual(agg.filled_day, 1)
        self.assertEqual(agg.leave_sick, 0)


class PersistScheduleImportDetailTests(unittest.TestCase):
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

    def test_persist_audit_and_person_events(self):
        week = "2026-05-25"
        upload = Path(self.tmp.name) / "sample.xlsx"
        upload.write_bytes(b"not-a-real-xlsx")
        records = [
            _staffed(),
            ShiftRecord(
                date=date(2026, 5, 26),
                base="",
                service_type="",
                day_night="D",
                role="RN",
                filled=False,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic (RN)",
                source_cell="D5",
                raw_value="OPEN",
                person_display="Smith, Jane",
                skip_reason="open",
                included_in_aggregates=False,
                excel_row=5,
                excel_col=4,
            ),
        ]
        issues = []
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=week,
                    filled_day=1,
                    filled_night=0,
                )
            )
            session.flush()
            _imp, _added = persist_schedule_import_detail(
                session,
                week_start=week,
                upload_path=str(upload),
                source_filename="test_schedule.xlsx",
                records=records,
                issues=issues,
                archive_raw_cells=False,
            )

        imports = list_imports_for_week(week, self.db_path)
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0].parser_version, PARSER_VERSION)
        self.assertEqual(imports[0].source_filename, "test_schedule.xlsx")
        self.assertEqual(imports[0].person_event_count, 2)

        events = get_week_person_events(week, self.db_path)
        self.assertEqual(len(events), 2)
        skipped = get_week_person_events(
            week, self.db_path, event_type="skipped"
        )
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].skip_reason, "open")

        with session_scope(self.db_path) as session:
            count = (
                session.query(WeeklyPersonShift)
                .filter(WeeklyPersonShift.week_start == week)
                .count()
            )
        self.assertEqual(count, 2)


class SampleWorkbookPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        init_db(self.db_path)
        self.sample = (
            Path(__file__).resolve().parents[1]
            / "uploads/schedule_upload_20260611T025001Z.xlsx"
        )

    def tearDown(self):
        import staffing_tool.db as db_mod

        resolved = db_mod._resolve_db_path(self.db_path)
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        db_mod._DB_READY_PATHS.discard(resolved)
        self.tmp.cleanup()

    def test_full_import_from_sample_workbook(self):
        if not self.sample.is_file():
            self.skipTest("sample upload not present")
        week = "2026-05-31"
        records, issues, ops_coverage = parse_schedule_workbook(
            str(self.sample), week_start=week
        )
        self.assertTrue(records)

        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=week,
                    filled_day=1,
                    filled_night=0,
                )
            )
            session.flush()
            imp, _roster_added = persist_schedule_import_detail(
                session,
                week_start=week,
                upload_path=str(self.sample),
                source_filename=self.sample.name,
                records=records,
                issues=issues,
            )

        self.assertGreater(imp.record_count, 0)
        self.assertGreater(imp.person_event_count, 0)

        staffed = get_week_person_events(
            week, self.db_path, event_type="staffed"
        )
        leave = get_week_person_events(week, self.db_path, event_type="leave")
        skipped = get_week_person_events(
            week, self.db_path, event_type="skipped"
        )
        self.assertGreater(len(staffed), 0)
        self.assertGreater(len(leave) + len(skipped), 0)

        ops_days = get_week_ops_view_days(week, self.db_path)
        ops_assign = get_week_ops_view_assignments(week, self.db_path)
        self.assertGreater(len(ops_days), 0)
        self.assertGreater(len(ops_assign), 0)

        raw_cells = get_week_all_cells(week, self.db_path)
        if imp.raw_cell_count:
            self.assertEqual(len(raw_cells), imp.raw_cell_count)
            self.assertLessEqual(imp.raw_cell_count, 5000)

        parse_issues = get_week_parse_issues(week, self.db_path)
        self.assertEqual(len(parse_issues), len(issues))

        with session_scope(self.db_path) as session:
            day_rows = (
                session.query(WeeklyOpsViewDay)
                .filter(WeeklyOpsViewDay.week_start == week)
                .count()
            )
            assign_rows = (
                session.query(WeeklyOpsViewAssignment)
                .filter(WeeklyOpsViewAssignment.week_start == week)
                .count()
            )
            issue_rows = (
                session.query(ScheduleParseIssue)
                .filter(ScheduleParseIssue.week_start == week)
                .count()
            )
            raw_rows = (
                session.query(ScheduleRawCell)
                .filter(ScheduleRawCell.week_start == week)
                .count()
            )
            import_rows = (
                session.query(ScheduleImport)
                .filter(ScheduleImport.week_start == week)
                .count()
            )
        self.assertEqual(day_rows, len(ops_days))
        self.assertEqual(assign_rows, len(ops_assign))
        self.assertEqual(issue_rows, len(issues))
        self.assertEqual(raw_rows, len(raw_cells))
        self.assertEqual(import_rows, 1)


if __name__ == "__main__":
    unittest.main()
