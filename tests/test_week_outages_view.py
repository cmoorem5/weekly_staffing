"""The "no vehicle" card on the week edit page: add, update, remove, validate."""

import os
import sys
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
from staffing_tool.db import session_scope
from staffing_tool.models import WeeklyStaffing, WeeklyVehicleOutage
from tests._temp_db import TempDbTestCase

from dashboard import context_processors
from dashboard.views import helpers, weeks

WEEK_START = "2026-10-25"


class WeekOutagesViewTests(TempDbTestCase):
    def setUp(self):
        self.db_path = self.make_temp_db("outages.db")
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=WEEK_START,
                    filled_day=52,
                    filled_night=30,
                    created_at="2026-10-25T00:00:00Z",
                    updated_at="2026-10-25T00:00:00Z",
                )
            )
        self._patchers = [
            patch.object(helpers, "DB_PATH", self.db_path),
            patch.object(weeks, "DB_PATH", self.db_path),
            patch.object(context_processors, "DB_PATH", self.db_path),
        ]
        for p in self._patchers:
            p.start()
        self.client = Client(HTTP_HOST="localhost")
        self.url = reverse("week_outages", args=[WEEK_START])

    def _rows(self):
        with session_scope(self.db_path) as session:
            return [
                (r.day_date, r.unit_code, r.note)
                for r in session.query(WeeklyVehicleOutage)
                .order_by(WeeklyVehicleOutage.id)
                .all()
            ]

    def _add(self, **kw):
        data = {
            "action": "add",
            "day_date": "2026-10-26",
            "unit_code": "D9L",
            "note": "",
        }
        data.update(kw)
        return self.client.post(self.url, data=data)

    def test_add_records_the_outage_and_returns_to_the_week(self):
        resp = self._add(note="aircraft in maintenance")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("week_edit", args=[WEEK_START]))
        self.assertEqual(
            self._rows(), [("2026-10-26", "D9L", "aircraft in maintenance")]
        )

    def test_adding_the_same_vehicle_day_updates_the_note(self):
        self._add(note="first")
        self._add(note="second")
        self.assertEqual(self._rows(), [("2026-10-26", "D9L", "second")])

    def test_rejects_a_day_outside_the_week(self):
        self._add(day_date="2026-11-09")
        self.assertEqual(self._rows(), [])

    def test_rejects_an_unknown_or_extra_vehicle(self):
        self._add(unit_code="NOPE")
        self._add(unit_code="GR2")
        self.assertEqual(self._rows(), [])

    def test_remove_deletes_only_this_weeks_checked_rows(self):
        self._add(unit_code="D9L")
        self._add(unit_code="N7B")
        with session_scope(self.db_path) as session:
            keep = (
                session.query(WeeklyVehicleOutage)
                .filter(WeeklyVehicleOutage.unit_code == "N7B")
                .one()
                .id
            )
            drop = (
                session.query(WeeklyVehicleOutage)
                .filter(WeeklyVehicleOutage.unit_code == "D9L")
                .one()
                .id
            )
        self.client.post(self.url, data={"action": "remove", "remove_ids": [str(drop)]})
        self.assertEqual([r[1] for r in self._rows()], ["N7B"])
        self.assertTrue(keep)

    def test_get_redirects_without_changing_anything(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._rows(), [])

    def test_week_edit_page_shows_the_card(self):
        self._add(note="aircraft in maintenance")
        resp = self.client.get(reverse("week_edit", args=[WEEK_START]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Vehicles out of service", body)
        self.assertIn("aircraft in maintenance", body)
        self.assertIn("D9L (Lawrence RW day)", body)
        self.assertNotIn("G9B", body)
