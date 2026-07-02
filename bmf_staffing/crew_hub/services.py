"""
Report seeding and workflow services.

Opening a report date get-or-creates the DailyReport and seeds every section:
duty roster and Comm Center from the schedulers, vehicle statuses from the
live board, the crew skeleton with blank names, and the transport tables.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone

from . import shifts
from .models import (
    CommCenterEntry,
    CommShiftAssignment,
    CrewEntry,
    DailyReport,
    DutyAssignment,
    DutyRosterEntry,
    MissCategoryCount,
    ReportAuditLog,
    TransportBaseCount,
    TransportSummary,
    Vehicle,
    VehicleStatusEntry,
)


def _duty_names_for(date: dt.date) -> dict[str, str]:
    """Role -> joined names from the duty scheduler (two MDOCs -> 'A / B')."""
    names: dict[str, list[str]] = {}
    for assignment in DutyAssignment.objects.filter(date=date).select_related(
        "officer"
    ):
        if assignment.name:
            names.setdefault(assignment.role, []).append(assignment.name)
    return {role: " / ".join(people) for role, people in names.items()}


def _comm_names_for(date: dt.date) -> dict[str, str]:
    """Seat -> name from the Comm Center scheduler."""
    return {
        a.seat: a.name
        for a in CommShiftAssignment.objects.filter(date=date).select_related("member")
        if a.name
    }


@transaction.atomic
def get_or_create_report(date: dt.date) -> tuple[DailyReport, bool]:
    """Return the report for ``date``, seeding all sections on first create."""
    report, created = DailyReport.objects.get_or_create(report_date=date)
    if created:
        seed_report(report)
    return report, created


def seed_report(report: DailyReport) -> None:
    """Populate a fresh report with skeleton rows and scheduler pulls."""
    duty_names = _duty_names_for(report.report_date)
    DutyRosterEntry.objects.bulk_create(
        DutyRosterEntry(report=report, role=role, name=duty_names.get(role, ""))
        for role in shifts.DUTY_ROLE_ORDER
    )

    CrewEntry.objects.bulk_create(
        CrewEntry(
            report=report, base=shift.base, shift_code=shift.code, position=position
        )
        for shift in shifts.CREW_SHIFTS
        for position in shift.positions
    )

    comm_names = _comm_names_for(report.report_date)
    CommCenterEntry.objects.bulk_create(
        CommCenterEntry(report=report, seat=seat.code, name=comm_names.get(seat.code, ""))
        for seat in shifts.COMM_SEATS
    )

    Vehicle.ensure_fleet()
    VehicleStatusEntry.objects.bulk_create(
        VehicleStatusEntry(
            report=report,
            vehicle_id=vehicle.identifier,
            category=vehicle.category,
            status=vehicle.current_status,
        )
        for vehicle in Vehicle.objects.filter(active=True)
    )

    TransportSummary.objects.create(report=report)
    TransportBaseCount.objects.bulk_create(
        TransportBaseCount(report=report, base=base)
        for base in shifts.TRANSPORT_BASE_ORDER
    )
    MissCategoryCount.objects.bulk_create(
        MissCategoryCount(report=report, order=i, label=label)
        for i, label in enumerate(shifts.DEFAULT_MISS_CATEGORIES)
    )


@transaction.atomic
def refresh_from_sources(report: DailyReport) -> None:
    """Re-pull duty roster, Comm Center, and vehicle statuses for a draft.

    Only fills from the live sources; does not touch crew names, transports,
    or free-text sections. Overwrites the three pulled sections wholesale.
    """
    duty_names = _duty_names_for(report.report_date)
    for entry in report.duty_entries.all():
        entry.name = duty_names.get(entry.role, "")
        entry.save(update_fields=["name"])

    comm_names = _comm_names_for(report.report_date)
    for entry in report.comm_entries.all():
        entry.name = comm_names.get(entry.seat, "")
        entry.save(update_fields=["name"])

    Vehicle.ensure_fleet()
    statuses = {v.identifier: v.current_status for v in Vehicle.objects.all()}
    for entry in report.vehicle_entries.all():
        entry.status = statuses.get(entry.vehicle_id, entry.status)
        entry.save(update_fields=["status"])


def submit_report(report: DailyReport, user) -> None:
    """Lock the report and record the submit event (email is sent separately)."""
    report.status = DailyReport.STATUS_SUBMITTED
    report.submitted_by = user
    report.submitted_at = timezone.now()
    report.save(update_fields=["status", "submitted_by", "submitted_at", "updated_at"])
    ReportAuditLog.objects.create(
        report=report, action=ReportAuditLog.ACTION_SUBMITTED, actor=user
    )


def reopen_report(report: DailyReport, user) -> None:
    """Unlock a submitted report; caller must hold crew_hub.reopen_report."""
    report.status = DailyReport.STATUS_DRAFT
    report.save(update_fields=["status", "updated_at"])
    ReportAuditLog.objects.create(
        report=report, action=ReportAuditLog.ACTION_REOPENED, actor=user
    )
