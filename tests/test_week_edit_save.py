"""Regression test: a blocked save must not silently redirect and discard edits.

week_edit()/week_add() used to call _save_week_and_coverage() and then
unconditionally redirect to week_list, even when the inner function bailed
out early (e.g. because notes are required for a low staffing rate) without
writing anything to the DB. The user would land back on the week list with
an easy-to-miss error message and their corrections gone -- and any
HTML/PDF export would keep reflecting the stale DB row.
"""

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
    DEFAULT_BASES,
    _get_engine_cached,
    _sessionmaker_for_path,
    get_engine,
    init_db,
    session_scope,
)
from staffing_tool.models import WeeklyStaffing

from dashboard.views import helpers, weeks

WEEK_START = "2026-07-05"


def _coverage_post(prefix="cov", *, rw_day=0):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(DEFAULT_BASES)),
        f"{prefix}-INITIAL_FORMS": str(len(DEFAULT_BASES)),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for i, (base_name, _rw, _gr) in enumerate(DEFAULT_BASES):
        data[f"{prefix}-{i}-base_name"] = base_name
        data[f"{prefix}-{i}-rw_staffed_day"] = str(rw_day)
        data[f"{prefix}-{i}-rw_staffed_night"] = "0"
        data[f"{prefix}-{i}-gr_staffed_day"] = "0"
        data[f"{prefix}-{i}-gr_staffed_night"] = "0"
    return data


class WeekEditBlockedSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "week_edit.db")
        init_db(self.db_path)
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=WEEK_START,
                    day_target=8,
                    night_min=4,
                    filled_day=52,
                    filled_night=30,
                    notes="baseline",
                    entered_by="test",
                    created_at="2026-07-05T00:00:00Z",
                    updated_at="2026-07-05T00:00:00Z",
                )
            )
        self._patchers = [
            patch.object(helpers, "DB_PATH", self.db_path),
            patch.object(weeks, "DB_PATH", self.db_path),
        ]
        for p in self._patchers:
            p.start()
        self.client = Client(HTTP_HOST="localhost")

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        import staffing_tool.db as db_mod

        resolved = db_mod._resolve_db_path(self.db_path)
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        db_mod._DB_READY_PATHS.discard(resolved)
        self.tmp.cleanup()

    def _post_edit(self, *, filled_day, filled_night, notes):
        data = {
            "week-week_start": WEEK_START,
            "week-filled_day": str(filled_day),
            "week-filled_night": str(filled_night),
            "week-notes": notes,
        }
        data.update(_coverage_post())
        return self.client.post(reverse("week_edit", args=[WEEK_START]), data=data)

    def test_blocked_save_does_not_redirect_and_preserves_db(self):
        # Zero staffing without a justifying note trips notes_required(), so
        # the save must be rejected.
        resp = self._post_edit(filled_day=0, filled_night=0, notes="")

        self.assertEqual(
            resp.status_code,
            200,
            "a blocked save must re-render the edit form, not redirect",
        )

        with session_scope(self.db_path) as session:
            row = (
                session.query(WeeklyStaffing)
                .filter(WeeklyStaffing.week_start == WEEK_START)
                .first()
            )
            self.assertEqual(
                row.filled_day,
                52,
                "rejected edit must not overwrite the previously saved value",
            )

    def test_allowed_save_redirects_and_persists(self):
        resp = self._post_edit(filled_day=52, filled_night=30, notes="")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("week_list"))

        with session_scope(self.db_path) as session:
            row = (
                session.query(WeeklyStaffing)
                .filter(WeeklyStaffing.week_start == WEEK_START)
                .first()
            )
            self.assertEqual(row.filled_day, 52)
            self.assertEqual(row.filled_night, 30)


if __name__ == "__main__":
    unittest.main()
