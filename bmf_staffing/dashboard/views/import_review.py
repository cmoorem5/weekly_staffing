"""Post-import review queue.

One place to clean up everything a schedule import couldn't resolve on its
own: parse issues for the week, unknown unit codes (with one-click alias
creation), and person names that didn't match the staff roster (with fuzzy
suggestions and one-click linking).
"""

from __future__ import annotations

import difflib
import re
from datetime import UTC, datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from staffing_tool.db import session_scope
from staffing_tool.models import (
    STAFF_ROSTER_ROLES,
    ScheduleImport,
    ScheduleParseIssue,
    StaffRosterEntry,
    WeeklyPersonShift,
)
from staffing_tool.schedule_import import UNIT_MAP
from staffing_tool.staff_roster import (
    canonical_display,
    match_parsed_person_to_roster,
    staff_roster_index_from_session,
)
from staffing_tool.unit_mappings import save_unit_mappings

from .helpers import DB_PATH, _ensure_db

_BASE_CODE_RE = re.compile(r"\(base '([^']+)'\)")


def _split_display(display: str) -> tuple[str, str]:
    """('Last', 'First') from 'Last, First' (first may be empty)."""
    last, _, first = display.partition(",")
    return last.strip(), first.strip()


def _issue_unit_code(issue) -> str:
    """The unmapped unit code an 'unknown_unit' issue refers to."""
    match = _BASE_CODE_RE.search(str(issue.message or ""))
    return match.group(1) if match else str(issue.raw_value or "").strip()


def _unlinked_names(session, week_start: str):
    """Unmatched (person_display, role) groups for the week, with suggestions."""
    from sqlalchemy import func

    rows = (
        session.query(
            WeeklyPersonShift.person_display,
            WeeklyPersonShift.role,
            func.count(),
        )
        .filter(
            WeeklyPersonShift.week_start == week_start,
            WeeklyPersonShift.staff_member_id.is_(None),
            WeeklyPersonShift.included_in_aggregates == 1,
            WeeklyPersonShift.event_type != "skipped",
            WeeklyPersonShift.person_display != "",
        )
        .group_by(WeeklyPersonShift.person_display, WeeklyPersonShift.role)
        .order_by(func.count().desc())
        .all()
    )
    if not rows:
        return []

    index = staff_roster_index_from_session(session)
    entries_by_role: dict[str, list[StaffRosterEntry]] = {
        r: [] for r in STAFF_ROSTER_ROLES
    }
    for entry in (
        session.query(StaffRosterEntry)
        .filter(StaffRosterEntry.active == 1)
        .order_by(StaffRosterEntry.last_name, StaffRosterEntry.first_name)
        .all()
    ):
        if entry.role in entries_by_role:
            entries_by_role[entry.role].append(entry)

    result = []
    for display, role, count in rows:
        candidates = entries_by_role.get(str(role), [])
        suggestion_id = None
        matched = match_parsed_person_to_roster(str(display), str(role), index)
        if matched is not None:
            suggestion_id = matched.id
        elif candidates:
            names = {canonical_display(e): e.id for e in candidates}
            close = difflib.get_close_matches(
                str(display), list(names), n=1, cutoff=0.6
            )
            if close:
                suggestion_id = names[close[0]]
        result.append(
            {
                "display": str(display),
                "role": str(role),
                "count": int(count),
                "suggestion_id": suggestion_id,
                "options": [
                    {"id": e.id, "label": canonical_display(e)} for e in candidates
                ],
            }
        )
    return result


def _post_map_unit(request, session) -> None:
    raw_code = (request.POST.get("raw_code") or "").strip().upper()
    maps_to = (request.POST.get("maps_to") or "").strip().upper()
    if not raw_code or maps_to not in UNIT_MAP:
        messages.error(
            request, f"Pick a valid canonical unit for “{raw_code}” — see the list."
        )
        return
    save_unit_mappings(session, {raw_code: maps_to}, source="import-review")
    messages.success(
        request,
        f"Mapping saved: {raw_code} → {maps_to}. Re-import the week "
        "(Import schedule) to apply it to the stored data.",
    )


def _post_link_name(request, session) -> None:
    display = (request.POST.get("display") or "").strip()
    role = (request.POST.get("role") or "").strip()
    entry_id_raw = (request.POST.get("entry_id") or "").strip()

    if request.POST.get("action") == "add_link":
        last, first = _split_display(display)
        if not last:
            messages.error(request, "Can't parse a last name from that label.")
            return
        entry = StaffRosterEntry(
            last_name=last,
            first_name=first,
            role=role,
            active=1,
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            notes="Added from import review",
        )
        session.add(entry)
        session.flush()
        entry_id = entry.id
        verb = f"Added {canonical_display(entry)} to the roster and linked"
    else:
        entry = (
            session.query(StaffRosterEntry)
            .filter(StaffRosterEntry.id == (entry_id_raw or None))
            .first()
        )
        if entry is None:
            messages.error(request, "Pick a roster entry to link to.")
            return
        entry_id = entry.id
        verb = f"Linked to {canonical_display(entry)}:"

    updated = (
        session.query(WeeklyPersonShift)
        .filter(
            WeeklyPersonShift.person_display == display,
            WeeklyPersonShift.role == role,
            WeeklyPersonShift.staff_member_id.is_(None),
        )
        .update({"staff_member_id": entry_id}, synchronize_session=False)
    )
    messages.success(
        request,
        f"{verb} “{display}” ({role}) — {updated} shift row(s) updated "
        "across all imported weeks.",
    )


def import_review(request):
    _ensure_db()
    if not DB_PATH:
        messages.error(request, "Database is not configured (STAFFING_DB_PATH).")
        return redirect("home")

    week = (request.GET.get("week") or request.POST.get("week") or "").strip()

    with session_scope(DB_PATH) as session:
        if request.method == "POST":
            action = request.POST.get("action", "")
            if action == "map_unit":
                _post_map_unit(request, session)
            elif action in ("link_name", "add_link"):
                _post_link_name(request, session)
            session.commit()
            url = reverse("import_review")
            return redirect(f"{url}?week={week}" if week else url)

        weeks = [
            str(r[0])
            for r in session.query(ScheduleImport.week_start)
            .order_by(ScheduleImport.week_start.desc())
            .limit(26)
            .all()
        ]
        if not weeks:
            return render(request, "dashboard/import_review.html", {"weeks": []})
        if week not in weeks:
            week = weeks[0]

        issues = (
            session.query(ScheduleParseIssue)
            .filter(ScheduleParseIssue.week_start == week)
            .order_by(ScheduleParseIssue.issue_type, ScheduleParseIssue.sheet)
            .all()
        )
        unknown_units: dict[str, dict] = {}
        other_issues = []
        for issue in issues:
            if str(issue.issue_type) == "unknown_unit":
                code = _issue_unit_code(issue)
                entry = unknown_units.setdefault(
                    code, {"code": code, "count": 0, "example": ""}
                )
                entry["count"] += 1
                entry["example"] = f"{issue.sheet}!{issue.cell}"
            else:
                other_issues.append(issue)

        unlinked = _unlinked_names(session, week)

        context = {
            "weeks": weeks,
            "week": week,
            "unknown_units": sorted(unknown_units.values(), key=lambda u: -u["count"]),
            "canonical_units": sorted(UNIT_MAP),
            "unlinked": unlinked,
            "other_issues": other_issues,
            "todo_count": len(unknown_units) + len(unlinked),
        }
    return render(request, "dashboard/import_review.html", context)
