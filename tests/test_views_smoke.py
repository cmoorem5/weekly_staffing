"""Smoke tests: key dashboard pages render (200) against an empty database.

These guard URL routing + template rendering so a future refactor can't
silently 500 a core page. They patch DB_PATH on each view module to a fresh
temp database, so they behave the same locally and in CI.
"""

import contextlib
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
)

from dashboard import context_processors
from dashboard.views import (
    helpers,
    home,
    import_schedule,
    manager_shifts,
    person_ops,
    reports,
    settings_views,
    staffing_dashboard,
    weeks,
)

_DB_PATH_MODULES = [
    helpers,
    context_processors,
    home,
    reports,
    settings_views,
    weeks,
    import_schedule,
    staffing_dashboard,
    manager_shifts,
    person_ops,
]

_SMOKE_URL_NAMES = [
    "home",
    "reports_index",
    "settings_index",
    "week_list",
    "import_schedule",
    "staffing_dashboard",
    "manager_shifts",
    "person_ops_report",
]


class DashboardSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "smoke.db")
        init_db(self.db_path)

    def tearDown(self):
        import staffing_tool.db as db_mod

        resolved = db_mod._resolve_db_path(self.db_path)
        get_engine(self.db_path).dispose()
        _get_engine_cached.cache_clear()
        _sessionmaker_for_path.cache_clear()
        db_mod._DB_READY_PATHS.discard(resolved)
        self.tmp.cleanup()

    def test_core_pages_render_with_empty_db(self):
        with contextlib.ExitStack() as stack:
            for mod in _DB_PATH_MODULES:
                if hasattr(mod, "DB_PATH"):
                    stack.enter_context(patch.object(mod, "DB_PATH", self.db_path))
            client = Client(HTTP_HOST="localhost")
            for name in _SMOKE_URL_NAMES:
                with self.subTest(page=name):
                    resp = client.get(reverse(name))
                    self.assertEqual(
                        resp.status_code,
                        200,
                        f"{name} returned {resp.status_code}",
                    )

    def test_root_redirects_to_schedule_board(self):
        client = Client(HTTP_HOST="localhost")
        resp = client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/hub/")


if __name__ == "__main__":
    unittest.main()
