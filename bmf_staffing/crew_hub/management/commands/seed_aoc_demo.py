"""
Seed demo data for Crew Hub using obviously fake names.

Usage:
    python manage.py seed_aoc_demo --date 2026-07-02

Creates duty officers, comm staff, one day of scheduler assignments,
vehicle statuses, and a draft AOC report pre-filled with fake crew names
and sample transport numbers. Never touches submitted reports.
"""

import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from crew_hub.models import (
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
    MissCategoryCount,
    PendingTransport,
    Vehicle,
)
from crew_hub.services import get_or_create_report, refresh_from_sources

DUTY_DEMO = {
    "AOC": "Duty Test-Alpha",
    "AAOC": "Duty Test-Bravo",
    "MDOC": "Duty Test-Charlie",
    "PEDIDOC": "Duty Test-Delta",
    "ITOC": "Duty Test-Echo",
    "BPM": "Duty Test-Foxtrot",
}

COMM_DEMO = {
    "D": "Comms Test-Alpha",
    "D2": "Comms Test-Bravo",
    "S": "Comms Test-Charlie",
    "S2": "Comms Test-Delta",
    "S3": "OPEN",
    "N": "Comms Test-Echo",
    "N2": "Comms Test-Foxtrot",
    "P": "Comms Test-Golf",
    "P2": "Comms Test-Hotel",
}

VEHICLE_DEMO = {
    "N141NE": "BED OOS - Maintenance",
    "N142NE": "PYM Primary",
    "N143NE": "MAN Primary",
    "N144NE": "MHT Primary",
    "N145NE": "LWM Primary",
    "N246NE": "PYM Backup",
    "N247NE": "BED Primary",
    "Med 11": "BED INIS - Needs medical equipment",
    "Med 12": "PYM Primary",
    "Med 14": "LWM Primary",
    "Med 15": "BED Backup",
    "Med 16": "BED Primary",
    "Med 17": "MAN Primary",
    "Med 18": "PYM Backup",
    "Med 19": "OOS - Maintenance",
    "Med 20": "LWM Backup",
}

CREW_NAME_BY_POSITION = {
    "RN": "RN Test-{tag}",
    "EMTP": "EMTP Test-{tag}",
    "PILOT": "Pilot Test-{tag}",
    "EMT": "EMT Test-{tag}",
}

TAGS = [
    "Alpha",
    "Bravo",
    "Charlie",
    "Delta",
    "Echo",
    "Foxtrot",
    "Golf",
    "Hotel",
    "India",
    "Juliett",
    "Kilo",
    "Lima",
    "Mike",
    "November",
]


class Command(BaseCommand):
    help = "Seed Crew Hub demo data (fake names only) for a given date."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            required=True,
            help="Report date, YYYY-MM-DD",
        )

    def handle(self, *args, **options):
        try:
            date = dt.date.fromisoformat(options["date"])
        except ValueError as exc:
            raise CommandError(f"Invalid --date: {options['date']}") from exc

        for name in DUTY_DEMO.values():
            DutyOfficer.objects.get_or_create(name=name)
        for role, name in DUTY_DEMO.items():
            officer = DutyOfficer.objects.get(name=name)
            DutyAssignment.objects.update_or_create(
                date=date,
                role=role,
                officer=officer,
                defaults={"display_name": ""},
            )

        for seat, name in COMM_DEMO.items():
            if name == "OPEN":
                CommShiftAssignment.objects.update_or_create(
                    date=date,
                    seat=seat,
                    defaults={"member": None, "display_name": "OPEN"},
                )
                continue
            member, _ = CommStaffMember.objects.get_or_create(name=name)
            CommShiftAssignment.objects.update_or_create(
                date=date, seat=seat, defaults={"member": member, "display_name": ""}
            )

        Vehicle.ensure_fleet()
        for identifier, status in VEHICLE_DEMO.items():
            Vehicle.objects.filter(identifier=identifier).update(current_status=status)

        report, created = get_or_create_report(date)
        if report.is_submitted:
            raise CommandError(
                f"Report for {date} is already submitted; refusing to modify it."
            )
        if not created:
            refresh_from_sources(report)

        tag_index = 0
        for entry in report.crew_entries.all():
            tag = TAGS[tag_index % len(TAGS)]
            tag_index += 1
            entry.name = CREW_NAME_BY_POSITION[entry.position].format(tag=tag)
            entry.save(update_fields=["name"])
        # One confirmed open position to demo the REF flag.
        ref_entry = report.crew_entries.filter(
            base="PYM", shift_code="PG", position="EMT"
        ).first()
        if ref_entry:
            ref_entry.name = ""
            ref_entry.ref_flag = True
            ref_entry.save(update_fields=["name", "ref_flag"])

        report.weather = "Mostly sunny. High 81°F. No weather impacts anticipated."
        report.save(update_fields=["weather", "updated_at"])

        summary = report.transport_summary
        summary.pending_count = 1
        summary.complex_calls = "None."
        summary.save(update_fields=["pending_count", "complex_calls"])

        report.pending_transports.all().delete()
        PendingTransport.objects.create(
            report=report,
            order=1,
            call_type="CCT",
            asset="N142NE",
            status="En route",
            location="Test General",
        )
        for row in report.transport_base_counts.all():
            row.gcct = 1
            row.rw = 2 if row.base in ("BED", "PYM") else 0
            row.save(update_fields=["gcct", "rw"])
        MissCategoryCount.objects.filter(report=report, label="Weather").update(count=1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded demo schedules, vehicle statuses, and a draft AOC "
                f"report for {date} (report {'created' if created else 'updated'})."
            )
        )
