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


class WeekCoverageCardTests(WeekOutagesViewTests):
    """The reasons card and the recompute hooks, with real schedule detail."""

    def setUp(self):
        super().setUp()
        from datetime import date, timedelta

        from staffing_tool.models import WeeklyBaseCoverage, WeeklyOpsViewAssignment
        from staffing_tool.schedule_import import UNIT_MAP

        full_rw = ("RN", "Medic", "PIC")
        full_gr = ("RN", "Medic", "EMT")
        units = ("D7B", "N7B", "D7P", "N7P", "D9L", "N9L", "D11M", "D11H")
        trucks = ("GR", "LG", "MG", "NG")
        start = date.fromisoformat(WEEK_START)
        with session_scope(self.db_path) as session:
            for i in range(7):
                day = (start + timedelta(days=i)).isoformat()
                for unit in units + trucks:
                    roles = full_rw if UNIT_MAP[unit][1] == "RW" else full_gr
                    for role in roles:
                        # Monday: Lawrence day aircraft has no pilot.
                        if i == 1 and unit == "D9L" and role == "PIC":
                            continue
                        session.add(
                            WeeklyOpsViewAssignment(
                                week_start=WEEK_START,
                                day_date=day,
                                unit_code=unit,
                                role=role,
                                person_display="N",
                                raw_value="N",
                                is_staffed=1,
                            )
                        )
            session.add(
                WeeklyBaseCoverage(
                    week_start=WEEK_START,
                    base_name="Lawrence",
                    rw_staffed_unit_days=13,
                    rw_staffed_day=6,
                    rw_staffed_night=7,
                    gr_staffed_unit_days=7,
                    gr_staffed_day=7,
                )
            )

    def test_card_names_the_missing_pilot(self):
        resp = self.client.get(reverse("week_edit", args=[WEEK_START]))
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Coverage and why vehicles were short", body)
        self.assertIn("Short a pilot", body)
        self.assertIn("D9L", body)

    def test_card_drops_days_fixed_by_hand_without_extra_wording(self):
        url = reverse("week_edit", args=[WEEK_START])
        before = self.client.get(url).content.decode()
        self.assertIn("<td>Pilot</td>", before)  # Lawrence's pilot gap is listed
        # Lawrence stored at 14 while the schedule supports 13: that day was fixed.
        with session_scope(self.db_path) as session:
            from staffing_tool.models import WeeklyBaseCoverage

            cov = (
                session.query(WeeklyBaseCoverage).filter_by(base_name="Lawrence").one()
            )
            cov.rw_staffed_day = 7
            cov.rw_staffed_night = 7
            cov.rw_staffed_unit_days = 14
        after = self.client.get(url).content.decode()
        self.assertNotIn("<td>Pilot</td>", after)
        self.assertNotIn("Corrected by hand", after)

    def test_marking_no_vehicle_removes_it_from_the_rotor_plan(self):
        self._add(day_date="2026-10-26", unit_code="D9L", note="aircraft down")
        with session_scope(self.db_path) as session:
            from staffing_tool.models import WeeklyBaseCoverage

            lawrence = (
                session.query(WeeklyBaseCoverage).filter_by(base_name="Lawrence").one()
            )
            self.assertEqual(lawrence.rw_planned_removed, 1)

    def test_saving_keeps_the_calculated_pilot_vacancy(self):
        from staffing_tool.db import DEFAULT_BASES

        prefix = "cov"
        data = {
            "week-week_start": WEEK_START,
            "week-training_shifts": "0",
            "week-notes": "saved",
            f"{prefix}-TOTAL_FORMS": str(len(DEFAULT_BASES)),
            f"{prefix}-INITIAL_FORMS": str(len(DEFAULT_BASES)),
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for i, (base_name, _rw, _gr) in enumerate(DEFAULT_BASES):
            data[f"{prefix}-{i}-base_name"] = base_name
            for col in ("rw_staffed_day", "rw_staffed_night", "gr_staffed_day"):
                data[f"{prefix}-{i}-{col}"] = "0"
            data[f"{prefix}-{i}-gr_staffed_night"] = "0"
        resp = self.client.post(reverse("week_edit", args=[WEEK_START]), data=data)
        self.assertEqual(resp.status_code, 302)
        with session_scope(self.db_path) as session:
            week = session.query(WeeklyStaffing).one()
            # D9L has no pilot on Monday in the saved schedule.
            self.assertEqual(week.pilot_vacancies, 1)

    def test_editing_ground_counts_moves_the_weekly_total(self):
        from staffing_tool.db import DEFAULT_BASES
        from staffing_tool.metrics import compute_week_metrics
        from staffing_tool.models import BaseConfig, WeeklyBaseCoverage

        prefix = "cov"
        data = {
            "week-week_start": WEEK_START,
            "week-filled_day": "52",
            "week-filled_night": "30",
            "week-training_shifts": "0",
            "week-notes": "coverage corrected",
            f"{prefix}-TOTAL_FORMS": str(len(DEFAULT_BASES)),
            f"{prefix}-INITIAL_FORMS": str(len(DEFAULT_BASES)),
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for i, (base_name, _rw, _gr) in enumerate(DEFAULT_BASES):
            data[f"{prefix}-{i}-base_name"] = base_name
            data[f"{prefix}-{i}-rw_staffed_day"] = "0"
            data[f"{prefix}-{i}-rw_staffed_night"] = "0"
            data[f"{prefix}-{i}-gr_staffed_day"] = (
                "6" if base_name == "Lawrence" else "0"
            )
            data[f"{prefix}-{i}-gr_staffed_night"] = "0"
        resp = self.client.post(reverse("week_edit", args=[WEEK_START]), data=data)
        self.assertEqual(resp.status_code, 302)
        with session_scope(self.db_path) as session:
            row = session.query(WeeklyStaffing).one()
            covs = session.query(WeeklyBaseCoverage).all()
            m = compute_week_metrics(row, covs, session.query(BaseConfig).all())
        self.assertEqual(m.gr_staffed_unit_days, 6)
        self.assertAlmostEqual(m.system_gr_pct, 6 / 28)
