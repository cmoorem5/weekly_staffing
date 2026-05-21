"""
Canonical manager display names for schedule import and reporting.

Schedule rows often store raw column A+B labels (e.g. ``m, Ender``) when roster
token matching was not applied. Reporting should group by roster last name.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .manager_roster import (
    default_manager_last_names_upper,
    manager_last_names_upper_from_session,
)
from .models import WeeklyManagerShift


def name_tokens_from_display(person_display: str) -> set[str]:
    """Uppercase tokens from a stored or raw person label."""
    tokens: set[str] = set()
    for part in (person_display or "").replace(",", " ").split():
        clean = part.strip(".,")
        if clean:
            tokens.add(clean.upper())
    return tokens


def canonical_manager_name(
    person_display: str,
    roster_upper: frozenset[str] | None = None,
) -> str:
    """
    Map a stored label to the roster last name when possible.

    Examples:
      ``m, Ender`` → ``Ender``
      ``P, Doherty`` → ``Doherty``
      ``1-Jonathan Tonelli, Holst`` → ``Holst``
    """
    raw = (person_display or "").strip()
    if not raw:
        return "(unknown)"
    if roster_upper is None:
        roster_upper = default_manager_last_names_upper()
    roster_hit = name_tokens_from_display(raw) & roster_upper
    if roster_hit:
        return min(roster_hit).title()
    return raw


def roster_upper_from_session_or_default(session: Session) -> frozenset[str]:
    names = manager_last_names_upper_from_session(session)
    return names if names else default_manager_last_names_upper()


def backfill_canonical_manager_shift_names(session: Session) -> int:
    """
    Update ``weekly_manager_shifts.person_display`` to canonical roster names.

    Only scans rows whose label still looks raw (contains a comma).
    Returns the number of rows changed. Safe to run repeatedly.
    """
    roster_upper = roster_upper_from_session_or_default(session)
    updated = 0
    rows = (
        session.query(WeeklyManagerShift)
        .filter(WeeklyManagerShift.person_display.contains(","))
        .all()
    )
    for row in rows:
        canon = canonical_manager_name(row.person_display, roster_upper)
        if canon != row.person_display:
            row.person_display = canon
            updated += 1
    if updated:
        session.flush()
    return updated
