"""Tests for the training events summary report (counts by role, by period)."""

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
from staffing_tool.models import WeeklyPersonShift, WeeklyStaffing
from staffing_tool.person_ops import load_person_ops_summary
from tests._temp_db import TempDbTestCase

from dashboard import context_processors
from dashboard.views import helpers, person_ops, training_summary


def _training_row(
    *, week_start, shift_date, role, raw_value, person="Smith, Jane"
) -> WeeklyPersonShift:
    return WeeklyPersonShift(
        week_start=week_start,
        shift_date=shift_date,
        role=role,
        event_type="training",
        raw_value=raw_value,
        person_display=person,
        included_in_aggregates=0,
    )


class TrainingSummaryReportTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        weeks = ["2025-10-05", "2025-10-12"]
        with session_scope(self.db_path) as session:
            for w in weeks:
                session.add(WeeklyStaffing(week_start=w, filled_day=0, filled_night=0))
            session.flush()
            session.add_all(
                [
                    _training_row(
                        week_start="2025-10-05",
                        shift_date="2025-10-06",
                        role="RN",
                        raw_value="CRM",
                    ),
                    _training_row(
                        week_start="2025-10-05",
                        shift_date="2025-10-06",
                        role="RN",
                        raw_value="SIM",
                        person="Doe, John",
                    ),
                    _training_row(
                        week_start="2025-10-05",
                        shift_date="2025-10-07",
                        role="MEDIC",
                        raw_value="CRM",
                    ),
                    _training_row(
                        week_start="2025-10-12",
                        shift_date="2025-10-13",
                        role="EMT",
                        raw_value="EDU",
                    ),
                ]
            )
        self.patchers = [
            patch.object(mod, "DB_PATH", self.db_path)
            for mod in (helpers, context_processors, training_summary, person_ops)
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def _context(self, **params) -> dict[str, object]:
        request = RequestFactory().get("/ops/training/", params)
        return training_summary._build_training_summary_context(request)

    def test_role_counts_by_week(self):
        ctx = self._context(
            granularity="week",
            date_start="2025-10-05",
            date_end="2025-10-18",
            fy="2026",
        )
        rows = {r["bucket_start"]: r for r in ctx["table_rows"]}
        self.assertEqual(rows["2025-10-05"]["rn_count"], 2)
        self.assertEqual(rows["2025-10-05"]["medic_count"], 1)
        self.assertEqual(rows["2025-10-05"]["emt_count"], 0)
        self.assertEqual(rows["2025-10-12"]["emt_count"], 1)
        totals = ctx["totals_by_role"]
        self.assertEqual(totals["RN"], 2)
        self.assertEqual(totals["MEDIC"], 1)
        self.assertEqual(totals["EMT"], 1)
        self.assertEqual(ctx["grand_total"], 4)

    def test_role_counts_pooled_into_larger_period(self):
        ctx = self._context(
            granularity="month",
            date_start="2025-10-05",
            date_end="2025-10-18",
            fy="2026",
        )
        rows = ctx["table_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rn_count"], 2)
        self.assertEqual(rows[0]["medic_count"], 1)
        self.assertEqual(rows[0]["emt_count"], 1)
        self.assertEqual(rows[0]["total_count"], 4)

    def test_code_breakdown_by_role(self):
        ctx = self._context(
            granularity="week",
            date_start="2025-10-05",
            date_end="2025-10-18",
            fy="2026",
        )
        by_code = {r["code"]: r for r in ctx["code_breakdown_rows"]}
        self.assertEqual(by_code["CRM"]["rn_count"], 1)
        self.assertEqual(by_code["CRM"]["medic_count"], 1)
        self.assertEqual(by_code["SIM"]["rn_count"], 1)

    def test_export_csv_returns_ok(self):
        client = Client(HTTP_HOST="localhost")
        resp = client.get(
            reverse("training_summary_export_csv"),
            {
                "granularity": "week",
                "date_start": "2025-10-05",
                "date_end": "2025-10-18",
                "fy": "2026",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8-sig")
        self.assertIn("Training events summary", body)
        self.assertIn("CRM", body)


class PersonOpsTrainingCountTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(week_start="2025-10-05", filled_day=0, filled_night=0)
            )
            session.flush()
            session.add_all(
                [
                    _training_row(
                        week_start="2025-10-05",
                        shift_date="2025-10-06",
                        role="RN",
                        raw_value="CRM",
                        person="Smith, Jane",
                    ),
                    _training_row(
                        week_start="2025-10-05",
                        shift_date="2025-10-07",
                        role="RN",
                        raw_value="SIM",
                        person="Smith, Jane",
                    ),
                ]
            )

    def test_summary_counts_training_events(self):
        from datetime import date

        summary = load_person_ops_summary(
            self.db_path,
            "Smith, Jane",
            date(2025, 10, 1),
            date(2025, 10, 31),
        )
        self.assertEqual(summary.training_count, 2)
