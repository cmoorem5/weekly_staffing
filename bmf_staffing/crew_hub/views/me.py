"""Self-service views: my schedule, time-off requests, notifications."""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import shifts
from ..ical import build_user_calendar
from ..models import (
    CalendarFeedToken,
    CommShiftAssignment,
    DutyAssignment,
    Notification,
    TimeOffRequest,
)
from ..notify import notify, notify_managers
from .helpers import REVIEW_DENIED_MSG, can_review_time_off, local_today

UPCOMING_DAYS = 42  # My-schedule lookahead window.
MAX_TIME_OFF_DAYS = 92


def _upcoming_assignments(user, start: dt.date, end: dt.date) -> list[dict]:
    """The user's comm + duty assignments between start and end, merged."""
    rows: list[dict] = []
    comm_profile = getattr(user, "comm_profile", None)
    if comm_profile:
        for a in CommShiftAssignment.objects.filter(
            member=comm_profile, date__gte=start, date__lte=end
        ):
            seat = shifts.COMM_SEAT_INDEX[a.seat]
            rows.append(
                {
                    "date": a.date,
                    "what": f"Comm {seat.label}",
                    "time": seat.time,
                    "work_type": a.get_work_type_display(),
                    "work_type_code": a.work_type,
                    "note": a.note,
                }
            )
    duty_profile = getattr(user, "duty_profile", None)
    if duty_profile:
        for a in DutyAssignment.objects.filter(
            officer=duty_profile, date__gte=start, date__lte=end
        ):
            rows.append(
                {
                    "date": a.date,
                    "what": f"Duty {shifts.DUTY_ROLE_LABELS[a.role]}",
                    "time": "all day",
                    "work_type": a.get_work_type_display(),
                    "work_type_code": a.work_type,
                    "note": a.note,
                }
            )
    rows.sort(key=lambda r: r["date"])
    return rows


@login_required
def my_schedule(request):
    today = local_today()
    end = today + dt.timedelta(days=UPCOMING_DAYS)
    assignments = _upcoming_assignments(request.user, today, end)
    comm_profile = getattr(request.user, "comm_profile", None)
    duty_profile = getattr(request.user, "duty_profile", None)
    feed_token = CalendarFeedToken.for_user(request.user)
    feed_url = request.build_absolute_uri(
        reverse("crew_hub:calendar_feed", kwargs={"token": feed_token.token})
    )
    return render(
        request,
        "crew_hub/my_schedule.html",
        {
            "assignments": assignments,
            "today": today,
            "end": end,
            "comm_profile": comm_profile,
            "duty_profile": duty_profile,
            "linked": bool(comm_profile or duty_profile),
            "my_requests": TimeOffRequest.objects.filter(user=request.user)[:20],
            "feed_url": feed_url,
        },
    )


def calendar_feed(request, token):
    """Personal .ics feed — token-authenticated so calendar apps can pull it."""
    feed = CalendarFeedToken.objects.filter(token=token).select_related("user").first()
    if feed is None or not feed.user.is_active:
        raise Http404("Unknown calendar feed.")
    response = HttpResponse(
        build_user_calendar(feed.user),
        content_type="text/calendar; charset=utf-8",
    )
    response["Content-Disposition"] = 'inline; filename="crew-hub-schedule.ics"'
    return response


@login_required
@require_POST
def calendar_feed_reset(request):
    """Rotate the feed token (invalidates any previously shared link)."""
    CalendarFeedToken.for_user(request.user).rotate()
    messages.success(
        request,
        "Calendar link reset. Re-subscribe with the new link below — the old "
        "one no longer works.",
    )
    return redirect("crew_hub:my_schedule")


@login_required
@require_POST
def time_off_submit(request):
    try:
        start = dt.date.fromisoformat(request.POST.get("start_date", ""))
        end = dt.date.fromisoformat(request.POST.get("end_date", ""))
    except ValueError:
        messages.error(request, "Both dates are required (YYYY-MM-DD).")
        return redirect("crew_hub:my_schedule")
    if end < start:
        start, end = end, start
    if (end - start).days > MAX_TIME_OFF_DAYS:
        messages.error(request, f"Requests are limited to {MAX_TIME_OFF_DAYS} days.")
        return redirect("crew_hub:my_schedule")

    time_off = TimeOffRequest.objects.create(
        user=request.user,
        start_date=start,
        end_date=end,
        reason=request.POST.get("reason", "").strip(),
    )
    notified = notify_managers(
        f"Time-off request: {request.user.get_username()} "
        f"{start:%b} {start.day} – {end:%b} {end.day} "
        f"({time_off.reason or 'no reason given'})",
        url=reverse("crew_hub:time_off_manage"),
        exclude=request.user,
    )
    messages.success(
        request,
        f"Time-off request submitted for {start} to {end}. "
        f"{notified} manager(s) notified.",
    )
    return redirect("crew_hub:my_schedule")


def _conflicts_for(time_off: TimeOffRequest) -> list[str]:
    """Existing assignments inside the requested window (to fix by hand)."""
    conflicts = []
    comm_profile = getattr(time_off.user, "comm_profile", None)
    if comm_profile:
        for a in CommShiftAssignment.objects.filter(
            member=comm_profile,
            date__gte=time_off.start_date,
            date__lte=time_off.end_date,
        ):
            conflicts.append(
                f"{a.date:%a %b} {a.date.day}: Comm {a.get_seat_display()}"
            )
    duty_profile = getattr(time_off.user, "duty_profile", None)
    if duty_profile:
        for a in DutyAssignment.objects.filter(
            officer=duty_profile,
            date__gte=time_off.start_date,
            date__lte=time_off.end_date,
        ):
            conflicts.append(
                f"{a.date:%a %b} {a.date.day}: Duty {shifts.DUTY_ROLE_LABELS[a.role]}"
            )
    return conflicts


@login_required
def time_off_manage(request):
    if not can_review_time_off(request.user):
        messages.error(request, REVIEW_DENIED_MSG)
        return redirect("crew_hub:my_schedule")

    pending = [
        {"req": r, "conflicts": _conflicts_for(r)}
        for r in TimeOffRequest.objects.filter(
            status=TimeOffRequest.STATUS_PENDING
        ).select_related("user")
    ]
    decided = TimeOffRequest.objects.exclude(
        status=TimeOffRequest.STATUS_PENDING
    ).select_related("user", "decided_by")[:25]
    return render(
        request,
        "crew_hub/time_off_manage.html",
        {"pending": pending, "decided": decided},
    )


@login_required
@require_POST
def time_off_decide(request, pk):
    if not can_review_time_off(request.user):
        messages.error(request, REVIEW_DENIED_MSG)
        return redirect("crew_hub:my_schedule")

    time_off = get_object_or_404(TimeOffRequest, pk=pk)
    if not time_off.is_pending:
        messages.info(request, "That request was already decided.")
        return redirect("crew_hub:time_off_manage")

    decision = request.POST.get("decision", "")
    if decision not in (TimeOffRequest.STATUS_APPROVED, TimeOffRequest.STATUS_DENIED):
        messages.error(request, "Pick approve or deny.")
        return redirect("crew_hub:time_off_manage")

    time_off.status = decision
    time_off.decided_by = request.user
    time_off.decided_at = timezone.now()
    time_off.manager_note = request.POST.get("manager_note", "").strip()
    time_off.save(update_fields=["status", "decided_by", "decided_at", "manager_note"])

    verdict = "approved" if decision == TimeOffRequest.STATUS_APPROVED else "denied"
    note = f" — {time_off.manager_note}" if time_off.manager_note else ""
    notify(
        time_off.user,
        f"Your time off {time_off.start_date:%b} {time_off.start_date.day} – "
        f"{time_off.end_date:%b} {time_off.end_date.day} was {verdict}{note}.",
        url=reverse("crew_hub:my_schedule"),
    )

    conflicts = _conflicts_for(time_off)
    if decision == TimeOffRequest.STATUS_APPROVED and conflicts:
        messages.warning(
            request,
            f"Approved, but {len(conflicts)} scheduled day(s) fall inside the "
            "window — adjust the calendars: " + "; ".join(conflicts[:6]),
        )
    else:
        messages.success(request, f"Request {verdict}; the requester was notified.")
    return redirect("crew_hub:time_off_manage")


@login_required
def notifications(request):
    items = Notification.objects.filter(user=request.user)[:50]
    return render(request, "crew_hub/notifications.html", {"items": items})


@login_required
@require_POST
def notifications_read(request):
    Notification.objects.filter(user=request.user, read=False).update(read=True)
    messages.success(request, "All notifications marked as read.")
    return redirect("crew_hub:notifications")
