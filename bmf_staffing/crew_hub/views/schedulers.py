"""Comm Center and duty officer schedulers: month calendars + day editors.

Both schedulers are person-first: a day holds any number of people, and
each person's slot (Comm seat / duty role), work type, and paid hours are
attributes you edit on their row. Nothing forces a person into a distinct
slot just to get them onto the day — that is what ``_day_editor`` below
implements for both kinds from one code path.
"""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from .. import roles, shifts
from ..models import (
    VALID_WORK_TYPES,
    WORK_TYPE_CHOICES,
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
)
from .helpers import (
    PERM_DENIED_MSG,
    can_manage_schedules,
    can_manage_users,
    local_today,
    month_bounds,
    month_nav,
    month_weeks,
    parse_date_or_404,
    parse_month,
)
from .kinds import KINDS, slot_options, slot_sort_key

MAX_REPEAT_DAYS = 62  # Guardrail for "apply through" ranges.
MAX_ASSIGNMENT_HOURS = 24.0


def _repeat_dates(start: dt.date, repeat_until_raw: str) -> list[dt.date]:
    """Dates from start through the optional repeat-until value (inclusive)."""
    dates = [start]
    if repeat_until_raw:
        try:
            until = dt.date.fromisoformat(repeat_until_raw)
        except ValueError:
            return dates
        day = start
        while day < until and len(dates) < MAX_REPEAT_DAYS:
            day += dt.timedelta(days=1)
            dates.append(day)
    return dates


# --- Shared person-first day editor ------------------------------------


def _parse_hours(raw: str) -> float | None:
    """Read an hours box: blank means 'use the slot's standard hours'."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        hours = float(raw)
    except ValueError:
        return None
    if hours < 0 or hours > MAX_ASSIGNMENT_HOURS:
        return None
    return hours


def _clone_fields(cfg) -> tuple[str, ...]:
    return (
        cfg["slot_field"],
        f"{cfg['person_field']}_id",
        "display_name",
        "work_type",
        "hours",
        "note",
    )


def _save_day_rows(request, cfg, date: dt.date) -> int:
    """Apply the inline edits to every row on ``date``; returns rows removed."""
    model = cfg["assignment_model"]
    slot_field = cfg["slot_field"]
    person_field = cfg["person_field"]

    keep, remove_pks = [], []
    for assignment in model.objects.filter(date=date):
        if request.POST.get(f"remove_{assignment.pk}"):
            remove_pks.append(assignment.pk)
            continue
        slot = request.POST.get(f"slot_{assignment.pk}", "").strip()
        setattr(assignment, slot_field, slot if slot in cfg["slot_codes"] else "")
        work_type = request.POST.get(f"wt_{assignment.pk}", "").strip()
        assignment.work_type = (
            work_type if work_type in VALID_WORK_TYPES else model.WORK_REGULAR
        )
        assignment.hours = _parse_hours(request.POST.get(f"hours_{assignment.pk}", ""))
        assignment.note = request.POST.get(f"note_{assignment.pk}", "").strip()[:256]
        keep.append(assignment)

    # A roster person may hold a slot once per day (the DutyAssignment
    # unique constraint enforces it). Drop the edit rather than 500 if two
    # rows were pointed at the same slot for the same person.
    seen, saveable = set(), []
    for assignment in keep:
        person_id = getattr(assignment, f"{person_field}_id")
        key = (getattr(assignment, slot_field), person_id)
        if person_id is not None and key in seen:
            messages.warning(
                request,
                f"{assignment.name} was already in that "
                f"{cfg['slot_label'].lower()} — the duplicate row was left "
                "unchanged.",
            )
            continue
        seen.add(key)
        saveable.append(assignment)

    if remove_pks:
        model.objects.filter(pk__in=remove_pks).delete()
    if saveable:
        model.objects.bulk_update(saveable, [slot_field, "work_type", "hours", "note"])
    return len(remove_pks)


def _add_people(request, cfg, date: dt.date) -> int:
    """Put the checked roster people (and any typed name) on ``date``."""
    model = cfg["assignment_model"]
    person_field = cfg["person_field"]
    slot_field = cfg["slot_field"]

    taken = set(
        model.objects.filter(date=date).values_list(f"{person_field}_id", flat=True)
    )
    wanted = [pk for pk in request.POST.getlist("add_person") if pk.isdigit()]
    people = cfg["person_model"].objects.filter(pk__in=wanted, active=True)

    new_rows, already = [], []
    for person in people:
        if person.pk in taken:
            already.append(person.name)
            continue
        # Duty officers land in the role they are rostered for; Comm staff
        # start unassigned because seats are picked per day.
        default_slot = getattr(person, "role", "") if slot_field == "role" else ""
        new_rows.append(
            model(
                date=date,
                **{person_field: person, slot_field: default_slot},
            )
        )

    typed = request.POST.get("add_name", "").strip()
    if typed:
        new_rows.append(model(date=date, display_name=typed[:128]))

    if new_rows:
        model.objects.bulk_create(new_rows)
    if already:
        messages.info(
            request,
            f"{', '.join(already)} {'was' if len(already) == 1 else 'were'} "
            "already on this day.",
        )
    if not new_rows and not already:
        messages.error(
            request,
            "Pick at least one person to add, or type a name for someone "
            "who is not on the roster.",
        )
    return len(new_rows)


def _repeat_day(cfg, date: dt.date, targets: list[dt.date]) -> None:
    """Replace each target day with a copy of ``date``'s people."""
    model = cfg["assignment_model"]
    fields = _clone_fields(cfg)
    source = list(model.objects.filter(date=date))
    model.objects.filter(date__in=targets).delete()
    model.objects.bulk_create(
        [
            model(date=target, **{f: getattr(a, f) for f in fields})
            for target in targets
            for a in source
        ]
    )


@login_required
def _day_editor(request, kind: str, date_str: str):
    """Person-first day editor shared by both schedulers."""
    cfg = KINDS[kind]
    date = parse_date_or_404(date_str)
    model = cfg["assignment_model"]

    if request.method == "POST":
        if not can_manage_schedules(request.user):
            messages.error(request, PERM_DENIED_MSG)
            return redirect(cfg["day_url"], date_str=date_str)

        action = request.POST.get("action", "save")
        with transaction.atomic():
            removed = _save_day_rows(request, cfg, date)
            if action == "add":
                added = _add_people(request, cfg, date)
            else:
                added = 0
                targets = _repeat_dates(
                    date, request.POST.get("repeat_until", "").strip()
                )[1:]
                if targets:
                    _repeat_day(cfg, date, targets)
                    messages.success(
                        request,
                        f"Copied this day to {len(targets)} following day(s), "
                        f"through {targets[-1]}.",
                    )

        if action == "add":
            if added:
                messages.success(
                    request,
                    f"Added {added} {'person' if added == 1 else 'people'} to "
                    f"{date}. Pick their {cfg['slot_label'].lower()} and hours "
                    "below, then save.",
                )
            return redirect(cfg["day_url"], date_str=date_str)

        note = f" ({removed} removed)" if removed else ""
        messages.success(request, f"{cfg['title']} schedule saved for {date}{note}.")
        return redirect(f"{cfg['month_path']}?month={date.year:04d}-{date.month:02d}")

    assignments = sorted(
        model.objects.filter(date=date).select_related(cfg["person_field"]),
        key=slot_sort_key(kind),
    )
    on_day_ids = {
        getattr(a, f"{cfg['person_field']}_id")
        for a in assignments
        if getattr(a, f"{cfg['person_field']}_id")
    }
    slot_field = cfg["slot_field"]
    rows = [
        {
            "pk": a.pk,
            "name": a.name or "(no name)",
            "slot": getattr(a, slot_field),
            "unassigned": not getattr(a, slot_field),
            "work_type": a.work_type,
            "hours": a.hours,
            "default_hours": a.default_hours,
            "note": a.note,
            "roster_linked": bool(getattr(a, f"{cfg['person_field']}_id")),
        }
        for a in assignments
    ]
    return render(
        request,
        "crew_hub/schedule_day.html",
        {
            "kind": kind,
            "title": cfg["title"],
            "slot_label": cfg["slot_label"],
            "date": date,
            "rows": rows,
            "unassigned_count": sum(1 for r in rows if r["unassigned"]),
            "slot_options": slot_options(kind),
            "work_type_choices": WORK_TYPE_CHOICES,
            "available": [
                p
                for p in cfg["person_model"].objects.filter(active=True)
                if p.pk not in on_day_ids
            ],
            "month_url": cfg["month_url"],
            "roster_url": cfg["roster_url"],
            "day_url": cfg["day_url"],
            "can_manage": can_manage_schedules(request.user),
            "prev_day": date - dt.timedelta(days=1),
            "next_day": date + dt.timedelta(days=1),
        },
    )


# --- Comm Center -------------------------------------------------------


@login_required
def comm_month(request):
    year, month = parse_month(request)
    weeks = month_weeks(year, month)
    first, last = month_bounds(year, month)

    member_id = request.GET.get("member", "")
    assignments = CommShiftAssignment.objects.filter(
        date__gte=first, date__lte=last
    ).select_related("member")

    by_day: dict[dt.date, list[CommShiftAssignment]] = {}
    for a in assignments:
        by_day.setdefault(a.date, []).append(a)

    sort_key = slot_sort_key("comm")
    day_cells = {}
    for day, items in by_day.items():
        filled = sorted([a for a in items if a.name], key=sort_key)
        selected_pk = int(member_id) if member_id.isdigit() else None
        chips = [
            {
                "pk": a.pk,
                "seat": a.slot_label,
                "name": a.name,
                "work_type": a.work_type,
                "unassigned": not a.seat,
                "mine": bool(selected_pk and a.member_id == selected_pk),
            }
            for a in filled
        ]
        # Coverage counts seats that are actually covered — several people
        # may share one seat, and Extra/unassigned rows are not coverage.
        day_cells[day] = {
            "filled": len({a.seat for a in filled if a.seat and a.seat != "EXTRA"}),
            "chips": chips,
            "mine": any(chip["mine"] for chip in chips),
        }

    return render(
        request,
        "crew_hub/schedule_month.html",
        {
            "title": "Comm Center schedule",
            "kind": "comm",
            "day_url_name": "crew_hub:comm_day",
            "weeks": weeks,
            "cells": day_cells,
            "nav": month_nav(year, month),
            "today": local_today(),
            "seat_total": len([s for s in shifts.COMM_SEATS if s.code != "EXTRA"]),
            "slot_label": KINDS["comm"]["slot_label"],
            "slot_options": slot_options("comm"),
            "members": CommStaffMember.objects.filter(active=True),
            "selected_member": member_id,
        },
    )


def comm_day(request, date_str):
    return _day_editor(request, "comm", date_str)


def _linkable_users():
    from django.contrib.auth.models import User

    return User.objects.filter(is_active=True).order_by("username")


def _link_user(request, person) -> None:
    """Attach/detach a login to a roster person (action == 'link')."""
    from django.contrib.auth.models import User

    user_raw = request.POST.get("user", "").strip()
    if not user_raw:
        person.user = None
        person.save(update_fields=["user"])
        messages.success(request, f"Unlinked login from {person.name}.")
        return
    user = User.objects.filter(pk=user_raw if user_raw.isdigit() else None).first()
    if user is None:
        messages.error(request, "Unknown user.")
        return
    already = type(person).objects.filter(user=user).exclude(pk=person.pk).first()
    if already:
        messages.error(
            request, f"{user.get_username()} is already linked to {already.name}."
        )
        return
    person.user = user
    person.save(update_fields=["user"])
    messages.success(
        request,
        f"Linked {person.name} to login “{user.get_username()}” — they now "
        "get My Schedule, time-off requests, and notifications.",
    )


# Bulk-add role tokens people are likely to type (ITC and "blood" included).
DUTY_ROLE_ALIASES = {
    "AOC": "AOC",
    "AAOC": "AAOC",
    "MDOC": "MDOC",
    "PEDIDOC": "PEDIDOC",
    "PEDI": "PEDIDOC",
    "PEDS": "PEDIDOC",
    "ITOC": "ITOC",
    "ITC": "ITOC",
    "BPM": "BPM",
    "BLOOD": "BPM",
}
_BULK_SEPARATORS = ("—", "–", ",", ";", "\t", " - ")


def _parse_bulk_roster(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse 'Name, ROLE' lines into (name, role) pairs.

    Accepts comma / dash / semicolon / tab separators or a trailing role
    word ('Jane Smith AOC'). Lines whose role part isn't recognized keep
    the whole line as the name (no role) and are reported back.
    """
    people: list[tuple[str, str]] = []
    no_role: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().strip(",")
        if not line:
            continue
        name, role = line, ""
        for sep in _BULK_SEPARATORS:
            if sep in line:
                left, _, right = line.rpartition(sep)
                token = right.strip().upper().replace("-", "").replace(" ", "")
                if left.strip() and token in DUTY_ROLE_ALIASES:
                    name, role = left.strip(), DUTY_ROLE_ALIASES[token]
                else:
                    no_role.append(line)
                break
        else:
            words = line.split()
            token = words[-1].upper() if len(words) > 1 else ""
            if token in DUTY_ROLE_ALIASES:
                name, role = " ".join(words[:-1]), DUTY_ROLE_ALIASES[token]
        people.append((name, role))
    return people, no_role


def _bulk_add_officers(request) -> None:
    """Roster ``bulk`` action: add several duty officers with roles at once."""
    people, no_role = _parse_bulk_roster(request.POST.get("people", ""))
    if not people:
        messages.error(request, "Paste one person per line, e.g. “Jane Smith, AOC”.")
        return
    added = updated = skipped = 0
    for name, role in people:
        person, created = DutyOfficer.objects.get_or_create(
            name=name, defaults={"role": role}
        )
        if created:
            added += 1
        elif role and person.role != role:
            person.role = role
            person.save(update_fields=["role"])
            updated += 1
        else:
            skipped += 1
    summary = f"Bulk add: {added} added"
    if updated:
        summary += f", {updated} role(s) updated"
    if skipped:
        summary += f", {skipped} already on the roster"
    messages.success(request, summary + ".")
    if no_role:
        messages.warning(
            request,
            "No role recognized on: "
            + "; ".join(f"“{line}”" for line in no_role[:5])
            + " — added without a role (valid roles: AOC, AAOC, MDOC, "
            "PediDOC, ITOC/ITC, BPM/Blood).",
        )


def _add_person(request, model, roster_label: str) -> None:
    """Roster ``add`` action: create the person, optionally with a new login.

    Filling in the optional username creates a login and links it in one
    step. Levels above Member require ``manage_users`` (Admin) — otherwise
    the login is created as Member and a note says so.
    """
    name = request.POST.get("name", "").strip()
    if not name:
        return
    defaults = {}
    duty_role = request.POST.get("duty_role", "").strip()
    if model is DutyOfficer and duty_role in shifts.DUTY_ROLE_LABELS:
        defaults["role"] = duty_role
    person, created = model.objects.get_or_create(name=name, defaults=defaults)
    if not created:
        messages.info(request, f"{name} is already on the roster.")
        return
    messages.success(request, f"Added {name} to the {roster_label}.")

    username = request.POST.get("username", "").strip()
    if not username:
        return
    level = request.POST.get("level") or roles.LEVEL_MEMBER
    if level not in roles.VALID_LEVELS:
        level = roles.LEVEL_MEMBER
    if level != roles.LEVEL_MEMBER and not can_manage_users(request.user):
        level = roles.LEVEL_MEMBER
        messages.info(
            request,
            "Only an Admin can grant levels above Member — the login was "
            "created as Member.",
        )
    first, _, last = name.partition(" ")
    user, temp_password, error = roles.create_login(
        username,
        email=request.POST.get("email", ""),
        first_name=first,
        last_name=last,
        level=level,
    )
    if error:
        messages.error(
            request,
            f"{name} was added, but the login was not created: {error} "
            "You can link or create one later.",
        )
        return
    person.user = user
    person.save(update_fields=["user"])
    messages.success(
        request,
        f"Created login “{username}” ({roles.LEVEL_LABELS[level]}) for "
        f"{name}. Temporary password: {temp_password} — shown once; have "
        "them change it after signing in.",
    )


@login_required
def comm_staff(request):
    if request.method == "POST":
        if not can_manage_schedules(request.user):
            messages.error(request, PERM_DENIED_MSG)
            return redirect("crew_hub:comm_staff")
        if request.POST.get("action") == "link":
            member = CommStaffMember.objects.filter(
                pk=request.POST.get("pk") or None
            ).first()
            if member:
                _link_user(request, member)
            return redirect("crew_hub:comm_staff")
        action = request.POST.get("action", "add")
        if action == "add":
            _add_person(request, CommStaffMember, "Comm Center roster")
        elif action == "toggle":
            pk = request.POST.get("pk", "")
            member = CommStaffMember.objects.filter(pk=pk or None).first()
            if member:
                member.active = not member.active
                member.save(update_fields=["active"])
                state = "reactivated" if member.active else "deactivated"
                messages.success(request, f"{member.name} {state}.")
        return redirect("crew_hub:comm_staff")

    return render(
        request,
        "crew_hub/roster.html",
        {
            "title": "Comm Center roster",
            "people": CommStaffMember.objects.select_related("user"),
            "back_url_name": "crew_hub:comm_month",
            "back_label": "Comm Center schedule",
            "users": _linkable_users(),
            "can_manage": can_manage_schedules(request.user),
            "can_set_levels": can_manage_users(request.user),
            "level_choices": roles.LEVEL_CHOICES,
        },
    )


# --- Duty officers -----------------------------------------------------


@login_required
def duty_month(request):
    year, month = parse_month(request)
    weeks = month_weeks(year, month)
    first, last = month_bounds(year, month)

    assignments = DutyAssignment.objects.filter(
        date__gte=first, date__lte=last
    ).select_related("officer")
    by_day: dict[dt.date, list[DutyAssignment]] = {}
    for a in assignments:
        by_day.setdefault(a.date, []).append(a)

    sort_key = slot_sort_key("duty")
    day_cells = {}
    for day, items in by_day.items():
        filled = sorted([a for a in items if a.name], key=sort_key)
        day_cells[day] = {
            # Roles covered — several officers may share one (split MDOC).
            "filled": len({a.role for a in filled if a.role}),
            "chips": [
                {
                    "pk": a.pk,
                    "seat": a.slot_label,
                    "name": a.name,
                    "work_type": a.work_type,
                    "unassigned": not a.role,
                    "mine": False,
                }
                for a in filled
            ],
            "mine": False,
        }

    return render(
        request,
        "crew_hub/schedule_month.html",
        {
            "title": "Duty officer rotation",
            "kind": "duty",
            "day_url_name": "crew_hub:duty_day",
            "weeks": weeks,
            "cells": day_cells,
            "nav": month_nav(year, month),
            "today": local_today(),
            "seat_total": len(shifts.DUTY_ROLE_ORDER),
            "slot_label": KINDS["duty"]["slot_label"],
            "slot_options": slot_options("duty"),
            "members": None,
            "selected_member": "",
        },
    )


def duty_day(request, date_str):
    return _day_editor(request, "duty", date_str)


@login_required
def duty_roster(request):
    if request.method == "POST":
        if not can_manage_schedules(request.user):
            messages.error(request, PERM_DENIED_MSG)
            return redirect("crew_hub:duty_roster")
        if request.POST.get("action") == "link":
            officer = DutyOfficer.objects.filter(
                pk=request.POST.get("pk") or None
            ).first()
            if officer:
                _link_user(request, officer)
            return redirect("crew_hub:duty_roster")
        action = request.POST.get("action", "add")
        if action == "add":
            _add_person(request, DutyOfficer, "duty roster")
        elif action == "bulk":
            _bulk_add_officers(request)
        elif action == "set_role":
            officer = DutyOfficer.objects.filter(
                pk=request.POST.get("pk") or None
            ).first()
            duty_role = request.POST.get("duty_role", "").strip()
            if officer and (duty_role in shifts.DUTY_ROLE_LABELS or duty_role == ""):
                officer.role = duty_role
                officer.save(update_fields=["role"])
                messages.success(
                    request,
                    f"{officer.name} is now {officer.role_label or 'unassigned'}.",
                )
        elif action == "toggle":
            pk = request.POST.get("pk", "")
            officer = DutyOfficer.objects.filter(pk=pk or None).first()
            if officer:
                officer.active = not officer.active
                officer.save(update_fields=["active"])
                state = "reactivated" if officer.active else "deactivated"
                messages.success(request, f"{officer.name} {state}.")
        return redirect("crew_hub:duty_roster")

    return render(
        request,
        "crew_hub/roster.html",
        {
            "title": "Duty officer roster",
            "people": DutyOfficer.objects.select_related("user"),
            "back_url_name": "crew_hub:duty_month",
            "back_label": "Duty rotation",
            "users": _linkable_users(),
            "can_manage": can_manage_schedules(request.user),
            "can_set_levels": can_manage_users(request.user),
            "level_choices": roles.LEVEL_CHOICES,
            "duty_role_choices": shifts.DUTY_ROLE_CHOICES,
        },
    )
