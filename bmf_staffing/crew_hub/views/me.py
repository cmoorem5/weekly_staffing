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
            rows.append(
                {
                    "date": a.date,
                    "what": f"Comm {shifts.comm_seat_label(a.seat)}",
                    "time": shifts.comm_seat_time(a.seat),
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
                    "what": f"Duty {shifts.duty_role_label(a.role)}",
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


def _conflicts_for_many(requests: list[TimeOffRequest]) -> dict[int, list[str]]:
    """Map request pk -> scheduled days inside its window, in two queries.

    The review queue shows every pending request at once, so the
    assignments are fetched for all of them in one pass per scheduler and
    matched up in Python rather than querying per request.
    """
    conflicts: dict[int, list[str]] = {r.pk: [] for r in requests}
    if not requests:
        return conflicts

    by_comm: dict[int, list[TimeOffRequest]] = {}
    by_duty: dict[int, list[TimeOffRequest]] = {}
    for r in requests:
        comm = getattr(r.user, "comm_profile", None)
        if comm:
            by_comm.setdefault(comm.pk, []).append(r)
        duty = getattr(r.user, "duty_profile", None)
        if duty:
            by_duty.setdefault(duty.pk, []).append(r)

    window_start = min(r.start_date for r in requests)
    window_end = max(r.end_date for r in requests)

    def collect(assignments, owner_field, owners_by_person, label):
        """One query's worth of assignments, matched to the requests they hit."""
        for a in assignments:
            for r in owners_by_person.get(getattr(a, owner_field), ()):
                if r.start_date <= a.date <= r.end_date:
                    conflicts[r.pk].append(f"{a.date:%a %b} {a.date.day}: {label(a)}")

    if by_comm:
        collect(
            CommShiftAssignment.objects.filter(
                member_id__in=by_comm, date__gte=window_start, date__lte=window_end
            ),
            "member_id",
            by_comm,
            lambda a: f"Comm {shifts.comm_seat_label(a.seat)}",
        )
    if by_duty:
        collect(
            DutyAssignment.objects.filter(
                officer_id__in=by_duty, date__gte=window_start, date__lte=window_end
            ),
            "officer_id",
            by_duty,
            lambda a: f"Duty {shifts.duty_role_label(a.role)}",
        )
    return conflicts


def _conflicts_for(time_off: TimeOffRequest) -> list[str]:
    """Existing assignments inside one request's window (to fix by hand)."""
    return _conflicts_for_many([time_off])[time_off.pk]


@login_required
def time_off_manage(request):
    if not can_review_time_off(request.user):
        messages.error(request, REVIEW_DENIED_MSG)
        return redirect("crew_hub:my_schedule")

    pending_requests = list(
        TimeOffRequest.objects.filter(
            status=TimeOffRequest.STATUS_PENDING
        ).select_related("user", "user__comm_profile", "user__duty_profile")
    )
    conflicts = _conflicts_for_many(pending_requests)
    pending = [{"req": r, "conflicts": conflicts[r.pk]} for r in pending_requests]
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
