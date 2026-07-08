"""Rotation management and calendar interaction APIs (comm + duty).

CrewSense-style behavior shared by both schedulers: rotations are
repeating patterns that materialize into assignments, right-click sets
the work type (sick / swap / overtime), and drag-and-drop moves or swaps
assignments on the month calendar.
"""

from __future__ import annotations

import datetime as dt
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .. import shifts
from ..models import (
    VALID_WORK_TYPES,
    CommRotation,
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
    DutyRotation,
)
from ..notify import assignment_owner, notify
from ..services import apply_duty_rotations_for_range, apply_rotations_for_range
from .helpers import PERM_DENIED_MSG, can_manage_schedules, month_bounds


def _notify_owner(request, assignment, message: str) -> None:
    """Tell the linked login about a change a manager made to their day."""
    owner = assignment_owner(assignment)
    if owner is not None and owner.pk != request.user.pk:
        notify(owner, message, url=reverse("crew_hub:my_schedule"))


WEEKDAY_OPTIONS = [
    (6, "Sun"),
    (0, "Mon"),
    (1, "Tue"),
    (2, "Wed"),
    (3, "Thu"),
    (4, "Fri"),
    (5, "Sat"),
]

# Everything that differs between the two schedulers, in one place.
ROTATION_KINDS = {
    "comm": {
        "rotation_model": CommRotation,
        "person_model": CommStaffMember,
        "person_field": "member",
        "slot_field": "seat",
        "slots": [
            (seat.code, f"{seat.label}{f' ({seat.time})' if seat.time else ''}")
            for seat in shifts.COMM_SEATS
        ],
        "slot_codes": set(shifts.COMM_SEAT_INDEX),
        "slot_label": "Seat",
        "title": "Comm Center rotations",
        "person_label": "Staff member",
        "manage_url": "crew_hub:comm_rotations",
        "month_path": "/hub/comm/",
        "apply_range": apply_rotations_for_range,
    },
    "duty": {
        "rotation_model": DutyRotation,
        "person_model": DutyOfficer,
        "person_field": "officer",
        "slot_field": "role",
        "slots": list(shifts.DUTY_ROLE_CHOICES),
        "slot_codes": set(shifts.DUTY_ROLE_LABELS),
        "slot_label": "Role",
        "title": "Duty officer rotations",
        "person_label": "Duty officer",
        "manage_url": "crew_hub:duty_rotations",
        "month_path": "/hub/duty/",
        "apply_range": apply_duty_rotations_for_range,
    },
}


def _rotation_manage(request, kind: str):
    cfg = ROTATION_KINDS[kind]
    model = cfg["rotation_model"]

    if request.method == "POST":
        if not can_manage_schedules(request.user):
            messages.error(request, PERM_DENIED_MSG)
            return redirect(cfg["manage_url"])
        action = request.POST.get("action", "add")
        if action == "add":
            _add_rotation(request, cfg)
        elif action in ("toggle", "delete"):
            rotation = model.objects.filter(pk=request.POST.get("pk") or None).first()
            if rotation:
                person = getattr(rotation, cfg["person_field"])
                if action == "toggle":
                    rotation.active = not rotation.active
                    rotation.save(update_fields=["active"])
                    state = "resumed" if rotation.active else "paused"
                    messages.success(request, f"Rotation for {person.name} {state}.")
                else:
                    rotation.delete()
                    messages.success(
                        request,
                        f"Rotation for {person.name} deleted. Days already on "
                        "the calendar are kept.",
                    )
        return redirect(cfg["manage_url"])

    return render(
        request,
        "crew_hub/rotation_manage.html",
        {
            "kind": kind,
            "title": cfg["title"],
            "person_label": cfg["person_label"],
            "slot_label": cfg["slot_label"],
            "slots": cfg["slots"],
            "rotations": model.objects.select_related(cfg["person_field"]),
            "people": cfg["person_model"].objects.filter(active=True),
            "weekday_options": WEEKDAY_OPTIONS,
            "manage_url": cfg["manage_url"],
            "month_path": cfg["month_path"],
            "person_field": cfg["person_field"],
        },
    )


def _add_rotation(request, cfg) -> None:
    person = (
        cfg["person_model"]
        .objects.filter(pk=request.POST.get("person") or None)
        .first()
    )
    slot = request.POST.get("slot", "")
    if not person or slot not in cfg["slot_codes"]:
        messages.error(
            request,
            f"Pick a {cfg['person_label'].lower()} and a {cfg['slot_label'].lower()}.",
        )
        return
    try:
        anchor = dt.date.fromisoformat(request.POST.get("anchor_date", ""))
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

    model = cfg["rotation_model"]
    pattern_type = request.POST.get("pattern_type", model.PATTERN_CYCLE)
    if pattern_type not in dict(model.PATTERN_CHOICES):
        messages.error(request, "Unknown pattern type.")
        return
    weekdays = ",".join(
        str(d) for d, _ in WEEKDAY_OPTIONS if f"weekday_{d}" in request.POST
    )

    def _int(name: str, default: int) -> int:
        try:
            return max(0, int(request.POST.get(name, default)))
        except (TypeError, ValueError):
            return default

    rotation = model(
        pattern_type=pattern_type,
        days_on=_int("days_on", 4),
        days_off=_int("days_off", 4),
        weekdays=weekdays if pattern_type == model.PATTERN_WEEKLY else "",
        anchor_date=anchor,
        end_date=end_date,
        note=request.POST.get("note", "").strip(),
        **{cfg["person_field"]: person, cfg["slot_field"]: slot},
    )
    if pattern_type == model.PATTERN_CYCLE and rotation.days_on == 0:
        messages.error(request, "Days on must be at least 1 for a cycle pattern.")
        return
    if pattern_type == model.PATTERN_WEEKLY and not rotation.weekday_set:
        messages.error(request, "Pick at least one weekday for a weekly pattern.")
        return
    rotation.save()
    messages.success(
        request,
        f"Rotation added: {person.name} ({rotation.pattern_label}). Use "
        "“Apply rotations” on the month view to fill the calendar.",
    )
    person_role = getattr(person, "role", "")
    if cfg["slot_field"] == "role" and person_role and slot != person_role:
        messages.warning(
            request,
            f"Heads up: {person.name} is rostered as "
            f"{shifts.DUTY_ROLE_LABELS.get(person_role, person_role)} but this "
            f"rotation covers {shifts.DUTY_ROLE_LABELS.get(slot, slot)}.",
        )
    if cfg["slot_field"] == "seat":
        _warn_seat_conflicts(request, model, rotation, cfg)


# How far ahead to scan for overlapping same-seat rotations (about 2 months
# — long enough to catch nearly any cycle/weekly overlap without walking
# an unbounded date range).
SEAT_CONFLICT_LOOKAHEAD_DAYS = 60


def _warn_seat_conflicts(request, model, rotation, cfg) -> None:
    """Warn if another active rotation already claims this seat on the same
    days — only one person can hold a seat per day, so whichever rotation
    is applied second gets silently skipped."""
    others = model.objects.filter(seat=rotation.seat, active=True).exclude(
        pk=rotation.pk
    )
    if not others:
        return
    window_start = rotation.anchor_date
    conflicting = set()
    for other in others:
        for offset in range(SEAT_CONFLICT_LOOKAHEAD_DAYS):
            day = window_start + dt.timedelta(days=offset)
            if rotation.works_on(day) and other.works_on(day):
                conflicting.add(getattr(other, cfg["person_field"]).name)
                break
    if conflicting:
        messages.warning(
            request,
            f"Heads up: {', '.join(sorted(conflicting))} also "
            f"{'has' if len(conflicting) == 1 else 'have'} an active rotation "
            f"in the {rotation.get_seat_display()} seat that overlaps with "
            f"this one. Only one person can hold a seat per day, so whichever "
            "rotation you apply first wins and the other gets skipped as "
            "“already assigned.” Give each person a different seat, "
            "or pause/delete the one that shouldn't apply.",
        )


def _rotation_apply(request, kind: str):
    cfg = ROTATION_KINDS[kind]
    if not can_manage_schedules(request.user):
        messages.error(request, PERM_DENIED_MSG)
        return redirect(cfg["month_path"])
    raw = request.POST.get("month", "")
    try:
        year_s, month_s = raw.split("-")
        first = dt.date(int(year_s), int(month_s), 1)
    except ValueError:
        messages.error(request, "Invalid month.")
        return redirect(cfg["month_path"])
    _, last = month_bounds(first.year, first.month)

    created, skipped = cfg["apply_range"](first, last)
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
    return redirect(f"{cfg['month_path']}?month={first.year:04d}-{first.month:02d}")


@login_required
def comm_rotations(request):
    return _rotation_manage(request, "comm")


@login_required
@require_POST
def comm_rotations_apply(request):
    return _rotation_apply(request, "comm")


@login_required
def duty_rotations(request):
    return _rotation_manage(request, "duty")


@login_required
@require_POST
def duty_rotations_apply(request):
    return _rotation_apply(request, "duty")


# --- Calendar chip APIs (work type / move / remove) --------------------


def _get_or_404_json(model, pk, request=None):
    if request is not None and not can_manage_schedules(request.user):
        return None, JsonResponse(
            {"ok": False, "error": "Schedule-manager access required."}, status=403
        )
    assignment = model.objects.filter(pk=pk).first()
    if assignment is None:
        return None, JsonResponse(
            {"ok": False, "error": "Assignment not found."}, status=404
        )
    return assignment, None


def _set_work_type(request, model, pk):
    assignment, error = _get_or_404_json(model, pk, request)
    if error:
        return error
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}
    work_type = payload.get("work_type", "")
    if work_type not in VALID_WORK_TYPES:
        return JsonResponse({"ok": False, "error": "Unknown work type."}, status=400)
    assignment.work_type = work_type
    assignment.save(update_fields=["work_type"])
    _notify_owner(
        request,
        assignment,
        f"Your {assignment.date} shift was marked "
        f"{assignment.get_work_type_display()} by {request.user.get_username()}.",
    )
    return JsonResponse(
        {"ok": True, "work_type": work_type, "label": assignment.name_with_tag}
    )


def _remove(request, model, pk):
    assignment, error = _get_or_404_json(model, pk, request)
    if error:
        return error
    _notify_owner(
        request,
        assignment,
        f"Your {assignment.date} shift was removed from the schedule "
        f"by {request.user.get_username()}.",
    )
    assignment.delete()
    return JsonResponse({"ok": True})


def _move(request, model, pk, slot_field: str):
    """Move an assignment to another day (same seat/role); swap if occupied."""
    assignment, error = _get_or_404_json(model, pk, request)
    if error:
        return error
    try:
        payload = json.loads(request.body or "{}")
        target = dt.date.fromisoformat(payload.get("date", ""))
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid target date."}, status=400)

    if target == assignment.date:
        return JsonResponse({"ok": True, "result": "unchanged"})

    slot_value = getattr(assignment, slot_field)
    with transaction.atomic():
        occupants = list(
            model.objects.select_for_update()
            .filter(date=target, **{slot_field: slot_value})
            .exclude(pk=assignment.pk)
        )
        if len(occupants) > 1:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "That day has multiple people in this slot — "
                    "edit it from the day editor instead.",
                },
                status=409,
            )
        source_date = assignment.date
        if occupants:
            occupant = occupants[0]
            # Park the occupant on a sentinel date to dodge unique
            # constraints, then exchange the two days.
            occupant.date = dt.date(1900, 1, 1)
            occupant.save(update_fields=["date"])
            assignment.date = target
            assignment.save(update_fields=["date"])
            occupant.date = source_date
            occupant.save(update_fields=["date"])
            actor = request.user.get_username()
            _notify_owner(
                request,
                assignment,
                f"Your shift moved from {source_date} to {target} "
                f"(swap made by {actor}).",
            )
            _notify_owner(
                request,
                occupant,
                f"Your shift moved from {target} to {source_date} "
                f"(swap made by {actor}).",
            )
            return JsonResponse({"ok": True, "result": "swapped"})
        assignment.date = target
        assignment.save(update_fields=["date"])
        _notify_owner(
            request,
            assignment,
            f"Your shift moved from {source_date} to {target} "
            f"by {request.user.get_username()}.",
        )
    return JsonResponse({"ok": True, "result": "moved"})


@login_required
@require_POST
def api_comm_work_type(request, pk):
    return _set_work_type(request, CommShiftAssignment, pk)


@login_required
@require_POST
def api_comm_remove(request, pk):
    return _remove(request, CommShiftAssignment, pk)


@login_required
@require_POST
def api_comm_move(request, pk):
    return _move(request, CommShiftAssignment, pk, "seat")


@login_required
@require_POST
def api_duty_work_type(request, pk):
    return _set_work_type(request, DutyAssignment, pk)


@login_required
@require_POST
def api_duty_remove(request, pk):
    return _remove(request, DutyAssignment, pk)


@login_required
@require_POST
def api_duty_move(request, pk):
    return _move(request, DutyAssignment, pk, "role")


# Placeholder seat value while dodging the (date, seat) unique constraint
# during a same-day seat swap. Never persisted outside one transaction.
_RESEAT_SENTINEL = "_TMP_"


@login_required
@require_POST
def api_comm_reseat(request, pk):
    """Change a Comm Center assignment's seat for the same day (swap if taken)."""
    assignment, error = _get_or_404_json(CommShiftAssignment, pk, request)
    if error:
        return error
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}
    new_seat = payload.get("seat", "")
    if new_seat not in shifts.COMM_SEAT_INDEX:
        return JsonResponse({"ok": False, "error": "Unknown seat."}, status=400)
    old_seat = assignment.seat
    if new_seat == old_seat:
        return JsonResponse({"ok": True, "result": "unchanged"})

    date = assignment.date
    old_label = shifts.COMM_SEAT_INDEX[old_seat].label
    new_label = shifts.COMM_SEAT_INDEX[new_seat].label
    actor = request.user.get_username()
    with transaction.atomic():
        occupant = (
            CommShiftAssignment.objects.select_for_update()
            .filter(date=date, seat=new_seat)
            .exclude(pk=assignment.pk)
            .first()
        )
        if occupant:
            occupant.seat = _RESEAT_SENTINEL
            occupant.save(update_fields=["seat"])
            assignment.seat = new_seat
            assignment.save(update_fields=["seat"])
            occupant.seat = old_seat
            occupant.save(update_fields=["seat"])
            _notify_owner(
                request,
                assignment,
                f"Your {date} shift moved from {old_label} to {new_label} "
                f"(swap made by {actor}).",
            )
            _notify_owner(
                request,
                occupant,
                f"Your {date} shift moved from {new_label} to {old_label} "
                f"(swap made by {actor}).",
            )
            return JsonResponse({"ok": True, "result": "swapped"})
        assignment.seat = new_seat
        assignment.save(update_fields=["seat"])
        _notify_owner(
            request,
            assignment,
            f"Your {date} shift moved from {old_label} to {new_label} by {actor}.",
        )
    return JsonResponse({"ok": True, "result": "moved"})
