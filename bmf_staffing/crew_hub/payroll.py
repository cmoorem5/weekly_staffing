"""
Per-person hours reporting for payroll export (e.g. upload to ADP).

Comm Center seats carry fixed paid hours (12h shifts; Orientee/Extra
counts 0 unless adjusted). Duty officer roles are day-based coverage, so
they are reported as duty days rather than hours. Sick and leave (LT)
days are tracked in their own buckets and excluded from worked hours.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import shifts
from .models import (
    WORK_LEAVE,
    WORK_OT,
    WORK_SICK,
    WORK_SWAP,
    WORK_TYPE_CHOICES,
    CommShiftAssignment,
    DutyAssignment,
)

WORK_TYPE_LABELS = dict(WORK_TYPE_CHOICES)


@dataclass
class PersonTotals:
    person: str
    regular: float = 0.0
    overtime: float = 0.0
    swap: float = 0.0
    sick: float = 0.0
    leave: float = 0.0
    duty_days: int = 0
    shifts: int = 0

    @property
    def worked(self) -> float:
        """Hours actually worked: regular + overtime + swap (sick/leave excluded)."""
        return self.regular + self.overtime + self.swap


@dataclass
class HoursReport:
    start: dt.date
    end: dt.date
    person_filter: str = ""
    totals: list[PersonTotals] = field(default_factory=list)
    detail: list[dict] = field(default_factory=list)

    @property
    def grand_worked(self) -> float:
        return sum(t.worked for t in self.totals)

    @property
    def grand_sick(self) -> float:
        return sum(t.sick for t in self.totals)

    @property
    def grand_leave(self) -> float:
        return sum(t.leave for t in self.totals)

    @property
    def grand_duty_days(self) -> int:
        return sum(t.duty_days for t in self.totals)


def build_hours_report(
    start: dt.date, end: dt.date, person_filter: str = ""
) -> HoursReport:
    """Aggregate comm hours and duty days per person over a date range."""
    report = HoursReport(start=start, end=end, person_filter=person_filter)
    totals: dict[str, PersonTotals] = {}
    needle = person_filter.strip().lower()

    def totals_for(person: str) -> PersonTotals:
        if person not in totals:
            totals[person] = PersonTotals(person=person)
        return totals[person]

    comm_assignments = (
        CommShiftAssignment.objects.filter(date__gte=start, date__lte=end)
        .select_related("member")
        .order_by("date", "seat")
    )
    for assignment in comm_assignments:
        person = assignment.name
        if not person or person.upper() == "OPEN":
            continue
        if needle and needle not in person.lower():
            continue
        seat = shifts.COMM_SEAT_INDEX[assignment.seat]
        hours = seat.hours
        row_totals = totals_for(person)
        row_totals.shifts += 1
        if assignment.work_type == WORK_SICK:
            row_totals.sick += hours
        elif assignment.work_type == WORK_LEAVE:
            row_totals.leave += hours
        elif assignment.work_type == WORK_OT:
            row_totals.overtime += hours
        elif assignment.work_type == WORK_SWAP:
            row_totals.swap += hours
        else:
            row_totals.regular += hours
        report.detail.append(
            {
                "date": assignment.date,
                "person": person,
                "assignment": f"Comm {seat.label}"
                + (f" ({seat.time})" if seat.time else ""),
                "work_type": WORK_TYPE_LABELS.get(
                    assignment.work_type, assignment.work_type
                ),
                "work_type_code": assignment.work_type,
                "hours": (
                    0.0
                    if assignment.work_type in (WORK_SICK, WORK_LEAVE)
                    else hours
                ),
            }
        )

    duty_assignments = (
        DutyAssignment.objects.filter(date__gte=start, date__lte=end)
        .select_related("officer")
        .order_by("date", "role")
    )
    for assignment in duty_assignments:
        person = assignment.name
        if not person:
            continue
        if needle and needle not in person.lower():
            continue
        row_totals = totals_for(person)
        row_totals.duty_days += 1
        report.detail.append(
            {
                "date": assignment.date,
                "person": person,
                "assignment": f"Duty {shifts.DUTY_ROLE_LABELS[assignment.role]}",
                "work_type": WORK_TYPE_LABELS.get(
                    assignment.work_type, assignment.work_type
                ),
                "work_type_code": assignment.work_type,
                "hours": 0.0,
            }
        )

    report.detail.sort(key=lambda row: (row["date"], row["person"]))
    report.totals = sorted(totals.values(), key=lambda t: t.person.lower())
    return report


def summary_csv_rows(report: HoursReport) -> list[list]:
    """ADP-style per-person totals: one row per employee per earnings bucket."""
    rows = [
        [
            "Person",
            "Regular Hours",
            "Overtime Hours",
            "Swap Hours",
            "Sick Hours",
            "Leave Hours",
            "Total Worked Hours",
            "Duty Days",
            "Comm Shifts",
        ]
    ]
    for t in report.totals:
        rows.append(
            [
                t.person,
                f"{t.regular:.2f}",
                f"{t.overtime:.2f}",
                f"{t.swap:.2f}",
                f"{t.sick:.2f}",
                f"{t.leave:.2f}",
                f"{t.worked:.2f}",
                t.duty_days,
                t.shifts,
            ]
        )
    return rows


def detail_csv_rows(report: HoursReport) -> list[list]:
    """One row per assignment: the audit trail behind the summary."""
    rows = [["Date", "Person", "Assignment", "Work Type", "Hours"]]
    for row in report.detail:
        rows.append(
            [
                row["date"].isoformat(),
                row["person"],
                row["assignment"],
                row["work_type"],
                f"{row['hours']:.2f}",
            ]
        )
    return rows
