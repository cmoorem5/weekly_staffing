"""Model-level validation: crew combos and REF flag round-trips."""

import datetime as dt

from django.core.exceptions import ValidationError
from django.test import TestCase

from crew_hub.models import CrewEntry, DailyReport


class CrewEntryValidationTests(TestCase):
    def setUp(self):
        self.report = DailyReport.objects.create(report_date=dt.date(2026, 7, 2))

    def test_valid_combo_saves(self):
        entry = CrewEntry.objects.create(
            report=self.report,
            base="BED",
            shift_code="D7B",
            position="RN",
            name="RN Test-Alpha",
        )
        self.assertIsNotNone(entry.pk)

    def test_bonus_rn_on_pg_rejected(self):
        with self.assertRaises(ValidationError):
            CrewEntry.objects.create(
                report=self.report, base="PYM", shift_code="PG", position="RN"
            )

    def test_shift_from_wrong_base_rejected(self):
        with self.assertRaises(ValidationError):
            CrewEntry.objects.create(
                report=self.report, base="MHT", shift_code="D7B", position="RN"
            )

    def test_emt_on_rotor_shift_rejected(self):
        with self.assertRaises(ValidationError):
            CrewEntry.objects.create(
                report=self.report, base="LWM", shift_code="D9L", position="EMT"
            )

    def test_ref_flag_round_trips(self):
        entry = CrewEntry.objects.create(
            report=self.report,
            base="PYM",
            shift_code="PG",
            position="EMT",
            ref_flag=True,
        )
        entry.refresh_from_db()
        self.assertTrue(entry.ref_flag)
        entry.ref_flag = False
        entry.save()
        entry.refresh_from_db()
        self.assertFalse(entry.ref_flag)
