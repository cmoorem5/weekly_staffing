"""Comm Center and duty officer schedulers: month calendars + day editors."""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from .. import shifts
from ..models import (
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
)
from .helpers import local_today, month_nav, month_weeks, parse_date_or_404, parse_month

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
    first = dt.date(year, month, 1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)

    member_id = request.GET.get("member", "")
    assignments = CommShiftAssignment.objects.filter(
        date__gte=first, date__lte=last
    ).select_related("member")

    by_day: dict[dt.date, list[CommShiftAssignment]] = {}
    for a in assignments:
        by_day.setdefault(a.date, []).append(a)

    day_cells = {}
    for day, items in by_day.items():
        filled = [a for a in items if a.name]
        mine = member_id and any(
            a.member_id == int(member_id) for a in filled if a.member_id
        )
        day_cells[day] = {
            "filled": len([a for a in filled if a.seat != "EXTRA"]),
            "names": [f"{a.get_seat_display()}: {a.name}" for a in filled],
            "mine": bool(mine),
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
        dates = _repeat_dates(date, request.POST.get("repeat_until", "").strip())
        with transaction.atomic():
            for target in dates:
                for seat in shifts.COMM_SEATS:
                    member_raw = request.POST.get(f"member_{seat.code}", "").strip()
                    name_raw = request.POST.get(f"name_{seat.code}", "").strip()
                    note = request.POST.get(f"note_{seat.code}", "").strip()
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
            "prev_day": date - dt.timedelta(days=1),
            "next_day": date + dt.timedelta(days=1),
        },
    )


@login_required
def comm_staff(request):
    if request.method == "POST":
        action = request.POST.get("action", "add")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if name:
                _, created = CommStaffMember.objects.get_or_create(name=name)
                if created:
                    messages.success(
                        request, f"Added {name} to the Comm Center roster."
                    )
                else:
                    messages.info(request, f"{name} is already on the roster.")
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
            "people": CommStaffMember.objects.all(),
            "back_url_name": "crew_hub:comm_month",
            "back_label": "Comm Center schedule",
        },
    )


# --- Duty officers -----------------------------------------------------


@login_required
def duty_month(request):
    year, month = parse_month(request)
    weeks = month_weeks(year, month)
    first = dt.date(year, month, 1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)

    assignments = DutyAssignment.objects.filter(
        date__gte=first, date__lte=last
    ).select_related("officer")
    by_day: dict[dt.date, list[DutyAssignment]] = {}
    for a in assignments:
        by_day.setdefault(a.date, []).append(a)

    day_cells = {}
    for day, items in by_day.items():
        filled = [a for a in items if a.name]
        roles = {a.role for a in filled}
        day_cells[day] = {
            "filled": len(roles),
            "names": [f"{shifts.DUTY_ROLE_LABELS[a.role]}: {a.name}" for a in filled],
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
        dates = _repeat_dates(date, request.POST.get("repeat_until", "").strip())
        with transaction.atomic():
            for target in dates:
                for role in shifts.DUTY_ROLE_ORDER:
                    DutyAssignment.objects.filter(date=target, role=role).delete()
                    officer_raw = request.POST.get(f"officer_{role}", "").strip()
                    second_raw = request.POST.get(f"second_{role}", "").strip()
                    if officer_raw.isdigit():
                        officer = next(
                            (o for o in officers if o.pk == int(officer_raw)), None
                        )
                        if officer:
                            DutyAssignment.objects.create(
                                date=target, role=role, officer=officer
                            )
                    if second_raw:
                        DutyAssignment.objects.create(
                            date=target, role=role, display_name=second_raw
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
            }
        )

    return render(
        request,
        "crew_hub/duty_day.html",
        {
            "date": date,
            "rows": rows,
            "officers": officers,
            "prev_day": date - dt.timedelta(days=1),
            "next_day": date + dt.timedelta(days=1),
        },
    )


@login_required
def duty_roster(request):
    if request.method == "POST":
        action = request.POST.get("action", "add")
        if action == "add":
            name = request.POST.get("name", "").strip()
            if name:
                _, created = DutyOfficer.objects.get_or_create(name=name)
                if created:
                    messages.success(request, f"Added {name} to the duty roster.")
                else:
                    messages.info(request, f"{name} is already on the roster.")
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
            "people": DutyOfficer.objects.all(),
            "back_url_name": "crew_hub:duty_month",
            "back_label": "Duty rotation",
        },
    )
