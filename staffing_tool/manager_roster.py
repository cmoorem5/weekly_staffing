"""
Manager last-name roster for schedule import (leave exclusion + manager line shifts).

Rows are stored in staffing.db and edited via Django Admin. Seeded once from the
built-in list when the table is empty.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import ManagerRosterLastName

# Default seed when ``manager_roster_last_name`` has no rows (same roster as
# originally shipped in schedule_import).
BUILTIN_MANAGER_LAST_NAMES: tuple[str, ...] = (
    "Ahlstedt",
    "Denison",
    "Doherty",
    "Ender",
    "Estanislao",
    "Holst",
    "Kadow",
    "Moore",
    "Powers",
    "Bowman",
    "Farkas",
    "Frakes",
    "Muszalski",
    "Steckevicz",
    "Wallace",
)


def default_manager_last_names_upper() -> frozenset[str]:
    """Uppercase last names used when no DB path is available (tests / CLI)."""
    return frozenset(n.strip().upper() for n in BUILTIN_MANAGER_LAST_NAMES if n.strip())


def manager_last_names_upper_from_session(session: Session) -> frozenset[str]:
    """Active roster from DB (may be empty)."""
    rows = session.query(ManagerRosterLastName.last_name).all()
    return frozenset(r[0].strip().upper() for r in rows if r[0] and str(r[0]).strip())


def seed_manager_roster_if_empty(session: Session) -> None:
    """Insert built-in names if the roster table has no rows."""
    if session.query(ManagerRosterLastName).count() > 0:
        return
    for name in BUILTIN_MANAGER_LAST_NAMES:
        clean = name.strip()
        if not clean:
            continue
        session.add(ManagerRosterLastName(last_name=clean))
