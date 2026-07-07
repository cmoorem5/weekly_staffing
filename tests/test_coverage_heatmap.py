"""Coverage heatmap view: base × day-of-week grids from ops-view days."""

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

import importlib

from django.test import Client
from django.urls import reverse
from staffing_tool.db import (
    _get_engine_cached,
    _sessionmaker_for_path,
    get_engine,
    init_db,
    session_scope,
)
from staffing_tool.models import BaseConfig, WeeklyOpsViewDay, WeeklyStaffing

# The function re-exported by dashboard.views shadows the submodule name.
heatmap_mod = importlib.import_module("dashboard.views.coverage_heatmap")


class CoverageHeatmapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "heatmap.db")
        init_db(self.db_path)
        with session_scope(self.db_path) as session:
            # init_db seeds the standard bases: keep only Bedford planning
            # 2 RW units/day (14 unit-days per week) and no GR anywhere.
            for cfg in session.query(BaseConfig).all():
                cfg.rw_total_unit_days = 14 if cfg.base_name == "Bedford" else 0
                cfg.gr_total_unit_days = 0
            for ws in ("2026-06-07", "2026-06-14"):
                session.add(WeeklyStaffing(week_start=ws, filled_day=0, filled_night=0))
            session.commit()
            # Two Sundays: one fully staffed, one half staffed -> 75% Sun avg.
            session.add(
                WeeklyOpsViewDay(
                    week_start="2026-06-07",
                    day_date="2026-06-07",
                    base_name="Bedford",
                    rw_count=2,
                    gr_count=0,
                )
            )
            session.add(
                WeeklyOpsViewDay(
                    week_start="2026-06-14",
                    day_date="2026-06-14",
                    base_name="Bedford",
                    rw_count=1,
                    gr_count=0,
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

    def _get(self, params=""):
        with patch.object(heatmap_mod, "DB_PATH", self.db_path):
            client = Client(HTTP_HOST="localhost")
            return client.get(reverse("coverage_heatmap") + params)

    def test_renders_grid_with_average_coverage(self):
        resp = self._get("?start=2026-06-01&end=2026-06-20")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("Bedford", html)
        # Sunday average: (2 + 1) staffed / (2 planned × 2 Sundays) = 75%.
        self.assertIn('hm-watch"', html)
        self.assertIn(">75%</td>", html)
        # Weekdays with no imported data render as empty, not 0%.
        self.assertIn("hm-none", html)
        # No base plans GR, so the GR grid shows its empty message.
        self.assertIn("No bases with a GR plan", html)

    def test_empty_database_shows_empty_state(self):
        with session_scope(self.db_path) as session:
            session.query(WeeklyOpsViewDay).delete()
            session.commit()
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"No per-day coverage data", resp.content)


if __name__ == "__main__":
    unittest.main()
