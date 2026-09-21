"""Tests for per-manager annual shift requirements and leave-credit math."""

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

from django.test import Client, RequestFactory
from django.urls import reverse
from staffing_tool.db import session_scope
from staffing_tool.fiscal_year import pay_periods_for_fy
from staffing_tool.models import ManagerRequirement, WeeklyManagerShift, WeeklyStaffing
from tests._temp_db import TempDbTestCase

import dashboard.views  # noqa: F401 -- registers dashboard.views.manager_shifts below
from dashboard import context_processors
from dashboard.views import helpers

# dashboard/views/__init__.py re-exports the view function `manager_shifts`,
# shadowing the submodule of the same name on the package -- pull the actual
# module (which has DB_PATH, fy_week1_sunday_containing, etc.) via sys.modules.
manager_shifts = sys.modules["dashboard.views.manager_shifts"]


def _week_starts_covering(start, end):
    weeks = []
    cur = start
    from datetime import timedelta

    # Walk to the Sunday on/after start, then every 7 days through end.
    cur = start + timedelta(days=(6 - start.weekday()) % 7)
    while cur <= end:
        weeks.append(cur)
        cur += timedelta(days=7)
    return weeks


class ManagerRequirementTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        from datetime import date

        self.fy_start = manager_shifts.fy_week1_sunday_containing(date(2026, 1, 1))
        self.fy_end = manager_shifts.fy_end_date(self.fy_start)
        periods = pay_periods_for_fy(self.fy_start)
        self.leave_period = periods[1]

        with session_scope(self.db_path) as session:
            for ws in _week_starts_covering(self.fy_start, self.fy_end):
                session.add(
                    WeeklyStaffing(
                        week_start=ws.isoformat(), filled_day=0, filled_night=0
                    )
                )
            session.flush()
            session.add(
                ManagerRequirement(person_display="Bowman", annual_shift_requirement=26)
            )
            # Five line shifts spread across the FY, one leave day inside
            # self.leave_period (a single day is enough to credit the whole PP).
            for i, d in enumerate(
                [
                    self.fy_start,
                    self.fy_start,
                    self.leave_period.end,
                    self.fy_end,
                    self.fy_end,
                ]
            ):
                session.add(
                    WeeklyManagerShift(
                        week_start=_week_starts_covering(self.fy_start, self.fy_end)[
                            0
                        ].isoformat(),
                        person_display="Bowman",
                        role="RN",
                        shift_date=d.isoformat(),
                        event_type="line_shift",
                        base_name="Bedford",
                        service_type="RW",
                        day_night="D",
                        unit_code="D7B",
                    )
                )
            session.add(
                WeeklyManagerShift(
                    week_start=_week_starts_covering(self.fy_start, self.fy_end)[
                        0
                    ].isoformat(),
                    person_display="Bowman",
                    role="RN",
                    shift_date=self.leave_period.start.isoformat(),
                    event_type="leave",
                    leave_type="LT",
                    base_name="",
                    service_type="",
                    day_night="",
                    unit_code="",
                )
            )
        self.patchers = [
            patch.object(mod, "DB_PATH", self.db_path)
            for mod in (helpers, context_processors, manager_shifts)
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def _bowman_row(self, **params):
        request = RequestFactory().get("/manager-shifts/", params)
        ctx = manager_shifts._build_manager_shifts_context(request)
        rows = {r["name"]: r for r in ctx["cumulative_rows"]}
        return rows["Bowman"]

    def test_custom_annual_requirement_and_leave_credit_applied(self):
        row = self._bowman_row(
            fy=str(manager_shifts.fy_label_year(self.fy_start)),
            granularity="quarter",
            date_start=self.fy_start.isoformat(),
            date_end=self.fy_end.isoformat(),
        )
        self.assertEqual(row["annual_requirement"], 26)
        self.assertGreaterEqual(row["leave_pay_periods"], 1)
        # Full-FY range: prorated target == annual_requirement exactly,
        # then leave credit (2 per PP with leave) is subtracted.
        expected_target = 26 - row["leave_pay_periods"] * 2
        self.assertEqual(row["target"], round(float(expected_target), 1))

    def test_aoc_credit_applied_same_as_leave(self):
        periods = pay_periods_for_fy(self.fy_start)
        aoc_period = periods[2]
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyManagerShift(
                    week_start=_week_starts_covering(self.fy_start, self.fy_end)[
                        0
                    ].isoformat(),
                    person_display="Bowman",
                    role="RN",
                    shift_date=aoc_period.start.isoformat(),
                    event_type="aoc",
                    base_name="",
                    service_type="",
                    day_night="",
                    unit_code="",
                    raw_value="AOC",
                )
            )
        row = self._bowman_row(
            fy=str(manager_shifts.fy_label_year(self.fy_start)),
            granularity="quarter",
            date_start=self.fy_start.isoformat(),
            date_end=self.fy_end.isoformat(),
        )
        self.assertGreaterEqual(row["aoc_pay_periods"], 1)
        expected_target = 26 - row["leave_pay_periods"] * 2 - row["aoc_pay_periods"] * 2
        self.assertEqual(row["target"], round(float(expected_target), 1))

    def test_leave_and_aoc_in_same_pay_period_credit_once(self):
        """A pay period holding both leave and AOC backs out 2 shifts, not 4."""
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyManagerShift(
                    week_start=_week_starts_covering(self.fy_start, self.fy_end)[
                        0
                    ].isoformat(),
                    person_display="Bowman",
                    role="RN",
                    # Same pay period as the leave day added in setUp.
                    shift_date=self.leave_period.end.isoformat(),
                    event_type="aoc",
                    base_name="",
                    service_type="",
                    day_night="",
                    unit_code="",
                    raw_value="AOC",
                )
            )
        row = self._bowman_row(
            fy=str(manager_shifts.fy_label_year(self.fy_start)),
            granularity="quarter",
            date_start=self.fy_start.isoformat(),
            date_end=self.fy_end.isoformat(),
        )
        # The overlapping pay period is credited under leave only.
        self.assertEqual(row["leave_pay_periods"], 1)
        self.assertEqual(row["aoc_pay_periods"], 0)
        self.assertEqual(row["leave_credit"] + row["aoc_credit"], 2)
        self.assertEqual(row["target"], round(float(26 - 2), 1))

    def test_default_requirement_when_no_override(self):
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyManagerShift(
                    week_start=_week_starts_covering(self.fy_start, self.fy_end)[
                        0
                    ].isoformat(),
                    person_display="NoOverride",
                    role="MEDIC",
                    shift_date=self.fy_start.isoformat(),
                    event_type="line_shift",
                    base_name="Bedford",
                    service_type="RW",
                    day_night="D",
                    unit_code="D7B",
                )
            )
        request = RequestFactory().get(
            "/manager-shifts/",
            {
                "fy": str(manager_shifts.fy_label_year(self.fy_start)),
                "granularity": "quarter",
                "date_start": self.fy_start.isoformat(),
                "date_end": self.fy_end.isoformat(),
            },
        )
        ctx = manager_shifts._build_manager_shifts_context(request)
        rows = {r["name"]: r for r in ctx["cumulative_rows"]}
        self.assertEqual(
            rows["NoOverride"]["annual_requirement"],
            manager_shifts.MANAGER_MIN_SHIFTS_PER_FY,
        )
        self.assertEqual(rows["NoOverride"]["leave_pay_periods"], 0)
        self.assertEqual(rows["NoOverride"]["aoc_pay_periods"], 0)


class ManagerRequirementSaveViewTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        self.patchers = [
            patch.object(mod, "DB_PATH", self.db_path)
            for mod in (helpers, context_processors, manager_shifts)
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_save_creates_and_updates_requirement(self):
        client = Client(HTTP_HOST="localhost")
        resp = client.post(
            reverse("manager_requirement_save"),
            {"person_display": "Bowman", "annual_shift_requirement": "30"},
        )
        self.assertEqual(resp.status_code, 302)
        with session_scope(self.db_path) as session:
            row = session.get(ManagerRequirement, "Bowman")
            self.assertIsNotNone(row)
            self.assertEqual(row.annual_shift_requirement, 30)

        resp2 = client.post(
            reverse("manager_requirement_save"),
            {"person_display": "Bowman", "annual_shift_requirement": "40"},
        )
        self.assertEqual(resp2.status_code, 302)
        with session_scope(self.db_path) as session:
            row = session.get(ManagerRequirement, "Bowman")
            self.assertEqual(row.annual_shift_requirement, 40)

    def test_save_rejects_non_numeric(self):
        client = Client(HTTP_HOST="localhost")
        client.post(
            reverse("manager_requirement_save"),
            {"person_display": "Bowman", "annual_shift_requirement": "not-a-number"},
        )
        with session_scope(self.db_path) as session:
            row = session.get(ManagerRequirement, "Bowman")
            self.assertIsNone(row)
