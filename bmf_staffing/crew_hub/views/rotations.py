"""Comm rotation management and calendar interaction APIs.

CrewSense-style behavior: rotations are repeating patterns that
materialize into seat assignments, right-click sets the work type
(sick / swap / overtime), and drag-and-drop moves or swaps assignments
on the month calendar.
"""

from __future__ import annotations

import datetime as dt
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .. import shifts
from ..models import CommRotation, CommShiftAssignment, CommStaffMember
from ..services import apply_rotations_for_range

WEEKDAY_OPTIONS = [
    (6, "Sun"),
    (0, "Mon"),
    (1, "Tue"),
    (2, "Wed"),
    (3, "Thu"),
    (4, "Fri"),
    (5, "Sat"),
]


@login_required
def comm_rotations(request):
    if request.method == "POST":
        action = request.POST.get("action", "add")
        if action == "add":
            _add_rotation(request)
        elif action == "toggle":
            rotation = CommRotation.objects.filter(
                pk=request.POST.get("pk") or None
            ).first()
            if rotation:
                rotation.active = not rotation.active
                rotation.save(update_fields=["active"])
                state = "resumed" if rotation.active else "paused"
                messages.success(
                    request, f"Rotation for {rotation.member.name} {state}."
                )
        elif action == "delete":
            rotation = CommRotation.objects.filter(
                pk=request.POST.get("pk") or None
            ).first()
            if rotation:
                rotation.delete()
                messages.success(
                    request,
                    f"Rotation for {rotation.member.name} deleted. Existing "
                    "calendar days are kept; remove them from the day editor "
                    "if needed.",
                )
        return redirect("crew_hub:comm_rotations")

    return render(
        request,
        "crew_hub/comm_rotations.html",
        {
            "rotations": CommRotation.objects.select_related("member"),
            "members": CommStaffMember.objects.filter(active=True),
            "seats": shifts.COMM_SEATS,
            "weekday_options": WEEKDAY_OPTIONS,
        },
    )


def _add_rotation(request) -> None:
    member = CommStaffMember.objects.filter(
        pk=request.POST.get("member") or None
    ).first()
    seat = request.POST.get("seat", "")
    anchor_raw = request.POST.get("anchor_date", "")
    if not member or seat not in shifts.COMM_SEAT_INDEX:
        messages.error(request, "Pick a staff member and a seat.")
        return
    try:
        anchor = dt.date.fromisoformat(anchor_raw)
    except ValueError:
        messages.error(request, "A valid start date is required.")
        return
    end_raw = request.POST.get("end_date", "").strip()
    end_date = None
    if end_raw:
        try:
            end_date = dt.date.fromisoformat(end_raw)
        except ValueError:
            messages.error(request, "End date must be YYYY-MM-DD.")
            return

    pattern_type = request.POST.get("pattern_type", CommRotation.PATTERN_CYCLE)
    weekdays = ",".join(
        str(d) for d, _ in WEEKDAY_OPTIONS if f"weekday_{d}" in request.POST
    )

    def _int(name: str, default: int) -> int:
        try:
            return max(0, int(request.POST.get(name, default)))
        except (TypeError, ValueError):
            return default

    rotation = CommRotation(
        member=member,
        seat=seat,
        pattern_type=pattern_type,
        days_on=_int("days_on", 4),
        days_off=_int("days_off", 4),
        weekdays=weekdays if pattern_type == CommRotation.PATTERN_WEEKLY else "",
        anchor_date=anchor,
        end_date=end_date,
        note=request.POST.get("note", "").strip(),
    )
    if pattern_type == CommRotation.PATTERN_CYCLE and rotation.days_on == 0:
        messages.error(request, "Days on must be at least 1 for a cycle pattern.")
        return
    if pattern_type == CommRotation.PATTERN_WEEKLY and not rotation.weekday_set:
        messages.error(request, "Pick at least one weekday for a weekly pattern.")
        return
    rotation.save()
    messages.success(
        request,
        f"Rotation added: {member.name} — {rotation.get_seat_display()} "
        f"({rotation.pattern_label}). Use “Apply rotations” on the month "
        "view to fill the calendar.",
    )


@login_required
@require_POST
def comm_rotations_apply(request):
    """Materialize active rotations for the month shown on the calendar."""
    raw = request.POST.get("month", "")
    try:
        year_s, month_s = raw.split("-")
        first = dt.date(int(year_s), int(month_s), 1)
    except ValueError:
        messages.error(request, "Invalid month.")
        return redirect("crew_hub:comm_month")
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)

    created, skipped = apply_rotations_for_range(first, last)
    if created:
        messages.success(
            request,
            f"Rotations applied for {first:%B %Y}: {created} day(s) filled"
            + (f", {skipped} already assigned (kept as-is)." if skipped else "."),
        )
    else:
        messages.info(
            request,
            f"Nothing to fill for {first:%B %Y} — every rotation day is "
            "already assigned or no active rotations match.",
        )
    return redirect(f"/hub/comm/?month={first.year:04d}-{first.month:02d}")


def _assignment_or_error(pk):
    assignment = (
        CommShiftAssignment.objects.select_related("member").filter(pk=pk).first()
    )
    if assignment is None:
        return None, JsonResponse(
            {"ok": False, "error": "Assignment not found."}, status=404
        )
    return assignment, None


@login_required
@require_POST
def api_comm_work_type(request, pk):
    """Right-click menu: qualify the day as regular / sick / swap / overtime."""
    assignment, error = _assignment_or_error(pk)
    if error:
        return error
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}
    work_type = payload.get("work_type", "")
    valid = {code for code, _ in CommShiftAssignment.WORK_TYPE_CHOICES}
    if work_type not in valid:
        return JsonResponse({"ok": False, "error": "Unknown work type."}, status=400)
    assignment.work_type = work_type
    assignment.save(update_fields=["work_type"])
    return JsonResponse(
        {"ok": True, "work_type": work_type, "label": assignment.name_with_tag}
    )


@login_required
@require_POST
def api_comm_remove(request, pk):
    """Right-click menu: remove the assignment from the schedule."""
    assignment, error = _assignment_or_error(pk)
    if error:
        return error
    assignment.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def api_comm_move(request, pk):
    """Drag-and-drop: move an assignment to another day (same seat).

    If the target day already has that seat filled, the two assignments
    swap dates — matching how commercial schedulers resolve drops.
    """
    assignment, error = _assignment_or_error(pk)
    if error:
        return error
    try:
        payload = json.loads(request.body or "{}")
        target = dt.date.fromisoformat(payload.get("date", ""))
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid target date."}, status=400)

    if target == assignment.date:
        return JsonResponse({"ok": True, "result": "unchanged"})

    with transaction.atomic():
        occupant = (
            CommShiftAssignment.objects.select_for_update()
            .filter(date=target, seat=assignment.seat)
            .exclude(pk=assignment.pk)
            .first()
        )
        source_date = assignment.date
        if occupant:
            # Swap: park the occupant on a sentinel date to dodge the
            # (date, seat) unique constraint, then exchange.
            occupant.date = dt.date(1900, 1, 1)
            occupant.save(update_fields=["date"])
            assignment.date = target
            assignment.save(update_fields=["date"])
            occupant.date = source_date
            occupant.save(update_fields=["date"])
            return JsonResponse({"ok": True, "result": "swapped"})
        assignment.date = target
        assignment.save(update_fields=["date"])
    return JsonResponse({"ok": True, "result": "moved"})
