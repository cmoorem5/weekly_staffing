"""Comm Center and duty officer schedulers: month calendars + day editors."""

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

MAX_REPEAT_DAYS = 62  # Guardrail for "apply through" ranges.


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

    seat_order = {seat.code: i for i, seat in enumerate(shifts.COMM_SEATS)}
    day_cells = {}
    for day, items in by_day.items():
        filled = [a for a in items if a.name]
        filled.sort(key=lambda a: seat_order.get(a.seat, 99))
        selected_pk = int(member_id) if member_id.isdigit() else None
        chips = [
            {
                "pk": a.pk,
                "seat": a.get_seat_display(),
                "name": a.name,
                "work_type": a.work_type,
                "mine": bool(selected_pk and a.member_id == selected_pk),
            }
            for a in filled
        ]
        day_cells[day] = {
            "filled": len([a for a in filled if a.seat != "EXTRA"]),
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
            "members": CommStaffMember.objects.filter(active=True),
            "selected_member": member_id,
        },
    )


@login_required
def comm_day(request, date_str):
    date = parse_date_or_404(date_str)
    members = list(CommStaffMember.objects.filter(active=True))

    if request.method == "POST":
        if not can_manage_schedules(request.user):
            messages.error(request, PERM_DENIED_MSG)
            return redirect("crew_hub:comm_day", date_str=date_str)
        dates = _repeat_dates(date, request.POST.get("repeat_until", "").strip())
        valid_work_types = VALID_WORK_TYPES
        with transaction.atomic():
            for target in dates:
                for seat in shifts.COMM_SEATS:
                    member_raw = request.POST.get(f"member_{seat.code}", "").strip()
                    name_raw = request.POST.get(f"name_{seat.code}", "").strip()
                    note = request.POST.get(f"note_{seat.code}", "").strip()
                    work_type = request.POST.get(f"wt_{seat.code}", "").strip()
                    if work_type not in valid_work_types:
                        work_type = CommShiftAssignment.WORK_REGULAR
                    member = None
                    if member_raw.isdigit():
                        member = next(
                            (m for m in members if m.pk == int(member_raw)), None
                        )
                    if member is None and not name_raw:
                        CommShiftAssignment.objects.filter(
                            date=target, seat=seat.code
                        ).delete()
                        continue
                    CommShiftAssignment.objects.update_or_create(
                        date=target,
                        seat=seat.code,
                        defaults={
                            "member": member,
                            "display_name": name_raw if member is None else "",
                            "note": note,
                            "work_type": work_type,
                        },
                    )
        messages.success(
            request,
            f"Comm Center schedule saved for {len(dates)} day(s) starting {date}.",
        )
        month_param = f"?month={date.year:04d}-{date.month:02d}"
        return redirect(f"{'/hub/comm/'}{month_param}")

    existing = {
        a.seat: a
        for a in CommShiftAssignment.objects.filter(date=date).select_related("member")
    }
    rows = [
        {"seat": seat, "assignment": existing.get(seat.code)}
        for seat in shifts.COMM_SEATS
    ]
    return render(
        request,
        "crew_hub/comm_day.html",
        {
            "date": date,
            "rows": rows,
            "members": members,
            "work_type_choices": WORK_TYPE_CHOICES,
            "prev_day": date - dt.timedelta(days=1),
            "next_day": date + dt.timedelta(days=1),
        },
    )


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


def _add_person(request, model, roster_label: str) -> None:
    """Roster ``add`` action: create the person, optionally with a new login.

    Filling in the optional username creates a login and links it in one
    step. Levels above Member require ``manage_users`` (Admin) — otherwise
    the login is created as Member and a note says so.
    """
    name = request.POST.get("name", "").strip()
    if not name:
        return
    person, created = model.objects.get_or_create(name=name)
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

    role_order = {role: i for i, role in enumerate(shifts.DUTY_ROLE_ORDER)}
    day_cells = {}
    for day, items in by_day.items():
        filled = [a for a in items if a.name]
        filled.sort(key=lambda a: (role_order.get(a.role, 99), a.pk))
        roles = {a.role for a in filled}
        day_cells[day] = {
            "filled": len(roles),
            "chips": [
                {
                    "pk": a.pk,
                    "seat": shifts.DUTY_ROLE_LABELS[a.role],
                    "name": a.name,
                    "work_type": a.work_type,
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
            "members": None,
            "selected_member": "",
        },
    )


@login_required
def duty_day(request, date_str):
    date = parse_date_or_404(date_str)
    officers = list(DutyOfficer.objects.filter(active=True))

    if request.method == "POST":
        if not can_manage_schedules(request.user):
            messages.error(request, PERM_DENIED_MSG)
            return redirect("crew_hub:duty_day", date_str=date_str)
        dates = _repeat_dates(date, request.POST.get("repeat_until", "").strip())
        valid_work_types = VALID_WORK_TYPES
        with transaction.atomic():
            for target in dates:
                for role in shifts.DUTY_ROLE_ORDER:
                    DutyAssignment.objects.filter(date=target, role=role).delete()
                    officer_raw = request.POST.get(f"officer_{role}", "").strip()
                    second_raw = request.POST.get(f"second_{role}", "").strip()
                    work_type = request.POST.get(f"wt_{role}", "").strip()
                    if work_type not in valid_work_types:
                        work_type = DutyAssignment.WORK_REGULAR
                    if officer_raw.isdigit():
                        officer = next(
                            (o for o in officers if o.pk == int(officer_raw)), None
                        )
                        if officer:
                            DutyAssignment.objects.create(
                                date=target,
                                role=role,
                                officer=officer,
                                work_type=work_type,
                            )
                    if second_raw:
                        DutyAssignment.objects.create(
                            date=target,
                            role=role,
                            display_name=second_raw,
                            work_type=work_type,
                        )
        messages.success(
            request,
            f"Duty rotation saved for {len(dates)} day(s) starting {date}.",
        )
        return redirect(f"/hub/duty/?month={date.year:04d}-{date.month:02d}")

    existing: dict[str, list[DutyAssignment]] = {}
    for a in DutyAssignment.objects.filter(date=date).select_related("officer"):
        existing.setdefault(a.role, []).append(a)

    rows = []
    for code, label in shifts.DUTY_ROLE_CHOICES:
        items = existing.get(code, [])
        primary = next((a for a in items if a.officer_id), None)
        second = next((a for a in items if not a.officer_id and a.display_name), None)
        rows.append(
            {
                "role": code,
                "label": label,
                "primary_id": primary.officer_id if primary else None,
                "second_name": second.display_name if second else "",
                "work_type": (primary or second).work_type
                if (primary or second)
                else DutyAssignment.WORK_REGULAR,
            }
        )

    return render(
        request,
        "crew_hub/duty_day.html",
        {
            "date": date,
            "rows": rows,
            "officers": officers,
            "work_type_choices": WORK_TYPE_CHOICES,
            "prev_day": date - dt.timedelta(days=1),
            "next_day": date + dt.timedelta(days=1),
        },
    )


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
        },
    )
