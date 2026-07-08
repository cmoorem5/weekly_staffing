"""Django staff roster settings page (remove / CSRF)."""

import os
import re
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
from staffing_tool.models import StaffRosterEntry as SaStaffRosterEntry

from dashboard.models import StaffRosterEntry as DjangoStaffRosterEntry
from dashboard.views import helpers as view_helpers
from dashboard.views import settings_views


class StaffRosterSettingsViewTests(unittest.TestCase):
    def test_remove_forms_include_csrf_token(self):
        # The roster listing is read via the Django ORM ("staffing" alias),
        # not the SQLAlchemy DB_PATH used elsewhere in this file, so the
        # seed row has to go through that same ORM for the view to see it.
        entry = DjangoStaffRosterEntry.objects.using("staffing").create(
            last_name="Smith",
            first_name="Jane",
            role="RN",
            active=True,
            created_at="2026-06-10T00:00:00Z",
        )
        try:
            c = Client(HTTP_HOST="localhost")
            resp = c.get(reverse("staff_roster_settings"))
            self.assertEqual(resp.status_code, 200)
            forms = re.findall(
                r'<form method="post" class="d-inline".*?</form>',
                resp.content.decode("utf-8"),
                re.DOTALL,
            )
            self.assertGreater(len(forms), 0)
            for form in forms:
                self.assertIn("csrfmiddlewaretoken", form)
        finally:
            entry.delete(using="staffing")

    def test_deactivate_removes_active_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            init_db(db_path)
            with session_scope(db_path) as session:
                session.add(
                    SaStaffRosterEntry(
                        last_name="Smith",
                        first_name="Jane",
                        role="RN",
                        active=1,
                        created_at="2026-06-10T00:00:00Z",
                    )
                )
                session.flush()
                entry_id = session.query(SaStaffRosterEntry).first().id

            with (
                patch.object(view_helpers, "DB_PATH", db_path),
                patch.object(settings_views, "DB_PATH", db_path),
            ):
                c = Client(HTTP_HOST="localhost")
                url = reverse("staff_roster_settings")
                c.get(url)
                csrf = c.cookies["csrftoken"].value
                resp = c.post(
                    url,
                    {
                        "action": "deactivate",
                        "roster_id": str(entry_id),
                        "csrfmiddlewaretoken": csrf,
                    },
                    follow=True,
                )
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"Removed Smith, Jane (RN)", resp.content)
            with session_scope(db_path) as session:
                row = session.query(SaStaffRosterEntry).filter_by(id=entry_id).one()
                self.assertEqual(row.active, 0)
            get_engine(db_path).dispose()
            _get_engine_cached.cache_clear()
            _sessionmaker_for_path.cache_clear()


if __name__ == "__main__":
    unittest.main()
