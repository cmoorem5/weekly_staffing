"""One registry describing the two person-first schedulers.

The Comm Center and duty officer schedulers differ only in which models,
field names, and labels they use. Both the day editor / month calendar
(``views.schedulers``) and the rotation manager / calendar APIs
(``views.rotations``) read this single table, so adding a scheduler — or
renaming a field on one — is a one-place change.
"""

from __future__ import annotations

from .. import shifts
from ..models import (
    CommRotation,
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
    DutyRotation,
)
from ..services import apply_duty_rotations_for_range, apply_rotations_for_range

KINDS = {
    "comm": {
        "assignment_model": CommShiftAssignment,
        "rotation_model": CommRotation,
        "person_model": CommStaffMember,
        "person_field": "member",
        "slot_field": "seat",
        "slot_codes": set(shifts.COMM_SEAT_INDEX),
        "slot_label": "Seat",
        "person_label": "Staff member",
        "title": "Comm Center",
        "day_url": "crew_hub:comm_day",
        "month_url": "crew_hub:comm_month",
        "roster_url": "crew_hub:comm_staff",
        "rotations_url": "crew_hub:comm_rotations",
        "month_path": "/hub/comm/",
        "apply_range": apply_rotations_for_range,
    },
    "duty": {
        "assignment_model": DutyAssignment,
        "rotation_model": DutyRotation,
        "person_model": DutyOfficer,
        "person_field": "officer",
        "slot_field": "role",
        "slot_codes": set(shifts.DUTY_ROLE_LABELS),
        "slot_label": "Role",
        "person_label": "Duty officer",
        "title": "Duty officers",
        "day_url": "crew_hub:duty_day",
        "month_url": "crew_hub:duty_month",
        "roster_url": "crew_hub:duty_roster",
        "rotations_url": "crew_hub:duty_rotations",
        "month_path": "/hub/duty/",
        "apply_range": apply_duty_rotations_for_range,
    },
}


def slot_options(kind: str, blank: bool = True) -> list[tuple[str, str]]:
    """Slot dropdown choices for one scheduler.

    ``blank`` adds the leading "unassigned" option the day editor and
    calendar chip menu need; rotations leave it off because a repeating
    pattern always names the slot it fills.
    """
    if kind == "comm":
        slots = [
            (seat.code, f"{seat.label} ({seat.time})" if seat.time else seat.label)
            for seat in shifts.COMM_SEATS
        ]
    else:
        slots = list(shifts.DUTY_ROLE_CHOICES)
    if blank:
        return [("", f"— {shifts.UNASSIGNED_LABEL} —")] + slots
    return slots


def slot_sort_key(kind: str):
    """Order assignments by slot, with unassigned people first."""
    order = (
        {seat.code: i for i, seat in enumerate(shifts.COMM_SEATS)}
        if kind == "comm"
        else {role: i for i, role in enumerate(shifts.DUTY_ROLE_ORDER)}
    )
    slot_field = KINDS[kind]["slot_field"]

    def key(assignment):
        slot = getattr(assignment, slot_field)
        # -1 sorts blank slots above every real slot.
        return (order.get(slot, 99) if slot else -1, assignment.name.lower())

    return key
