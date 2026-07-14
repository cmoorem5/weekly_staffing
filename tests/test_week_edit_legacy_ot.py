"""Regression test: editing a legacy week must not silently erase its OT.

Weeks imported/backfilled before the day/night OT split store overtime only
in the legacy ot_rn/ot_medic/ot_emt/ot_shifts totals. The edit form only
shows the day/night columns (all zero for such weeks), so saving the form
untouched used to overwrite the legacy totals with zeros — wiping the
week's OT out of OT dependency, the board pack, and the trend charts.
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

from dashboard import context_processors
from dashboard.views import helpers, weeks

WEEK_START = "2026-07-05"


def _coverage_post(prefix="cov"):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(DEFAULT_BASES)),
        f"{prefix}-INITIAL_FORMS": str(len(DEFAULT_BASES)),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for i, (base_name, _rw, _gr) in enumerate(DEFAULT_BASES):
        data[f"{prefix}-{i}-base_name"] = base_name
        data[f"{prefix}-{i}-rw_staffed_day"] = "0"
        data[f"{prefix}-{i}-rw_staffed_night"] = "0"
        data[f"{prefix}-{i}-gr_staffed_day"] = "0"
        data[f"{prefix}-{i}-gr_staffed_night"] = "0"
    return data


class WeekEditLegacyOtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "legacy_ot.db")
        init_db(self.db_path)
        self._patchers = [
            patch.object(helpers, "DB_PATH", self.db_path),
            patch.object(weeks, "DB_PATH", self.db_path),
            patch.object(context_processors, "DB_PATH", self.db_path),
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

    def _add_week(self, **ot_fields):
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
                    **ot_fields,
                )
            )

    def _post_edit(self, **ot_inputs):
        data = {
            "week-week_start": WEEK_START,
            "week-filled_day": "52",
            "week-filled_night": "30",
            "week-notes": "",
        }
        for field in (
            "ot_rn_day",
            "ot_rn_night",
            "ot_medic_day",
            "ot_medic_night",
            "ot_emt_day",
            "ot_emt_night",
        ):
            data[f"week-{field}"] = str(ot_inputs.get(field, 0))
        data.update(_coverage_post())
        return self.client.post(reverse("week_edit", args=[WEEK_START]), data=data)

    def _reload_week(self):
        with session_scope(self.db_path) as session:
            return (
                session.query(WeeklyStaffing)
                .filter(WeeklyStaffing.week_start == WEEK_START)
                .first()
            )

    def test_untouched_save_preserves_legacy_only_ot(self):
        # Legacy week: OT lives only in the aggregate columns; split is zero.
        self._add_week(ot_shifts=6, ot_rn=3, ot_medic=2, ot_emt=1)

        resp = self._post_edit()  # all day/night OT inputs zero (form default)
        self.assertEqual(resp.status_code, 302)

        row = self._reload_week()
        self.assertEqual(row.ot_shifts, 6, "legacy OT total must survive the save")
        self.assertEqual(row.ot_rn, 3)
        self.assertEqual(row.ot_medic, 2)
        self.assertEqual(row.ot_emt, 1)

    def test_zero_submission_still_clears_split_ot(self):
        # Week with real day/night values: the form displayed them, so an
        # all-zero submission is a deliberate clear and must be honored.
        self._add_week(
            ot_shifts=5,
            ot_rn=3,
            ot_medic=2,
            ot_rn_day=2,
            ot_rn_night=1,
            ot_medic_day=2,
        )

        resp = self._post_edit()
        self.assertEqual(resp.status_code, 302)

        row = self._reload_week()
        self.assertEqual(row.ot_shifts, 0)
        self.assertEqual(row.ot_rn, 0)
        self.assertEqual(row.ot_rn_day, 0)

    def test_new_split_values_replace_legacy_totals(self):
        self._add_week(ot_shifts=6, ot_rn=3, ot_medic=2, ot_emt=1)

        resp = self._post_edit(ot_rn_day=4, ot_medic_night=1)
        self.assertEqual(resp.status_code, 302)

        row = self._reload_week()
        self.assertEqual(row.ot_rn, 4)
        self.assertEqual(row.ot_medic, 1)
        self.assertEqual(row.ot_emt, 0)
        self.assertEqual(row.ot_shifts, 5)
        self.assertEqual(row.ot_rn_day, 4)
        self.assertEqual(row.ot_medic_night, 1)


if __name__ == "__main__":
    unittest.main()
