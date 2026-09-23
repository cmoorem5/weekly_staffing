"""Tests for per-manager annual requirements, manual LT credit, and AOC credit."""

import os
import sys
from datetime import date, timedelta
from html.parser import HTMLParser
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


class _FirstTableCells(HTMLParser):
    """Cell counts per row of the first <table> in a page.

    The summary table is hand-written markup with a head, body and foot, so a
    column added to one and not the others is a real (and invisible) defect.
    """

    def __init__(self):
        super().__init__()
        self.rows: list[int] = []
        self._depth = 0
        self._done = False
        self._count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table" and not self._done:
            self._depth += 1
        elif tag == "tr" and self._depth:
            self._count = 0
        elif tag in ("td", "th") and self._depth:
            self._count += 1

    def handle_endtag(self, tag):
        if tag == "tr" and self._depth:
            self.rows.append(self._count)
        elif tag == "table" and self._depth:
            self._depth -= 1
            self._done = True


def _week_starts_covering(start, end):
    weeks = []
    # Walk to the Sunday on/after start, then every 7 days through end.
    cur = start + timedelta(days=(6 - start.weekday()) % 7)
    while cur <= end:
        weeks.append(cur)
        cur += timedelta(days=7)
    return weeks


class ManagerRequirementTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()

        self.fy_start = manager_shifts.fy_week1_sunday_containing(date(2026, 1, 1))
        self.fy_end = manager_shifts.fy_end_date(self.fy_start)
        self.weeks = _week_starts_covering(self.fy_start, self.fy_end)
        periods = pay_periods_for_fy(self.fy_start)
        self.leave_period = periods[1]
        self.aoc_period = periods[2]

        with session_scope(self.db_path) as session:
            for ws in self.weeks:
                session.add(
                    WeeklyStaffing(
                        week_start=ws.isoformat(), filled_day=0, filled_night=0
                    )
                )
            session.flush()
            session.add(
                ManagerRequirement(person_display="Bowman", annual_shift_requirement=26)
            )
            # Five line shifts spread across the FY, plus one leave day (which
            # must not move the target on its own).
            for d in [
                self.fy_start,
                self.fy_start,
                self.leave_period.end,
                self.fy_end,
                self.fy_end,
            ]:
                session.add(self._shift(d, "line_shift"))
            session.add(
                self._shift(
                    self.leave_period.start, event_type="leave", leave_type="LT"
                )
            )
        self.patchers = [
            patch.object(mod, "DB_PATH", self.db_path)
            for mod in (helpers, context_processors, manager_shifts)
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def _shift(self, shift_date, event_type, person="Bowman", **kwargs):
        staffed = event_type == "line_shift"
        return WeeklyManagerShift(
            week_start=self.weeks[0].isoformat(),
            person_display=person,
            role="RN",
            shift_date=shift_date.isoformat(),
            event_type=event_type,
            base_name="Bedford" if staffed else "",
            service_type="RW" if staffed else "",
            day_night="D" if staffed else "",
            unit_code="D7B" if staffed else "",
            raw_value="" if staffed else event_type.upper(),
            **kwargs,
        )

    def _row(self, name="Bowman", **params):
        request = RequestFactory().get("/manager-shifts/", params)
        ctx = manager_shifts._build_manager_shifts_context(request)
        rows = {r["name"]: r for r in ctx["cumulative_rows"]}
        return rows[name]

    def _full_fy_row(self, name="Bowman"):
        return self._row(
            name,
            fy=str(manager_shifts.fy_label_year(self.fy_start)),
            granularity="quarter",
            date_start=self.fy_start.isoformat(),
            date_end=self.fy_end.isoformat(),
        )

    def _aoc_week(self, start=None):
        """Seven AOC days = one week of coverage = one shift of credit."""
        first = start or self.aoc_period.start
        return [self._shift(first + timedelta(days=i), "aoc") for i in range(7)]

    def _add(self, *rows):
        with session_scope(self.db_path) as session:
            for row in rows:
                session.add(row)

    def test_imported_leave_is_reference_only(self):
        """LT on the schedule is displayed but never reduces the target."""
        row = self._full_fy_row()
        self.assertEqual(row["annual_requirement"], 26)
        self.assertEqual(row["leave_days"], 1)
        self.assertEqual(row["leave_codes"], "LT")
        self.assertEqual(row["annual_leave_credit"], 0)
        self.assertEqual(row["leave_credit"], 0)
        # Full-FY range: prorated target == annual requirement exactly.
        self.assertEqual(row["target"], 26.0)

    def test_manual_leave_credit_nets_off_annual_requirement(self):
        with session_scope(self.db_path) as session:
            req = session.get(ManagerRequirement, "Bowman")
            req.annual_leave_credit_shifts = 6
            req.leave_credit_note = "4 wks LT + 2 wks AT"
        row = self._full_fy_row()
        self.assertEqual(row["annual_leave_credit"], 6)
        self.assertEqual(row["leave_note"], "4 wks LT + 2 wks AT")
        self.assertEqual(row["leave_credit"], 6.0)
        self.assertEqual(row["target"], 20.0)

    def test_manual_leave_credit_is_prorated_over_partial_range(self):
        """An annual credit scales with the window, like the requirement does."""
        with session_scope(self.db_path) as session:
            session.get(ManagerRequirement, "Bowman").annual_leave_credit_shifts = 13
        half_end = self.fy_start + timedelta(
            days=((self.fy_end - self.fy_start).days + 1) // 2 - 1
        )
        row = self._row(
            fy=str(manager_shifts.fy_label_year(self.fy_start)),
            granularity="quarter",
            date_start=self.fy_start.isoformat(),
            date_end=half_end.isoformat(),
        )
        # Net annual is 26 - 13 = 13, prorated over roughly half the FY.
        self.assertAlmostEqual(row["target"], 6.5, delta=0.2)
        self.assertAlmostEqual(row["leave_credit"], 6.5, delta=0.2)

    def test_aoc_credit_is_total_days_over_seven(self):
        """Scattered AOC days convert on the total, not on how many weeks they touch."""
        # 14 days spread one per week across 14 different weeks: 2 shifts, not 14.
        self._add(
            *[
                self._shift(self.aoc_period.start + timedelta(days=7 * i), "aoc")
                for i in range(14)
            ]
        )
        row = self._full_fy_row()
        self.assertEqual(row["aoc_count"], 14)
        self.assertEqual(row["aoc_weeks"], 2.0)
        self.assertEqual(row["aoc_credit"], 2)
        self.assertEqual(row["target"], 24.0)

    def test_aoc_credit_rounds_to_the_nearest_shift(self):
        """76 AOC days is 10.9 weeks and earns 11 shifts (the Holst case)."""
        credit = manager_shifts._aoc_shift_credit
        self.assertEqual(credit(76), 11)
        self.assertEqual(manager_shifts._aoc_weeks_display(76), 10.9)
        # Exact multiples, each side of the halfway point, and the empty case.
        self.assertEqual(credit(0), 0)
        self.assertEqual(credit(7), 1)
        self.assertEqual(credit(14), 2)
        self.assertEqual(credit(3), 0)
        self.assertEqual(credit(4), 1)
        # Half rounds up, where Python's round() would go to even.
        self.assertEqual(credit(10 * 7 + 4), 11)  # 10.571 -> 11
        self.assertEqual(credit(73), 10)  # 10.43 -> 10

    def test_a_weeks_worth_of_consecutive_aoc_credits_one_shift(self):
        """Seven straight AOC days is one week of coverage, so one shift."""
        self._add(
            *[
                self._shift(self.aoc_period.start + timedelta(days=i), "aoc")
                for i in range(7)
            ]
        )
        row = self._full_fy_row()
        self.assertEqual(row["aoc_credit"], 1)
        self.assertEqual(row["target"], 25.0)

    def test_leave_beside_aoc_adds_nothing(self):
        """Successor to the leave/AOC double-credit guard from #27.

        Leave no longer credits at all, so a day of leave sitting beside AOC
        days changes neither the credit nor the target.
        """
        self._add(self._shift(self.leave_period.end, event_type="aoc"))
        row = self._full_fy_row()
        self.assertEqual(row["leave_days"], 1)
        self.assertEqual(row["leave_credit"], 0)
        self.assertEqual(row["aoc_count"], 1)
        # One AOC day is 0.14 weeks, which rounds to no shift.
        self.assertEqual(row["aoc_credit"], 0)
        self.assertEqual(row["target"], 26.0)

    def test_aoc_and_manual_leave_credit_stack(self):
        with session_scope(self.db_path) as session:
            session.get(ManagerRequirement, "Bowman").annual_leave_credit_shifts = 4
        self._add(*self._aoc_week())
        row = self._full_fy_row()
        self.assertEqual(row["target"], 26.0 - 4 - 1)

    def test_target_never_goes_negative(self):
        with session_scope(self.db_path) as session:
            session.get(ManagerRequirement, "Bowman").annual_leave_credit_shifts = 99
        self._add(*self._aoc_week())
        row = self._full_fy_row()
        self.assertEqual(row["target"], 0.0)

    def test_manager_report_page_renders_credit_columns(self):
        with session_scope(self.db_path) as session:
            req = session.get(ManagerRequirement, "Bowman")
            req.annual_leave_credit_shifts = 6
            req.leave_credit_note = "4 wks LT"
        self._add(*self._aoc_week())
        resp = Client(HTTP_HOST="localhost").get(
            reverse("manager_shifts"),
            {
                "fy": str(manager_shifts.fy_label_year(self.fy_start)),
                "granularity": "quarter",
                "date_start": self.fy_start.isoformat(),
                "date_end": self.fy_end.isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("LT credit (annual)", html)
        self.assertIn(">LT days<", html)
        self.assertIn('name="annual_leave_credit_shifts"', html)
        self.assertIn("4 wks LT", html)
        self.assertIn("1.0 wks (&minus;1)", html)

        parser = _FirstTableCells()
        parser.feed(html)
        # Head, body and foot of the summary table must agree on column count.
        self.assertTrue(parser.rows)
        self.assertEqual(set(parser.rows), {12}, parser.rows)

    def test_default_requirement_when_no_override(self):
        self._add(
            self._shift(self.fy_start, event_type="line_shift", person="NoOverride")
        )
        row = self._full_fy_row("NoOverride")
        self.assertEqual(
            row["annual_requirement"], manager_shifts.MANAGER_MIN_SHIFTS_PER_FY
        )
        self.assertEqual(row["annual_leave_credit"], 0)
        self.assertEqual(row["leave_credit"], 0)
        self.assertEqual(row["aoc_weeks"], 0)
        self.assertEqual(row["leave_days"], 0)


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

    def _post(self, **data):
        client = Client(HTTP_HOST="localhost")
        return client.post(reverse("manager_requirement_save"), data)

    def _row(self):
        with session_scope(self.db_path) as session:
            return session.get(ManagerRequirement, "Bowman")

    def test_save_creates_and_updates_requirement(self):
        self.assertEqual(
            self._post(
                person_display="Bowman", annual_shift_requirement="30"
            ).status_code,
            302,
        )
        self.assertEqual(self._row().annual_shift_requirement, 30)

        self.assertEqual(
            self._post(
                person_display="Bowman", annual_shift_requirement="40"
            ).status_code,
            302,
        )
        self.assertEqual(self._row().annual_shift_requirement, 40)

    def test_save_stores_leave_credit_and_note(self):
        self._post(
            person_display="Bowman",
            annual_shift_requirement="52",
            annual_leave_credit_shifts="8",
            leave_credit_note="6 wks LT",
        )
        row = self._row()
        self.assertEqual(row.annual_leave_credit_shifts, 8)
        self.assertEqual(row.leave_credit_note, "6 wks LT")

    def test_blank_field_leaves_stored_value_alone(self):
        """The row form posts both fields; a blank one must not zero the other."""
        self._post(
            person_display="Bowman",
            annual_shift_requirement="40",
            annual_leave_credit_shifts="8",
            leave_credit_note="6 wks LT",
        )
        self._post(person_display="Bowman", annual_shift_requirement="44")
        row = self._row()
        self.assertEqual(row.annual_shift_requirement, 44)
        self.assertEqual(row.annual_leave_credit_shifts, 8)
        self.assertEqual(row.leave_credit_note, "6 wks LT")

    def test_clearing_the_note_is_possible(self):
        self._post(
            person_display="Bowman",
            annual_leave_credit_shifts="8",
            leave_credit_note="6 wks LT",
        )
        self._post(
            person_display="Bowman",
            annual_leave_credit_shifts="8",
            leave_credit_note="",
        )
        self.assertIsNone(self._row().leave_credit_note)

    def test_save_rejects_non_numeric(self):
        self._post(person_display="Bowman", annual_shift_requirement="not-a-number")
        self.assertIsNone(self._row())

    def test_save_rejects_non_numeric_leave_credit(self):
        self._post(person_display="Bowman", annual_shift_requirement="30")
        self._post(
            person_display="Bowman",
            annual_shift_requirement="30",
            annual_leave_credit_shifts="lots",
        )
        row = self._row()
        self.assertEqual(row.annual_shift_requirement, 30)
        self.assertEqual(row.annual_leave_credit_shifts, 0)
