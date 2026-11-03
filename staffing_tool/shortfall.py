"""Why a planned vehicle was not staffed: no crew, or no operator.

A vehicle-day is staffed only when every required seat has a name (the same
rule the OPS View coverage counts use):

- crew     = RN and Medic
- operator = Pilot (PIC row) on a rotor-wing (RW) unit, EMT on a ground (GR) unit

When a planned vehicle-day is short, this module says which side was missing:
``No crew`` (RN and/or Medic), ``No operator`` (Pilot on RW, EMT on GR), or
both. It is derived from ``WeeklyOpsViewAssignment`` rows, which the schedule
import already stores, so it works on every imported week with no re-import.

Pure calculation in ``find_shortfalls``; ``load_shortfalls`` adds the database
read. No Django.

What counts as "planned":

- **Rotor wing**: the per-base weekly caps (``MAX_RW_UNIT_DAYS_PER_WEEK`` / 7),
  the same plan the coverage numbers use.
- **Ground**: the system runs 4 trucks a day, 3 on days and 1 on nights, and
  which truck or base staffs them does not matter (crews move around to where
  the staffing is). So ground is one target per shift for the whole system, not
  a plan per base; any staffed day truck counts toward the 3, any night truck
  toward the 1. Which truck is blamed for a missing slot is only the one
  closest to being staffed, so read ground by cause and shift, not by base.
- Opportunistic units (``EXTRA_UNIT_CODES``) are never planned.

**No vehicle** (``outages``, entered by hand per vehicle-day) is not a staffing
problem and never counts against staffing. A marked vehicle-day with a full
crew counts as staffed, as it always did; one without a crew is removed from
the planned count. Either way it is listed on its own in ``no_vehicle``.

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
from .metrics import EXTRA_UNIT_CODES, SYSTEM_GR_MAX_SHIFTS_PER_WEEK
from .models import WeeklyOpsViewAssignment, WeeklyStaffing, WeeklyVehicleOutage
from .schedule_import import MAX_RW_UNIT_DAYS_PER_WEEK, UNIT_MAP

CREW_ROLES: tuple[str, ...] = ("RN", "Medic")

CAUSE_CREW = "No crew"
CAUSE_OPERATOR = "No operator"
CAUSE_BOTH = "No crew and no operator"
CAUSES: tuple[str, ...] = (CAUSE_CREW, CAUSE_OPERATOR, CAUSE_BOTH)

# Ground trucks the system wants running each day: 3 day + 1 night.
GROUND_DAY_PER_DAY = 3
GROUND_NIGHT_PER_DAY = 1
SYSTEM_GROUND_PER_DAY = GROUND_DAY_PER_DAY + GROUND_NIGHT_PER_DAY
# Same 28 a week the System GR % denominator uses.
assert SYSTEM_GROUND_PER_DAY * 7 == SYSTEM_GR_MAX_SHIFTS_PER_WEEK
# Scope labels for the system-wide ground targets in planned/staffed counters.
GROUND_DAY_SCOPE = "System day"
GROUND_NIGHT_SCOPE = "System night"

# (day, unit_code) -> set of OPS View role labels that had a staffed cell.
StaffedRoles = Mapping[tuple[date, str], set[str]]
# (day, unit_code) -> note, for vehicles marked out of service.
Outages = Mapping[tuple[date, str], str]


def operator_label(service_type: str) -> str:
    """Operator seat name for the vehicle type: Pilot on RW, EMT on GR."""
    return "Pilot" if service_type == "RW" else "EMT"


@dataclass(frozen=True)
class Group:
    """Vehicles that compete for the same planned slots on a given day."""

    service_type: str  # "RW" or "GR"
    scope: str  # base name for RW, GROUND_DAY/NIGHT_SCOPE for GR
    units: tuple[str, ...]  # day units before night units
    slots: int  # planned vehicles per day


def planned_groups() -> list[Group]:
    """Rotor wing per base, plus the system-wide ground day and night targets."""
    rw_units: dict[str, list[tuple[str, str]]] = defaultdict(list)
    gr_day: list[str] = []
    gr_night: list[str] = []
    for code, (base, service, day_night) in UNIT_MAP.items():
        if code in EXTRA_UNIT_CODES:
            continue
        if service == "RW":
            rw_units[base].append((day_night, code))
        elif day_night == "D":
            gr_day.append(code)
        else:
            gr_night.append(code)

    groups: list[Group] = []
    for base, units in rw_units.items():
        slots = MAX_RW_UNIT_DAYS_PER_WEEK.get(base, 0) // 7
        if slots > 0:
            codes = tuple(
                c for _dn, c in sorted(units, key=lambda t: (t[0] != "D", t[1]))
            )
            groups.append(Group("RW", base, codes, slots))
    groups.append(Group("GR", GROUND_DAY_SCOPE, tuple(gr_day), GROUND_DAY_PER_DAY))
    groups.append(
        Group("GR", GROUND_NIGHT_SCOPE, tuple(gr_night), GROUND_NIGHT_PER_DAY)
    )
    return groups


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


@dataclass(frozen=True)
class NoVehicleDay:
    """A vehicle marked out of service for a day. Never counts against staffing."""

    day: date
    base: str
    service_type: str
    unit_code: str
    note: str
    crew_present: bool  # every seat was filled, so the shift counts as staffed

    @property
    def treatment(self) -> str:
        return "Staffed" if self.crew_present else "Removed from planned"


@dataclass
class ShortfallResult:
    shortfalls: list[UnitDayShortfall] = field(default_factory=list)
    no_vehicle: list[NoVehicleDay] = field(default_factory=list)
    # (scope, service) -> planned / staffed vehicle-days across the window.
    # scope is the base for RW and GROUND_SCOPE for GR.
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
    staffed_roles: StaffedRoles,
    days: Iterable[date],
    outages: Outages | None = None,
) -> ShortfallResult:
    """Attribute every unstaffed planned vehicle-day on ``days`` to a cause."""
    outages = outages or {}
    result = ShortfallResult()
    groups = planned_groups()
    day_list = sorted(set(days))
    result.days_analyzed = len(day_list)
    for day in day_list:
        for g in groups:
            slots = g.slots
            staffed_count = 0
            unavailable = 0
            gaps: list[tuple[int, int, str, tuple[str, ...], bool]] = []
            for order, unit in enumerate(g.units):
                roles = staffed_roles.get((day, unit), set())
                crew, op_missing = _missing(g.service_type, roles)
                complete = not crew and not op_missing
                marked = (day, unit) in outages
                if marked:
                    result.no_vehicle.append(
                        NoVehicleDay(
                            day=day,
                            base=UNIT_MAP[unit][0],
                            service_type=g.service_type,
                            unit_code=unit,
                            note=outages[(day, unit)],
                            crew_present=complete,
                        )
                    )
                if complete:
                    staffed_count += 1
                    continue
                if marked:
                    unavailable += 1  # out of service: not a staffing problem
                    continue
                gaps.append(
                    (len(crew) + int(op_missing), order, unit, crew, op_missing)
                )
            # A vehicle out of service can't be staffed, so it takes a slot off
            # the plan only when too few other vehicles remain to fill them all
            # (a down aircraft always does; one of four day trucks doesn't).
            slots = min(slots, len(g.units) - unavailable)
            filled = min(staffed_count, slots)
            result.planned[(g.scope, g.service_type)] += slots
            result.staffed[(g.scope, g.service_type)] += filled
            need = slots - filled
            # Blame the vehicles closest to being staffed: they are the ones a
            # single missing seat kept off the board.
            for _n, _order, unit, crew, op_missing in sorted(gaps)[:need]:
                result.shortfalls.append(
                    UnitDayShortfall(
                        day=day,
                        base=UNIT_MAP[unit][0],
                        service_type=g.service_type,
                        unit_code=unit,
                        day_night=UNIT_MAP[unit][2],
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
        outage_rows = (
            session.query(
                WeeklyVehicleOutage.day_date,
                WeeklyVehicleOutage.unit_code,
                WeeklyVehicleOutage.note,
            )
            .filter(WeeklyVehicleOutage.day_date >= start_s)
            .filter(WeeklyVehicleOutage.day_date <= end_s)
            .all()
        )
    roles: dict[tuple[date, str], set[str]] = defaultdict(set)
    for day_s, unit, role in staffed_rows:
        roles[(datetime.strptime(day_s, "%Y-%m-%d").date(), unit)].add(role)
    outages = {
        (datetime.strptime(day_s, "%Y-%m-%d").date(), unit): note or ""
        for day_s, unit, note in outage_rows
    }
    days = [
        d
        for w in weeks_with_rows
        for d in _week_days(w)
        if start_s <= d.isoformat() <= end_s
    ]
    result = find_shortfalls(roles, days, outages)
    result.weeks_without_detail = sorted(set(known_weeks) - weeks_with_rows)
    return result


def summarize_by(result: ShortfallResult, key) -> dict[object, dict[str, int]]:
    """Count shortfalls per ``key(shortfall)`` split by cause."""
    out: dict[object, dict[str, int]] = defaultdict(lambda: dict.fromkeys(CAUSES, 0))
    for s in result.shortfalls:
        out[key(s)][s.cause] += 1
    return out


# The four seats a short vehicle can be missing, with the plain name the
# reports use. RN and Medic are the crew; Pilot (RW) or EMT (GR) is the operator.
REASON_ROLES: tuple[str, ...] = ("RN", "Medic", "Pilot", "EMT")
REASON_LABELS: dict[str, str] = {
    "RN": "Nurse",
    "Medic": "Paramedic",
    "Pilot": "Pilot",
    "EMT": "EMT",
}


def reason_counts(shortfalls: Iterable[UnitDayShortfall]) -> dict[str, int]:
    """Short vehicle-days by missing seat.

    A vehicle missing two seats counts once under each, so the four counts can
    add to more than the number of short vehicle-days.
    """
    out = dict.fromkeys(REASON_ROLES, 0)
    for s in shortfalls:
        for role in s.missing_crew:
            out[role] += 1
        if s.operator_missing:
            out[s.operator] += 1
    return out


def analyze_week(session, week_start: str) -> ShortfallResult | None:
    """Shortfalls for one stored week, or None if it has no name-level rows."""
    has_rows = (
        session.query(WeeklyOpsViewAssignment.id)
        .filter(WeeklyOpsViewAssignment.week_start == week_start)
        .first()
    )
    if has_rows is None:
        return None
    staffed_rows = (
        session.query(
            WeeklyOpsViewAssignment.day_date,
            WeeklyOpsViewAssignment.unit_code,
            WeeklyOpsViewAssignment.role,
        )
        .filter(WeeklyOpsViewAssignment.week_start == week_start)
        .filter(WeeklyOpsViewAssignment.is_staffed == 1)
        .all()
    )
    outage_rows = (
        session.query(
            WeeklyVehicleOutage.day_date,
            WeeklyVehicleOutage.unit_code,
            WeeklyVehicleOutage.note,
        )
        .filter(WeeklyVehicleOutage.week_start == week_start)
        .all()
    )
    roles: dict[tuple[date, str], set[str]] = defaultdict(set)
    for day_s, unit, role in staffed_rows:
        roles[(datetime.strptime(day_s, "%Y-%m-%d").date(), unit)].add(role)
    outages = {
        (datetime.strptime(day_s, "%Y-%m-%d").date(), unit): note or ""
        for day_s, unit, note in outage_rows
    }
    return find_shortfalls(roles, _week_days(week_start), outages)
