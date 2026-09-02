"""
Roster-wide staff exception summary: shifts, OT, and leave/exception counts
per person, across active clinical staff and managers.

Builds on the per-person granular data already captured by the schedule
import pipeline (``WeeklyPersonShift`` for RN/Medic/EMT, ``WeeklyManagerShift``
for managers) — this module aggregates it across the whole roster instead of
one person at a time (see ``person_ops.py`` for the single-person report).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .db import session_scope
from .manager_names import canonical_manager_name, roster_upper_from_session_or_default
from .models import StaffRosterEntry, WeeklyManagerShift, WeeklyPersonShift
from .person_names import normalize_legacy_person_display, person_sort_key
from .staff_roster import canonical_display

# Leave codes folded to the same display buckets used by the weekly/quarterly
# board reports' exception breakdown (aggregate_week_from_records), so this
# report's columns read the same way as those.
_LEAVE_DISPLAY = {
    "AT": "AT",
    "LT": "LT",
    "LT-D": "LT",
    "LT-N": "LT",
    "SICK": "SICK",
    "LOA": "LOA",
    "PFML": "LOA",
    "JURY": "JURY",
    "BREV": "BREV",
    "BERV": "BREV",
    "BEREAVEMENT": "BREV",
}
LEAVE_DISPLAY_COLUMNS = ("AT", "LT", "SICK", "LOA", "JURY", "BREV")


@dataclass
class StaffExceptionSummary:
    person_display: str
    role: str
    is_manager: bool = False
    staffed_count: int = 0
    ot_count: int = 0
    leave_counts: dict[str, int] = field(default_factory=dict)
    leave_total: int = 0
    # Non-leave "skipped" exceptions (AOC/CLINICAL/FLOAT/LTM/MIL for clinical
    # staff; AOC admin days for managers) — collapsed into one bucket rather
    # than broken out, matching how the import pipeline already groups them.
    admin_other_count: int = 0

    @property
    def total_exceptions(self) -> int:
        return self.leave_total + self.admin_other_count


def _leave_display(raw: str | None) -> str:
    key = (raw or "").strip().upper()
    return _LEAVE_DISPLAY.get(key, key or "OTHER")


def load_clinical_exception_summary(
    db_path: str | None,
    date_start: date,
    date_end: date,
    *,
    role: str | None = None,
) -> list[StaffExceptionSummary]:
    """One row per active RN/Medic/EMT roster member with shift/OT/leave totals."""
    start_s = date_start.isoformat()
    end_s = date_end.isoformat()

    with session_scope(db_path) as session:
        roster_q = session.query(StaffRosterEntry).filter(StaffRosterEntry.active == 1)
        if role:
            roster_q = roster_q.filter(StaffRosterEntry.role == role)
        roster_rows = roster_q.all()

        by_id: dict[int, StaffExceptionSummary] = {}
        by_display_lower: dict[str, int] = {}
        for entry in roster_rows:
            display = canonical_display(entry)
            if not display:
                continue
            by_id[entry.id] = StaffExceptionSummary(
                person_display=display, role=entry.role
            )
            by_display_lower[display.lower()] = entry.id

        shift_q = session.query(WeeklyPersonShift).filter(
            WeeklyPersonShift.shift_date >= start_s,
            WeeklyPersonShift.shift_date <= end_s,
            WeeklyPersonShift.is_manager_row == 0,
        )
        if role:
            shift_q = shift_q.filter(WeeklyPersonShift.role == role)
        shift_rows = shift_q.all()

    for row in shift_rows:
        key_id = row.staff_member_id if row.staff_member_id in by_id else None
        if key_id is None:
            label = (row.person_display or "").strip()
            clean = (
                label
                if label.lower() in by_display_lower
                else (normalize_legacy_person_display(label) or "")
            )
            key_id = by_display_lower.get(clean.lower())
        if key_id is None:
            continue
        summary = by_id[key_id]
        if row.event_type == "leave":
            lt = _leave_display(row.leave_type or row.raw_value)
            summary.leave_counts[lt] = summary.leave_counts.get(lt, 0) + 1
            summary.leave_total += 1
        elif row.event_type == "ot":
            summary.ot_count += 1
            summary.staffed_count += 1
        elif row.event_type == "staffed":
            summary.staffed_count += 1
        elif row.event_type == "skipped" and row.skip_reason == "admin":
            summary.admin_other_count += 1

    return sorted(
        by_id.values(), key=lambda s: (s.role, person_sort_key(s.person_display))
    )


def load_manager_exception_summary(
    db_path: str | None,
    date_start: date,
    date_end: date,
) -> list[StaffExceptionSummary]:
    """One row per manager with line-shift/OT/AOC totals.

    Manager schedule rows (``WeeklyManagerShift``) don't carry a leave-type
    breakdown the way clinical ``WeeklyPersonShift`` rows do, so this is
    aggregate-only: total line shifts, OT shifts, and AOC admin days.
    """
    start_s = date_start.isoformat()
    end_s = date_end.isoformat()

    with session_scope(db_path) as session:
        roster_upper = roster_upper_from_session_or_default(session)
        rows = (
            session.query(WeeklyManagerShift)
            .filter(
                WeeklyManagerShift.shift_date >= start_s,
                WeeklyManagerShift.shift_date <= end_s,
            )
            .all()
        )

    by_name: dict[str, StaffExceptionSummary] = {}
    for m in rows:
        raw_name = (m.person_display or "").strip() or "(unknown)"
        name = canonical_manager_name(raw_name, roster_upper)
        summary = by_name.setdefault(
            name,
            StaffExceptionSummary(person_display=name, role="MANAGER", is_manager=True),
        )
        if m.event_type == "aoc":
            summary.admin_other_count += 1
            continue
        summary.staffed_count += 1
        if m.overtime:
            summary.ot_count += 1

    return sorted(by_name.values(), key=lambda s: person_sort_key(s.person_display))
