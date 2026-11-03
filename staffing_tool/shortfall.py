"""Why a planned vehicle was not staffed: no crew, or no operator.

A vehicle-day is staffed only when every required seat has a name (the same
rule the OPS View coverage counts use):

- crew     = RN and Medic
- operator = Pilot (PIC row) on a rotor-wing (RW) unit, EMT on a ground (GR) unit

When a planned vehicle-day is short, this module says which side was missing:
``No crew`` (RN and/or Medic), ``No operator`` (Pilot on RW, EMT on GR), or
both. It is derived entirely from ``WeeklyOpsViewAssignment`` rows, which the
schedule import already stores, so it works on every imported week with no
schema change and no re-import.

Pure calculation in ``find_shortfalls``; ``load_shortfalls`` adds the database
read. No Django.

Two rules keep it reconciled with the base-coverage numbers:

- "Planned" vehicle-days follow the same weekly caps as coverage
  (``MAX_RW_UNIT_DAYS_PER_WEEK`` / ``MAX_GR_UNIT_DAYS_PER_WEEK`` divided by 7
  days = slots per base per day). Plymouth ground has one planned slot a day,
  so a staffed night unit (NP) covers a missing day unit (PG), exactly as the
  capped coverage count treats it.
- Opportunistic units (``EXTRA_UNIT_CODES``) are never planned.

A blank cell is the only gap signal in the workbook (there is no ``OPEN`` text
and no leave code in crew or pilot cells), so a required seat with no staffed
row for that vehicle-day is a gap.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func

from .db import session_scope
from .metrics import EXTRA_UNIT_CODES
from .models import WeeklyOpsViewAssignment, WeeklyStaffing
from .schedule_import import (
    MAX_GR_UNIT_DAYS_PER_WEEK,
    MAX_RW_UNIT_DAYS_PER_WEEK,
    UNIT_MAP,
)

CREW_ROLES: tuple[str, ...] = ("RN", "Medic")

CAUSE_CREW = "No crew"
CAUSE_OPERATOR = "No operator"
CAUSE_BOTH = "No crew and no operator"
CAUSES: tuple[str, ...] = (CAUSE_CREW, CAUSE_OPERATOR, CAUSE_BOTH)

# (day, unit_code) -> set of OPS View role labels that had a staffed cell.
StaffedRoles = Mapping[tuple[date, str], set[str]]


def operator_label(service_type: str) -> str:
    """Operator seat name for the vehicle type: Pilot on RW, EMT on GR."""
    return "Pilot" if service_type == "RW" else "EMT"


def slots_per_day(base: str, service_type: str) -> int:
    """Planned vehicles per day for a base and vehicle type (weekly cap / 7)."""
    caps = (
        MAX_RW_UNIT_DAYS_PER_WEEK
        if service_type == "RW"
        else (MAX_GR_UNIT_DAYS_PER_WEEK)
    )
    return caps.get(base, 0) // 7


def planned_units() -> dict[tuple[str, str], list[str]]:
    """(base, service) -> unit codes eligible to fill that base's daily slots.

    Day units sort before night units. Extra units are excluded.
    """
    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for code, (base, service, day_night) in UNIT_MAP.items():
        if code in EXTRA_UNIT_CODES:
            continue
        groups[(base, service)].append((day_night, code))
    return {
        key: [code for _dn, code in sorted(units, key=lambda t: (t[0] != "D", t[1]))]
        for key, units in groups.items()
        if slots_per_day(*key) > 0
    }


@dataclass(frozen=True)
class UnitDayShortfall:
    """One planned vehicle-day that was not staffed, and what was missing."""

    day: date
    base: str
    service_type: str  # "RW" or "GR"
    unit_code: str
    day_night: str  # "D" or "N"
    missing_crew: tuple[str, ...]  # subset of ("RN", "Medic")
    operator_missing: bool

    @property
    def operator(self) -> str:
        return operator_label(self.service_type)

    @property
    def cause(self) -> str:
        if self.missing_crew and self.operator_missing:
            return CAUSE_BOTH
        if self.operator_missing:
            return CAUSE_OPERATOR
        return CAUSE_CREW

    @property
    def detail(self) -> str:
        """Plain-language gap, e.g. 'RN + Pilot' or 'Medic'."""
        parts = list(self.missing_crew)
        if self.operator_missing:
            parts.append(self.operator)
        return " + ".join(parts)


@dataclass
class ShortfallResult:
    shortfalls: list[UnitDayShortfall] = field(default_factory=list)
    # (base, service) -> planned / staffed vehicle-days across the window.
    planned: Counter = field(default_factory=Counter)
    staffed: Counter = field(default_factory=Counter)
    days_analyzed: int = 0
    weeks_without_detail: list[str] = field(default_factory=list)


def _missing(service_type: str, roles: set[str]) -> tuple[tuple[str, ...], bool]:
    crew = tuple(r for r in CREW_ROLES if r not in roles)
    if service_type == "RW":
        operator_ok = "PIC" in roles or "EMT" in roles
    else:
        operator_ok = "EMT" in roles
    return crew, not operator_ok


def find_shortfalls(
    staffed_roles: StaffedRoles, days: Iterable[date]
) -> ShortfallResult:
    """Attribute every unstaffed planned vehicle-day on ``days`` to a cause."""
    result = ShortfallResult()
    groups = planned_units()
    day_list = sorted(set(days))
    result.days_analyzed = len(day_list)
    for day in day_list:
        for (base, service), units in groups.items():
            slots = slots_per_day(base, service)
            result.planned[(base, service)] += slots
            gaps: list[tuple[int, int, str, tuple[str, ...], bool, str]] = []
            staffed_count = 0
            for order, unit in enumerate(units):
                roles = staffed_roles.get((day, unit), set())
                crew, op_missing = _missing(service, roles)
                if not crew and not op_missing:
                    staffed_count += 1
                    continue
                gaps.append(
                    (
                        len(crew) + int(op_missing),
                        order,
                        unit,
                        crew,
                        op_missing,
                        UNIT_MAP[unit][2],
                    )
                )
            filled = min(staffed_count, slots)
            result.staffed[(base, service)] += filled
            need = slots - filled
            # Blame the vehicles closest to being staffed: they are the ones a
            # single missing seat kept off the board.
            for _n, _order, unit, crew, op_missing, dn in sorted(gaps)[:need]:
                result.shortfalls.append(
                    UnitDayShortfall(
                        day=day,
                        base=base,
                        service_type=service,
                        unit_code=unit,
                        day_night=dn,
                        missing_crew=crew,
                        operator_missing=op_missing,
                    )
                )
    return result


def _week_days(week_start: str) -> list[date]:
    start = datetime.strptime(week_start, "%Y-%m-%d").date()
    return [start + timedelta(days=i) for i in range(7)]


def load_shortfalls(db_path: str | None, start: date, end: date) -> ShortfallResult:
    """Shortfalls for [start, end], from stored OPS View assignments.

    Only days in weeks that have name-level OPS View rows are analyzed. A week
    imported without them is listed in ``weeks_without_detail`` and left out
    rather than counted as fully short.
    """
    start_s, end_s = start.isoformat(), end.isoformat()
    # Weeks overlapping the window (a week can start before ``start``).
    week_floor = (start - timedelta(days=6)).isoformat()
    with session_scope(db_path) as session:
        known_weeks = [
            w
            for (w,) in session.query(WeeklyStaffing.week_start)
            .filter(WeeklyStaffing.week_start >= week_floor)
            .filter(WeeklyStaffing.week_start <= end_s)
            .all()
        ]
        weeks_with_rows = {
            w
            for (w,) in session.query(WeeklyOpsViewAssignment.week_start)
            .filter(WeeklyOpsViewAssignment.week_start >= week_floor)
            .filter(WeeklyOpsViewAssignment.week_start <= end_s)
            .group_by(WeeklyOpsViewAssignment.week_start)
            .having(func.count(WeeklyOpsViewAssignment.id) > 0)
            .all()
        }
        staffed_rows = (
            session.query(
                WeeklyOpsViewAssignment.day_date,
                WeeklyOpsViewAssignment.unit_code,
                WeeklyOpsViewAssignment.role,
            )
            .filter(WeeklyOpsViewAssignment.week_start.in_(weeks_with_rows or [""]))
            .filter(WeeklyOpsViewAssignment.is_staffed == 1)
            .all()
        )
    roles: dict[tuple[date, str], set[str]] = defaultdict(set)
    for day_s, unit, role in staffed_rows:
        roles[(datetime.strptime(day_s, "%Y-%m-%d").date(), unit)].add(role)
    days = [
        d
        for w in weeks_with_rows
        for d in _week_days(w)
        if start_s <= d.isoformat() <= end_s
    ]
    result = find_shortfalls(roles, days)
    result.weeks_without_detail = sorted(set(known_weeks) - weeks_with_rows)
    return result


def summarize_by(result: ShortfallResult, key) -> dict[object, dict[str, int]]:
    """Count shortfalls per ``key(shortfall)`` split by cause."""
    out: dict[object, dict[str, int]] = defaultdict(lambda: dict.fromkeys(CAUSES, 0))
    for s in result.shortfalls:
        out[key(s)][s.cause] += 1
    return out
