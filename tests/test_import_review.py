"""Import review queue: unknown units, unlinked names, one-click fixes."""

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bmf_staffing"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmf_staffing.settings")

import django

django.setup()

from django.test import Client
from django.urls import reverse
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
    StaffRosterEntry,
    UnitCodeMapping,
    WeeklyPersonShift,
    WeeklyStaffing,
)

review_mod = importlib.import_module("dashboard.views.import_review")

WEEK = "2026-06-07"


class ImportReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "review.db")
        init_db(self.db_path)
        with session_scope(self.db_path) as session:
            session.add(WeeklyStaffing(week_start=WEEK, filled_day=0, filled_night=0))
            session.commit()
            session.add(
                ScheduleImport(
                    week_start=WEEK,
                    imported_at="2026-06-08T00:00:00Z",
                    source_filename="test.xlsx",
                )
            )
            session.add(
                ScheduleParseIssue(
                    week_start=WEEK,
                    sheet="RN",
                    cell="D12",
                    raw_value="9X",
                    issue_type="unknown_unit",
                    message="Unknown unit code '9X' (base '9X') for role RN.",
                )
            )
            session.add(
                StaffRosterEntry(
                    last_name="Smith",
                    first_name="Jane",
                    role="RN",
                    active=1,
                    created_at="2026-01-01T00:00:00Z",
                )
            )
            session.add(
                WeeklyPersonShift(
                    week_start=WEEK,
                    person_display="Smyth, Jane",
                    shift_date="2026-06-08",
                    role="RN",
                    event_type="staffed",
                    included_in_aggregates=1,
                )
            )
            session.commit()

    def tearDown(self):
        import staffing_tool.db as db_mod

        resolved = db_mod._resolve_db_path(self.db_path)
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        db_mod._DB_READY_PATHS.discard(resolved)
        self.tmp.cleanup()

    def _client(self):
        return Client(HTTP_HOST="localhost")

    def _patched(self):
        return patch.object(review_mod, "DB_PATH", self.db_path)

    def test_page_lists_unknown_units_and_unlinked_names(self):
        with self._patched():
            resp = self._client().get(reverse("import_review"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("9X", html)
        self.assertIn("Smyth, Jane", html)
        # Fuzzy suggestion (Smith, Jane) offered in the dropdown.
        self.assertIn("Smith, Jane", html)
        self.assertIn("2 item(s) need review", html)

    def test_map_unit_saves_alias(self):
        with self._patched():
            self._client().post(
                reverse("import_review"),
                {
                    "action": "map_unit",
                    "week": WEEK,
                    "raw_code": "9X",
                    "maps_to": "D7B",
                },
            )
        with session_scope(self.db_path) as session:
            row = (
                session.query(UnitCodeMapping)
                .filter(UnitCodeMapping.raw_code == "9X")
                .one()
            )
            self.assertEqual(row.maps_to, "D7B")

    def test_map_unit_rejects_unknown_canonical(self):
        with self._patched():
            self._client().post(
                reverse("import_review"),
                {
                    "action": "map_unit",
                    "week": WEEK,
                    "raw_code": "9X",
                    "maps_to": "NOT-A-UNIT",
                },
            )
        with session_scope(self.db_path) as session:
            self.assertEqual(session.query(UnitCodeMapping).count(), 0)

    def test_link_name_updates_shift_rows(self):
        with session_scope(self.db_path) as session:
            entry_id = session.query(StaffRosterEntry.id).scalar()
        with self._patched():
            self._client().post(
                reverse("import_review"),
                {
                    "action": "link_name",
                    "week": WEEK,
                    "display": "Smyth, Jane",
                    "role": "RN",
                    "entry_id": str(entry_id),
                },
            )
        with session_scope(self.db_path) as session:
            shift = session.query(WeeklyPersonShift).one()
            self.assertEqual(shift.staff_member_id, entry_id)

    def test_add_and_link_creates_roster_entry(self):
        with self._patched():
            self._client().post(
                reverse("import_review"),
                {
                    "action": "add_link",
                    "week": WEEK,
                    "display": "Smyth, Jane",
                    "role": "RN",
                },
            )
        with session_scope(self.db_path) as session:
            entry = (
                session.query(StaffRosterEntry)
                .filter(StaffRosterEntry.last_name == "Smyth")
                .one()
            )
            self.assertEqual(entry.first_name, "Jane")
            shift = session.query(WeeklyPersonShift).one()
            self.assertEqual(shift.staff_member_id, entry.id)


if __name__ == "__main__":
    unittest.main()
