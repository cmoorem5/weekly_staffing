"""
iCalendar (.ics) feed of a user's comm and duty assignments.

Events use floating local times (no timezone suffix) — the whole operation
runs in one timezone, and floating times render correctly in Outlook,
Google Calendar, and Apple Calendar without a VTIMEZONE block. Duty days
are all-day events; comm seats carry their shift window (overnight seats
end the next morning).
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

from . import shifts
from .models import CommShiftAssignment, DutyAssignment

FEED_PAST_DAYS = 14
FEED_FUTURE_DAYS = 120

_SEAT_BY_CODE = {seat.code: seat for seat in shifts.COMM_SEATS}


def _escape(text: str) -> str:
    """Escape TEXT values per RFC 5545."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def parse_seat_window(
    time_str: str,
) -> tuple[dt.time, dt.time, bool] | None:
    """(start, end, ends_next_day) from a seat time like '0630–1830'."""
    cleaned = time_str.replace("–", "-").replace("—", "-").strip()
    parts = cleaned.split("-")
    if len(parts) != 2:
        return None
    try:
        start = dt.time(int(parts[0][:2]), int(parts[0][2:4]))
        end = dt.time(int(parts[1][:2]), int(parts[1][2:4]))
    except (ValueError, IndexError):
        return None
    return start, end, end <= start


def _dtstamp() -> str:
    return timezone.now().strftime("%Y%m%dT%H%M%SZ")


def _event(
    uid: str, summary: str, description: str, dt_lines: list[str], stamp: str
) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
    lines.extend(dt_lines)
    lines.append(f"SUMMARY:{_escape(summary)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    lines.append("END:VEVENT")
    return lines


def _comm_events(profile, start: dt.date, end: dt.date, stamp: str) -> list[str]:
    lines: list[str] = []
    assignments = CommShiftAssignment.objects.filter(
        member=profile, date__gte=start, date__lte=end
    )
    for a in assignments:
        seat = _SEAT_BY_CODE.get(a.seat)
        label = seat.label if seat else a.seat
        summary = f"Comm {label}"
        tag = a.WORK_TYPE_TAGS.get(a.work_type)
        if tag:
            summary += f" ({tag})"
        window = parse_seat_window(seat.time) if seat and seat.time else None
        if window:
            start_t, end_t, next_day = window
            end_date = a.date + dt.timedelta(days=1) if next_day else a.date
            dt_lines = [
                f"DTSTART:{a.date:%Y%m%d}T{start_t:%H%M}00",
                f"DTEND:{end_date:%Y%m%d}T{end_t:%H%M}00",
            ]
        else:
            dt_lines = [
                f"DTSTART;VALUE=DATE:{a.date:%Y%m%d}",
                f"DTEND;VALUE=DATE:{a.date + dt.timedelta(days=1):%Y%m%d}",
            ]
        lines.extend(
            _event(
                f"comm-{a.pk}@bmf-crew-hub",
                summary,
                a.note or "",
                dt_lines,
                stamp,
            )
        )
    return lines


def _duty_events(profile, start: dt.date, end: dt.date, stamp: str) -> list[str]:
    lines: list[str] = []
    assignments = DutyAssignment.objects.filter(
        officer=profile, date__gte=start, date__lte=end
    )
    for a in assignments:
        summary = f"Duty {shifts.DUTY_ROLE_LABELS.get(a.role, a.role)}"
        tag = a.WORK_TYPE_TAGS.get(a.work_type)
        if tag:
            summary += f" ({tag})"
        dt_lines = [
            f"DTSTART;VALUE=DATE:{a.date:%Y%m%d}",
            f"DTEND;VALUE=DATE:{a.date + dt.timedelta(days=1):%Y%m%d}",
        ]
        lines.extend(
            _event(
                f"duty-{a.pk}@bmf-crew-hub",
                summary,
                a.note or "",
                dt_lines,
                stamp,
            )
        )
    return lines


def build_user_calendar(user) -> str:
    """Complete VCALENDAR for the user's linked comm/duty profiles."""
    today = timezone.localdate()
    start = today - dt.timedelta(days=FEED_PAST_DAYS)
    end = today + dt.timedelta(days=FEED_FUTURE_DAYS)
    stamp = _dtstamp()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Boston MedFlight//Crew Hub//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:BMF Crew Hub — {_escape(user.get_username())}",
        "X-WR-CALDESC:Your Comm Center and duty officer schedule",
    ]
    comm_profile = getattr(user, "comm_profile", None)
    if comm_profile:
        lines.extend(_comm_events(comm_profile, start, end, stamp))
    duty_profile = getattr(user, "duty_profile", None)
    if duty_profile:
        lines.extend(_duty_events(duty_profile, start, end, stamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
