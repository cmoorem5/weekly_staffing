"""
Staff roster (RN / Medic / EMT) for schedule import matching and ops reports.

Rows are stored in staffing.db and edited via the dashboard settings page.
New names from schedule import are added automatically on each apply-import pass.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from .models import (
    STAFF_ROSTER_ROLES,
    ScheduleImport,
    StaffRosterEntry,
    WeeklyPersonShift,
)
from .person_names import (
    is_likely_person_name,
    is_plausible_person_display,
    normalize_legacy_person_display,
    person_sort_key,
)


def canonical_display(entry: StaffRosterEntry) -> str:
    """Canonical ``Last, First`` or ``Last`` label for UI and imports."""
    last = (entry.last_name or "").strip()
    first = (entry.first_name or "").strip()
    if not last:
        return ""
    if first:
        return f"{last}, {first}"
    return last


def _title_name_part(word: str) -> str:
    s = (word or "").strip()
    if not s:
        return ""
    return s[:1].upper() + s[1:].lower()


def normalize_roster_last_name(name: str) -> str:
    return _title_name_part((name or "").strip())


def normalize_roster_first_name(name: str) -> str:
    return _title_name_part((name or "").strip())


def _parsed_last_first(parsed_display: str) -> tuple[str, str]:
    """Split a parsed schedule label into (last, first) tokens."""
    s = (parsed_display or "").strip()
    if not s:
        return "", ""
    if "," in s:
        last, first = (p.strip() for p in s.split(",", 1))
        return last, first
    parts = s.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _name_tokens(value: str) -> frozenset[str]:
    tokens: set[str] = set()
    for part in value.replace(",", " ").split():
        t = part.strip(".,").upper()
        if t:
            tokens.add(t)
    return frozenset(tokens)


@dataclass
class StaffRosterMatchIndex:
    """Active roster entries indexed for schedule import matching."""

    by_role_last: dict[tuple[str, str], list[StaffRosterEntry]] = field(
        default_factory=dict
    )
    entries: list[StaffRosterEntry] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.entries


def staff_roster_index_from_session(session: Session) -> StaffRosterMatchIndex:
    """Load active roster entries into a match index."""
    rows = (
        session.query(StaffRosterEntry)
        .filter(StaffRosterEntry.active == 1)
        .order_by(StaffRosterEntry.role, StaffRosterEntry.last_name)
        .all()
    )
    index = StaffRosterMatchIndex(entries=list(rows))
    for entry in rows:
        key = (entry.role, (entry.last_name or "").strip().upper())
        index.by_role_last.setdefault(key, []).append(entry)
    return index


def list_active_roster_entries(
    session: Session,
    *,
    role: str | None = None,
) -> list[StaffRosterEntry]:
    """Active roster rows, optionally filtered by role."""
    q = session.query(StaffRosterEntry).filter(StaffRosterEntry.active == 1)
    if role:
        q = q.filter(StaffRosterEntry.role == role)
    return q.order_by(
        StaffRosterEntry.role,
        StaffRosterEntry.last_name,
        StaffRosterEntry.first_name,
    ).all()


def match_parsed_person_to_roster(
    parsed_display: str,
    role: str,
    index: StaffRosterMatchIndex,
) -> StaffRosterEntry | None:
    """
    Match a parsed schedule person label to an active roster entry.

    Uses last-name token overlap (same approach as manager roster) and
    prefers first-name match when multiple roster rows share a last name.
    """
    if index.is_empty():
        return None
    role_key = (role or "").strip().upper()
    if role_key not in STAFF_ROSTER_ROLES:
        return None

    parsed = (parsed_display or "").strip()
    if not parsed:
        return None

    last, first = _parsed_last_first(parsed)
    last_upper = last.strip().upper()
    if not last_upper:
        return None

    candidates = index.by_role_last.get((role_key, last_upper), [])
    if not candidates:
        parsed_tokens = _name_tokens(parsed)
        for (r, lk), group in index.by_role_last.items():
            if r != role_key:
                continue
            for entry in group:
                entry_tokens = _name_tokens(
                    f"{entry.last_name} {entry.first_name or ''}"
                )
                if entry_tokens & parsed_tokens:
                    candidates.append(entry)
        if not candidates:
            return None

    if len(candidates) == 1:
        return candidates[0]

    first_upper = first.strip().upper()
    if first_upper:
        for entry in candidates:
            ef = (entry.first_name or "").strip().upper()
            if ef and ef == first_upper:
                return entry
            if ef and first_upper.startswith(ef):
                return entry
            if ef and ef.startswith(first_upper):
                return entry

    without_first = [e for e in candidates if not (e.first_name or "").strip()]
    if len(without_first) == 1:
        return without_first[0]

    return candidates[0]


@dataclass
class RosterImportSuggestion:
    """Person seen on imported shifts who is not yet on the staff roster."""

    role: str
    last_name: str
    first_name: str
    display: str
    shift_count: int = 0

    @property
    def form_key(self) -> str:
        return f"{self.role}|{self.last_name}|{self.first_name}"


def _roster_name_key(
    role: str, last_name: str, first_name: str
) -> tuple[str, str, str]:
    return (
        (role or "").strip().upper(),
        (last_name or "").strip().upper(),
        (first_name or "").strip().upper(),
    )


def _existing_roster_keys(session: Session) -> set[tuple[str, str, str]]:
    """All roster rows (active or inactive) for duplicate detection."""
    keys: set[tuple[str, str, str]] = set()
    for row in session.query(StaffRosterEntry).all():
        keys.add(_roster_name_key(row.role, row.last_name, row.first_name))
    return keys


def list_roster_import_weeks(session: Session) -> list[str]:
    """Sunday week_start values with person-shift imports, newest first."""
    from_imports = [
        r[0]
        for r in session.query(ScheduleImport.week_start)
        .order_by(ScheduleImport.week_start.desc())
        .all()
    ]
    if from_imports:
        return from_imports
    return [
        r[0]
        for r in session.query(distinct(WeeklyPersonShift.week_start))
        .order_by(WeeklyPersonShift.week_start.desc())
        .all()
    ]


def _clean_shift_person_label(raw: str) -> str | None:
    label = (raw or "").strip()
    if not label:
        return None
    if is_plausible_person_display(label):
        clean = label
    else:
        clean = normalize_legacy_person_display(label)
    if not clean or not is_likely_person_name(clean):
        return None
    return clean


def _dedupe_labels_by_last(labels: list[str]) -> list[str]:
    """Prefer ``Last, First`` over last-only when both appear for one last name."""
    by_last: dict[str, str] = {}
    for label in labels:
        last, _first = _parsed_last_first(label)
        lk = last.strip().lower()
        if not lk:
            continue
        existing = by_last.get(lk)
        if existing is None:
            by_last[lk] = label
            continue
        if "," in label and "," not in existing:
            by_last[lk] = label
    return sorted(by_last.values(), key=person_sort_key)


def suggest_roster_imports(
    session: Session,
    week_start: str | None = None,
) -> list[RosterImportSuggestion]:
    """
    Names from ``weekly_person_shifts`` for one week that are not on the roster.

    When ``week_start`` is omitted, uses the newest week with shift rows.
    """
    weeks = list_roster_import_weeks(session)
    if not weeks:
        return []
    target_week = week_start or weeks[0]
    if target_week not in weeks:
        return []

    rows = (
        session.query(
            WeeklyPersonShift.role,
            WeeklyPersonShift.person_display,
            func.count(WeeklyPersonShift.id),
        )
        .filter(
            WeeklyPersonShift.week_start == target_week,
            WeeklyPersonShift.is_manager_row == 0,
            WeeklyPersonShift.person_display != "",
            WeeklyPersonShift.event_type != "skipped",
        )
        .group_by(WeeklyPersonShift.role, WeeklyPersonShift.person_display)
        .all()
    )

    existing = _existing_roster_keys(session)
    by_role_labels: dict[str, list[str]] = {}
    shift_counts: dict[tuple[str, str], int] = {}

    for role, raw_display, count in rows:
        role_key = (role or "").strip().upper()
        if role_key not in STAFF_ROSTER_ROLES:
            continue
        clean = _clean_shift_person_label(raw_display)
        if not clean:
            continue
        by_role_labels.setdefault(role_key, []).append(clean)
        shift_counts[(role_key, clean)] = int(count or 0)

    suggestions: list[RosterImportSuggestion] = []
    for role_key, labels in by_role_labels.items():
        for label in _dedupe_labels_by_last(labels):
            last_raw, first_raw = _parsed_last_first(label)
            last = normalize_roster_last_name(last_raw)
            first = normalize_roster_first_name(first_raw)
            if not last:
                continue
            display = f"{last}, {first}" if first else last
            if not is_likely_person_name(display):
                continue
            if _roster_name_key(role_key, last, first) in existing:
                continue
            last_lk = last_raw.strip().lower()
            total_shifts = sum(
                shift_counts.get((role_key, lbl), 0)
                for lbl in labels
                if _parsed_last_first(lbl)[0].strip().lower() == last_lk
            )
            suggestions.append(
                RosterImportSuggestion(
                    role=role_key,
                    last_name=last,
                    first_name=first,
                    display=display,
                    shift_count=total_shifts,
                )
            )

    return sorted(
        suggestions,
        key=lambda s: (s.role, person_sort_key(s.display)),
    )


def _shift_record_persons(rec: Any) -> tuple[str, ...]:
    """Person label(s) on a parsed shift row (mirrors schedule_import helper)."""
    displays = getattr(rec, "person_displays", None)
    if displays:
        return displays
    person = (getattr(rec, "person_display", None) or "").strip()
    return (person,) if person else ()


def _record_eligible_for_roster_sync(rec: Any) -> bool:
    """Clinical grid rows only — not manager, skipped, or non-RN/Medic/EMT."""
    role = (getattr(rec, "role", None) or "").strip().upper()
    if role not in STAFF_ROSTER_ROLES:
        return False
    if getattr(rec, "is_manager_row", False):
        return False
    if getattr(rec, "skip_reason", None):
        return False
    if getattr(rec, "leave_type", None):
        return True
    return bool(getattr(rec, "filled", False))


def _roster_entries_from_role_labels(
    by_role_labels: dict[str, list[str]],
    existing: set[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Build add-candidates from parsed labels (same dedupe rules as import suggestions)."""
    entries: list[tuple[str, str, str]] = []
    for role_key, labels in by_role_labels.items():
        if role_key not in STAFF_ROSTER_ROLES:
            continue
        for label in _dedupe_labels_by_last(labels):
            last_raw, first_raw = _parsed_last_first(label)
            last = normalize_roster_last_name(last_raw)
            first = normalize_roster_first_name(first_raw)
            if not last:
                continue
            display = f"{last}, {first}" if first else last
            if not is_likely_person_name(display):
                continue
            if _roster_name_key(role_key, last, first) in existing:
                continue
            entries.append((role_key, last, first))
    return entries


def sync_roster_from_import(
    session: Session,
    records: Iterable[Any],
    *,
    created_at: str,
) -> tuple[int, StaffRosterMatchIndex]:
    """
    Auto-add new clinical staff seen in parsed import records.

    Uses the same junk filters as ``suggest_roster_imports``. Skips names
    already on the roster (including inactive — deactivated people are not
    re-added). Returns ``(added_count, match_index)`` for linking
    ``staff_member_id`` on the same import pass.
    """
    by_role_labels: dict[str, list[str]] = {}
    for rec in records:
        if not _record_eligible_for_roster_sync(rec):
            continue
        role_key = (getattr(rec, "role", None) or "").strip().upper()
        for person in _shift_record_persons(rec):
            clean = _clean_shift_person_label(person)
            if not clean:
                continue
            by_role_labels.setdefault(role_key, []).append(clean)

    existing = _existing_roster_keys(session)
    to_add = _roster_entries_from_role_labels(by_role_labels, existing)
    added, _skipped = add_roster_entries(session, to_add, created_at=created_at)
    return added, staff_roster_index_from_session(session)


def add_roster_entries(
    session: Session,
    entries: list[tuple[str, str, str]],
    *,
    created_at: str,
) -> tuple[int, int]:
    """
    Insert active roster rows.

    Returns ``(added_count, skipped_count)``; skips duplicates and invalid roles.
    """
    existing = _existing_roster_keys(session)
    added = 0
    skipped = 0
    for role, last_name, first_name in entries:
        role_key = (role or "").strip().upper()
        if role_key not in STAFF_ROSTER_ROLES:
            skipped += 1
            continue
        last = normalize_roster_last_name(last_name)
        first = normalize_roster_first_name(first_name)
        if not last:
            skipped += 1
            continue
        display = f"{last}, {first}" if first else last
        if not is_likely_person_name(display):
            skipped += 1
            continue
        key = _roster_name_key(role_key, last, first)
        if key in existing:
            skipped += 1
            continue
        session.add(
            StaffRosterEntry(
                role=role_key,
                last_name=last,
                first_name=first,
                active=1,
                created_at=created_at,
            )
        )
        existing.add(key)
        added += 1
    if added:
        session.flush()
    return added, skipped


def parse_roster_import_form_key(key: str) -> tuple[str, str, str] | None:
    """Decode ``role|last|first`` checkbox value from the import form."""
    parts = (key or "").split("|", 2)
    if len(parts) != 3:
        return None
    role, last, first = parts
    role_key = role.strip().upper()
    if role_key not in STAFF_ROSTER_ROLES:
        return None
    last_n = normalize_roster_last_name(last)
    if not last_n:
        return None
    return role_key, last_n, normalize_roster_first_name(first)


def roster_entry_for_display(
    session: Session,
    person_display: str,
    *,
    role: str | None = None,
) -> StaffRosterEntry | None:
    """Find active roster row matching a canonical display label."""
    target = (person_display or "").strip()
    if not target:
        return None
    last, first = _parsed_last_first(target)
    q = session.query(StaffRosterEntry).filter(
        StaffRosterEntry.active == 1,
        StaffRosterEntry.last_name == last,
    )
    if role:
        q = q.filter(StaffRosterEntry.role == role)
    rows = q.all()
    if not rows:
        return None
    if first:
        for row in rows:
            if (row.first_name or "").strip().lower() == first.lower():
                return row
    for row in rows:
        if canonical_display(row) == target:
            return row
    if len(rows) == 1:
        return rows[0]
    return None
