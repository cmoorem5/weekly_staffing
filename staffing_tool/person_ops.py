"""
Person-level ops reporting queries (staff detail from schedule imports).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import distinct, func, or_

from .db import session_scope
from .models import StaffRosterEntry, WeeklyPersonShift
from .person_names import (
    is_plausible_person_display,
    normalize_legacy_person_display,
    person_sort_key,
)
from .staff_roster import canonical_display, roster_entry_for_display


@dataclass
class PersonOpsSummary:
    person_display: str
    date_start: str
    date_end: str
    staffed_count: int = 0
    rw_count: int = 0
    gr_count: int = 0
    rw_pct: float | None = None
    ot_count: int = 0
    leave_counts: dict[str, int] = field(default_factory=dict)
    leave_total: int = 0


@dataclass
class PersonOpsRow:
    shift_date: str
    week_start: str
    role: str
    event_type: str
    base_name: str
    service_type: str
    day_night: str
    unit_code: str
    leave_type: str | None
    overtime: bool
    raw_value: str
    source_tab: str
    source_cell: str


def _person_display_labels_for_query(
    db_path: str | None,
    person_display: str,
    date_start: date,
    date_end: date,
    *,
    staff_member_id: int | None = None,
) -> list[str]:
    """DB labels that map to the selected clean person name (includes legacy)."""
    selected = (person_display or "").strip()
    if not selected:
        return []
    start_s = date_start.isoformat()
    end_s = date_end.isoformat()
    with session_scope(db_path) as session:
        raw_rows = (
            session.query(distinct(WeeklyPersonShift.person_display))
            .filter(
                WeeklyPersonShift.shift_date >= start_s,
                WeeklyPersonShift.shift_date <= end_s,
            )
            .all()
        )
    labels: set[str] = {selected}
    for (raw,) in raw_rows:
        label = (raw or "").strip()
        if not label:
            continue
        if label == selected:
            labels.add(label)
            continue
        normalized = normalize_legacy_person_display(label)
        if normalized and normalized == selected:
            labels.add(label)
    if staff_member_id is not None:
        with session_scope(db_path) as session:
            id_rows = (
                session.query(distinct(WeeklyPersonShift.person_display))
                .filter(
                    WeeklyPersonShift.staff_member_id == staff_member_id,
                    WeeklyPersonShift.shift_date >= start_s,
                    WeeklyPersonShift.shift_date <= end_s,
                )
                .all()
            )
        for (raw,) in id_rows:
            label = (raw or "").strip()
            if label:
                labels.add(label)
    return sorted(labels)


def _resolve_staff_member_id(
    db_path: str | None,
    person_display: str,
    *,
    role: str | None = None,
) -> int | None:
    with session_scope(db_path) as session:
        entry = roster_entry_for_display(session, person_display, role=role)
        return entry.id if entry else None


def list_staff_roster_persons(
    db_path: str | None,
    *,
    role: str | None = None,
) -> list[tuple[str, str]]:
    """Active roster members as (canonical display, role) sorted Last, First."""
    with session_scope(db_path) as session:
        q = session.query(StaffRosterEntry).filter(StaffRosterEntry.active == 1)
        if role:
            q = q.filter(StaffRosterEntry.role == role)
        rows = q.order_by(
            StaffRosterEntry.role,
            StaffRosterEntry.last_name,
            StaffRosterEntry.first_name,
        ).all()
    result = [(canonical_display(r), r.role) for r in rows if canonical_display(r)]
    return sorted(result, key=lambda pair: (person_sort_key(pair[0]), pair[1]))


def list_distinct_persons(
    db_path: str | None,
    date_start: date,
    date_end: date,
) -> list[str]:
    """Distinct person_display values with shifts in the date range."""
    start_s = date_start.isoformat()
    end_s = date_end.isoformat()
    with session_scope(db_path) as session:
        rows = (
            session.query(distinct(WeeklyPersonShift.person_display))
            .filter(
                WeeklyPersonShift.shift_date >= start_s,
                WeeklyPersonShift.shift_date <= end_s,
                WeeklyPersonShift.event_type != "skipped",
            )
            .all()
        )
    seen: set[str] = set()
    names: list[str] = []
    for (raw,) in rows:
        label = (raw or "").strip()
        if not label:
            continue
        if is_plausible_person_display(label):
            clean = label
        else:
            clean = normalize_legacy_person_display(label)
            if not clean:
                continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(clean)
    return sorted(names, key=person_sort_key)


def load_person_ops_detail(
    db_path: str | None,
    person_display: str,
    date_start: date,
    date_end: date,
    *,
    role: str | None = None,
) -> list[PersonOpsRow]:
    """All shift rows for one person in the date range."""
    start_s = date_start.isoformat()
    end_s = date_end.isoformat()
    person = (person_display or "").strip()
    if not person:
        return []
    staff_member_id = _resolve_staff_member_id(db_path, person, role=role)
    match_labels = _person_display_labels_for_query(
        db_path,
        person,
        date_start,
        date_end,
        staff_member_id=staff_member_id,
    )
    with session_scope(db_path) as session:
        q = session.query(WeeklyPersonShift).filter(
            WeeklyPersonShift.shift_date >= start_s,
            WeeklyPersonShift.shift_date <= end_s,
            WeeklyPersonShift.event_type != "skipped",
        )
        if staff_member_id is not None:
            q = q.filter(
                or_(
                    WeeklyPersonShift.staff_member_id == staff_member_id,
                    WeeklyPersonShift.person_display.in_(match_labels),
                )
            )
        else:
            q = q.filter(WeeklyPersonShift.person_display.in_(match_labels))
        if role:
            q = q.filter(WeeklyPersonShift.role == role)
        raw = q.order_by(
            WeeklyPersonShift.shift_date,
            WeeklyPersonShift.event_type,
            WeeklyPersonShift.role,
            WeeklyPersonShift.unit_code,
        ).all()
    return [
        PersonOpsRow(
            shift_date=m.shift_date,
            week_start=m.week_start,
            role=m.role,
            event_type=m.event_type,
            base_name=m.base_name or "",
            service_type=m.service_type or "",
            day_night=m.day_night or "",
            unit_code=m.unit_code or "",
            leave_type=m.leave_type,
            overtime=bool(m.overtime),
            raw_value=m.raw_value or "",
            source_tab=m.source_tab or "",
            source_cell=m.source_cell or "",
        )
        for m in raw
    ]


def load_person_ops_summary(
    db_path: str | None,
    person_display: str,
    date_start: date,
    date_end: date,
    *,
    role: str | None = None,
) -> PersonOpsSummary:
    """Summary metrics for one person over a date range."""
    person = (person_display or "").strip()
    start_s = date_start.isoformat()
    end_s = date_end.isoformat()
    summary = PersonOpsSummary(
        person_display=person,
        date_start=start_s,
        date_end=end_s,
    )
    if not person:
        return summary

    rows = load_person_ops_detail(
        db_path, person, date_start, date_end, role=role
    )
    leave_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        if row.event_type == "leave":
            lt = (row.leave_type or row.raw_value or "Other").strip() or "Other"
            leave_counts[lt] += 1
            summary.leave_total += 1
        elif row.event_type == "ot":
            summary.ot_count += 1
            summary.staffed_count += 1
            if row.service_type == "RW":
                summary.rw_count += 1
            elif row.service_type == "GR":
                summary.gr_count += 1
        elif row.event_type == "staffed":
            summary.staffed_count += 1
            if row.service_type == "RW":
                summary.rw_count += 1
            elif row.service_type == "GR":
                summary.gr_count += 1

    rw_gr_total = summary.rw_count + summary.gr_count
    if rw_gr_total > 0:
        summary.rw_pct = round(100.0 * summary.rw_count / rw_gr_total, 1)
    summary.leave_counts = dict(sorted(leave_counts.items()))
    return summary
