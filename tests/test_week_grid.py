"""Week grid: edit seats and out-of-service marks, everything rebuilds from it."""

import os
import sys
from datetime import date, timedelta
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
from staffing_tool.db import session_scope
from staffing_tool.metrics import compute_week_metrics
from staffing_tool.models import (
    BaseConfig,
    WeeklyBaseCoverage,
    WeeklyOpsViewAssignment,
    WeeklyOpsViewDay,
    WeeklyStaffing,
    WeeklyVehicleOutage,
)
from staffing_tool.schedule_import import UNIT_MAP
from staffing_tool.shortfall import analyze_week, planned_groups
from staffing_tool.week_grid import (
    apply_grid_post,
    has_grid,
    load_week_grid,
    outage_field,
    seat_field,
)
from tests._temp_db import TempDbTestCase

from dashboard import context_processors
from dashboard.views import helpers, weeks

# The views package re-exports the function under the module's own name.
week_grid_view = importlib.import_module("dashboard.views.week_grid")
import_schedule = importlib.import_module("dashboard.views.import_schedule")

WEEK = "2026-11-08"
MON = date(2026, 11, 9)
OPERATOR = {"RW": "PIC", "GR": "EMT"}


class _Post(dict):
    """Form stand-in: ``.get`` like a QueryDict."""


class WeekGridTests(TempDbTestCase):
    def setUp(self):
        self.db_path = self.make_temp_db("grid.db")
        start = date.fromisoformat(WEEK)
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=WEEK,
                    filled_day=0,
                    filled_night=0,
                    created_at="2026-11-08T00:00:00Z",
                    updated_at="2026-11-08T00:00:00Z",
                )
            )
            session.flush()
            # Every planned vehicle fully staffed on all seven days.
            for i in range(7):
                day = (start + timedelta(days=i)).isoformat()
                for g in planned_groups():
                    for unit in g.units:
                        for seat in ("RN", "Medic", OPERATOR[UNIT_MAP[unit][1]]):
                            session.add(
                                WeeklyOpsViewAssignment(
                                    week_start=WEEK,
                                    day_date=day,
                                    unit_code=unit,
                                    role=seat,
                                    excel_row=10,
                                    person_display=f"{unit} {seat}",
                                    raw_value=f"{unit} {seat}",
                                    is_staffed=1,
                                )
                            )

    def _post(self, session, **changes):
        """Submit the grid exactly as stored, plus ``changes`` (key -> value)."""
        grid = load_week_grid(session, WEEK)
        post = _Post()
        for row in grid.rows:
            for seat, names in row.seats.items():
                post[seat_field(row.day, row.unit, seat)] = names
            if row.out_of_service:
                post[outage_field(row.day, row.unit)] = "on"
        for key, value in changes.items():
            if value is None:
                post.pop(key, None)
            else:
                post[key] = value
        return post

    def _metrics(self, session):
        week = session.query(WeeklyStaffing).one()
        covs = session.query(WeeklyBaseCoverage).all()
        return compute_week_metrics(week, covs, session.query(BaseConfig).all())

    def test_grid_lists_every_planned_vehicle_for_every_day(self):
        with session_scope(self.db_path) as session:
            self.assertTrue(has_grid(session, WEEK))
            grid = load_week_grid(session, WEEK)
        planned = {u for g in planned_groups() for u in g.units}
        self.assertEqual(len(grid.rows), 7 * len(planned))
        self.assertTrue(all(r.staffed for r in grid.rows))
        row = next(r for r in grid.rows if r.unit == "D7B" and r.day == MON)
        self.assertEqual(
            row.seats, {"RN": "D7B RN", "Medic": "D7B Medic", "PIC": "D7B PIC"}
        )

    def test_unchanged_grid_saves_nothing_and_keeps_provenance(self):
        with session_scope(self.db_path) as session:
            result = apply_grid_post(session, WEEK, self._post(session))
            self.assertFalse(result.changed)
            rows = session.query(WeeklyOpsViewAssignment.excel_row).distinct().all()
        self.assertEqual({r[0] for r in rows}, {10})

    def test_rebuild_matches_a_full_week(self):
        with session_scope(self.db_path) as session:
            apply_grid_post(session, WEEK, self._post(session))
            m = self._metrics(session)
            self.assertEqual(m.system_rw_pct, 1.0)
            self.assertEqual(m.system_gr_pct, 1.0)
            self.assertEqual(m.staffing_rate, 1.0)
            days = session.query(WeeklyOpsViewDay).count()
        self.assertGreater(days, 0)

    def test_emptying_a_seat_makes_the_vehicle_short_everywhere(self):
        with session_scope(self.db_path) as session:
            post = self._post(session, **{seat_field(MON, "D9L", "Medic"): ""})
            result = apply_grid_post(session, WEEK, post)
            self.assertEqual(result.cells_changed, 1)
            m = self._metrics(session)
            self.assertAlmostEqual(m.system_rw_pct, 55 / 56)
            shorts = analyze_week(session, WEEK).shortfalls
        self.assertEqual(
            [(s.unit_code, s.missing_crew) for s in shorts], [("D9L", ("Medic",))]
        )

    def test_typing_a_name_fills_the_seat(self):
        with session_scope(self.db_path) as session:
            apply_grid_post(
                session,
                WEEK,
                self._post(session, **{seat_field(MON, "D9L", "Medic"): ""}),
            )
            apply_grid_post(
                session,
                WEEK,
                self._post(session, **{seat_field(MON, "D9L", "Medic"): "Moved, Pat"}),
            )
            m = self._metrics(session)
            row = next(
                r
                for r in load_week_grid(session, WEEK).rows
                if r.unit == "D9L" and r.day == MON
            )
        self.assertEqual(m.system_rw_pct, 1.0)
        self.assertEqual(row.seats["Medic"], "Moved, Pat")

    def test_out_of_service_aircraft_comes_off_the_plan_even_with_a_crew(self):
        with session_scope(self.db_path) as session:
            changes = {
                outage_field(date.fromisoformat(WEEK) + timedelta(days=i), "D11M"): "on"
                for i in range(7)
            }
            result = apply_grid_post(session, WEEK, self._post(session, **changes))
            self.assertEqual(result.outages_added, 7)
            m = self._metrics(session)
            mansfield = m.base_metrics["Mansfield"]
        self.assertEqual(mansfield["rw_planned"], 0)
        self.assertEqual(mansfield["rw_staffed"], 0)
        self.assertEqual(m.rw_total_unit_days, 49)
        self.assertEqual(m.system_rw_pct, 1.0)
        self.assertEqual(m.required_total, 84 - 7)

    def test_unticking_clears_the_mark_and_restores_the_plan(self):
        key = outage_field(MON, "D11M")
        with session_scope(self.db_path) as session:
            apply_grid_post(session, WEEK, self._post(session, **{key: "on"}))
            self.assertEqual(session.query(WeeklyVehicleOutage).count(), 1)
            result = apply_grid_post(session, WEEK, self._post(session, **{key: None}))
            self.assertEqual(result.outages_removed, 1)
            self.assertEqual(session.query(WeeklyVehicleOutage).count(), 0)
            self.assertEqual(self._metrics(session).rw_total_unit_days, 56)


class WeekGridViewTests(TempDbTestCase):
    def setUp(self):
        self.db_path = self.make_temp_db("gridview.db")
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=WEEK,
                    filled_day=0,
                    filled_night=0,
                    created_at="2026-11-08T00:00:00Z",
                    updated_at="2026-11-08T00:00:00Z",
                )
            )
            session.flush()
            session.add(
                WeeklyOpsViewAssignment(
                    week_start=WEEK,
                    day_date="2026-11-09",
                    unit_code="D7B",
                    role="RN",
                    excel_row=5,
                    person_display="Nurse, Ann",
                    raw_value="Nurse, Ann",
                    is_staffed=1,
                )
            )
        self._patchers = [
            patch.object(helpers, "DB_PATH", self.db_path),
            patch.object(weeks, "DB_PATH", self.db_path),
            patch.object(week_grid_view, "DB_PATH", self.db_path),
            patch.object(import_schedule, "DB_PATH", self.db_path),
            patch.object(context_processors, "DB_PATH", self.db_path),
        ]
        for p in self._patchers:
            p.start()
        self.client = Client(HTTP_HOST="localhost")
        self.url = reverse("week_grid", args=[WEEK])

    def test_page_renders_the_grid(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Nurse, Ann", resp.content.decode())
        self.assertIn("Out of service", resp.content.decode())

    def test_week_without_detail_redirects_to_the_week_list(self):
        resp = self.client.get(reverse("week_grid", args=["2026-11-15"]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("week_list"))

    def test_post_saves_and_returns_to_the_grid(self):
        data = {
            seat_field(MON, "D7B", "RN"): "Nurse, Ann",
            seat_field(MON, "D7B", "Medic"): "Medic, Bob",
            outage_field(MON, "D11M"): "on",
        }
        resp = self.client.post(self.url, data=data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], self.url)
        with session_scope(self.db_path) as session:
            self.assertEqual(session.query(WeeklyVehicleOutage).count(), 1)
            medics = (
                session.query(WeeklyOpsViewAssignment)
                .filter(WeeklyOpsViewAssignment.role == "Medic")
                .all()
            )
        self.assertEqual([m.person_display for m in medics], ["Medic, Bob"])

    def test_week_edit_links_to_the_grid(self):
        resp = self.client.get(reverse("week_edit", args=[WEEK]))
        self.assertIn(self.url, resp.content.decode())
