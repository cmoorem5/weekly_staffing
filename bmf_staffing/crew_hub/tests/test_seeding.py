"""Report seeding: skeleton row counts and pulls from the live schedules."""

import datetime as dt

from django.test import TestCase

from crew_hub import shifts
from crew_hub.models import (
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
    Vehicle,
)
from crew_hub.services import get_or_create_report, refresh_from_sources

DATE = dt.date(2026, 7, 2)


class SeedingCountTests(TestCase):
    def setUp(self):
        self.report, created = get_or_create_report(DATE)
        self.assertTrue(created)

    def test_get_or_create_is_idempotent(self):
        report, created = get_or_create_report(DATE)
        self.assertFalse(created)
        self.assertEqual(report.pk, self.report.pk)
        self.assertEqual(report.crew_entries.count(), shifts.EXPECTED_CREW_ROW_COUNT)

    def test_crew_skeleton_counts(self):
        self.assertEqual(
            self.report.crew_entries.count(), shifts.EXPECTED_CREW_ROW_COUNT
        )
        per_base_expected = {"BED": 12, "PYM": 8, "LWM": 10, "MAN": 6, "MHT": 3}
        for base, expected in per_base_expected.items():
            self.assertEqual(
                self.report.crew_entries.filter(base=base).count(), expected, base
            )

    def test_crew_positions_per_shift(self):
        for shift in shifts.CREW_SHIFTS:
            rows = self.report.crew_entries.filter(
                base=shift.base, shift_code=shift.code
            )
            self.assertEqual(
                sorted(rows.values_list("position", flat=True)),
                sorted(shift.positions),
                f"{shift.base}/{shift.code}",
            )

    def test_fixed_section_counts(self):
        self.assertEqual(self.report.duty_entries.count(), 6)
        self.assertEqual(self.report.comm_entries.count(), 10)
        self.assertEqual(self.report.vehicle_entries.count(), 16)
        self.assertEqual(self.report.transport_base_counts.count(), 5)
        self.assertEqual(self.report.miss_counts.count(), 6)
        self.assertEqual(
            list(self.report.miss_counts.values_list("label", flat=True)),
            shifts.DEFAULT_MISS_CATEGORIES,
        )

    def test_names_start_blank(self):
        self.assertFalse(
            self.report.crew_entries.exclude(name="").exists(),
            "crew names must start blank",
        )


class SchedulePullTests(TestCase):
    def setUp(self):
        officer = DutyOfficer.objects.create(name="Duty Test-Alpha")
        DutyAssignment.objects.create(date=DATE, role="AOC", officer=officer)
        DutyAssignment.objects.create(
            date=DATE, role="MDOC", display_name="Duty Test-Bravo"
        )
        DutyAssignment.objects.create(
            date=DATE, role="MDOC", display_name="Duty Test-Charlie"
        )
        member = CommStaffMember.objects.create(name="Comms Test-Alpha")
        CommShiftAssignment.objects.create(date=DATE, seat="D", member=member)
        CommShiftAssignment.objects.create(
            date=DATE, seat="N", display_name="Comms Test-Bravo"
        )
        Vehicle.ensure_fleet()
        Vehicle.objects.filter(identifier="N141NE").update(
            current_status="BED OOS - Maintenance"
        )

    def test_seed_pulls_from_schedulers_and_vehicle_board(self):
        report, _ = get_or_create_report(DATE)
        duty = {e.role: e.name for e in report.duty_entries.all()}
        self.assertEqual(duty["AOC"], "Duty Test-Alpha")
        self.assertEqual(duty["MDOC"], "Duty Test-Bravo / Duty Test-Charlie")
        self.assertEqual(duty["BPM"], "")

        comm = {e.seat: e.name for e in report.comm_entries.all()}
        self.assertEqual(comm["D"], "Comms Test-Alpha")
        self.assertEqual(comm["N"], "Comms Test-Bravo")
        self.assertEqual(comm["S"], "")

        vehicle = report.vehicle_entries.get(vehicle_id="N141NE")
        self.assertEqual(vehicle.status, "BED OOS - Maintenance")

    def test_refresh_from_sources_updates_draft(self):
        report, _ = get_or_create_report(DATE)
        Vehicle.objects.filter(identifier="N141NE").update(current_status="BED Primary")
        DutyAssignment.objects.filter(role="AOC").delete()
        DutyAssignment.objects.create(
            date=DATE, role="AOC", display_name="Duty Test-Delta"
        )
        refresh_from_sources(report)
        self.assertEqual(
            report.vehicle_entries.get(vehicle_id="N141NE").status, "BED Primary"
        )
        self.assertEqual(report.duty_entries.get(role="AOC").name, "Duty Test-Delta")
