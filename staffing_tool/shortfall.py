"""Why a planned vehicle was not staffed: no crew, or no operator.

A vehicle-day is staffed only when every required seat has a name (the same
rule the OPS View coverage counts use):

- crew     = RN and Medic
- operator = Pilot (PIC row) on a rotor-wing (RW) unit, EMT on a ground (GR) unit
  (an EMT never substitutes for a pilot)

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
  toward the 1. The *weekly* ground percentage is the total of every ground
  shift staffed (``staffed_all``) over the plan, so an extra truck one day makes
  up for a short day; the per-day list here names the days a seat was missing. Which truck is blamed for a missing slot is only the one
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
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from sqlalchemy import func

from .db import session_scope
from .metrics import (
    CURRENT_DEFINITIONS_FROM_WEEK,
    EXTRA_UNIT_CODES,
    SYSTEM_GR_MAX_SHIFTS_PER_WEEK,
    uses_current_definitions,
)
from .models import (
    WeeklyBaseCoverage,
    WeeklyOpsViewAssignment,
    WeeklyStaffing,
    WeeklyVehicleOutage,
)
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
    # Staffed vehicle-days counted per day against the plan (a spare vehicle one
    # day does not offset a gap another day): what the per-day shortfall list uses.
    staffed: Counter = field(default_factory=Counter)
    # Every fully staffed vehicle-day, including ones beyond the day's plan. The
    # weekly ground % is built from this (an extra truck offsets a short day).
    staffed_all: Counter = field(default_factory=Counter)
    # "D" / "N" -> planned vehicle-days taken off the plan for "no vehicle".
    planned_removed: Counter = field(default_factory=Counter)
    # (service_type, "D"/"N") -> the same removals, split by vehicle type, so the
    # shift table can take them off the right cell (day rotor, night ground, ...).
    planned_removed_cell: Counter = field(default_factory=Counter)
    # (day, "GR", day_night, extra) for each day ground had more fully staffed
    # trucks than its plan: the spare-crew / weather-swap days a reviewer should
    # look at beside any rotor gap. Ground only: a rotor group spans day and
    # night units, so its extra cannot be assigned to a shift.
    extra_days: list[tuple[date, str, str, int]] = field(default_factory=list)
    days_analyzed: int = 0
    weeks_without_detail: list[str] = field(default_factory=list)


def _missing(service_type: str, roles: set[str]) -> tuple[tuple[str, ...], bool]:
    crew = tuple(r for r in CREW_ROLES if r not in roles)
    if service_type == "RW":
        operator_ok = "PIC" in roles  # a pilot is always required on rotor wing
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
            staffed_count = 0
            unavailable: list[str] = []  # day/night of each down vehicle
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
                    # out of service: not a staffing problem
                    unavailable.append(UNIT_MAP[unit][2])
                    continue
                gaps.append(
                    (len(crew) + int(op_missing), order, unit, crew, op_missing)
                )
            # A vehicle out of service can't be staffed, so it takes a slot off
            # the plan only when too few other vehicles remain to fill them all
            # (a down aircraft always does; one of four day trucks doesn't).
            slots = min(g.slots, len(g.units) - len(unavailable))
            for day_night in unavailable[: g.slots - slots]:
                result.planned_removed[day_night] += 1
                result.planned_removed_cell[(g.service_type, day_night)] += 1
            if g.service_type == "GR" and staffed_count > slots:
                result.extra_days.append(
                    (
                        day,
                        g.service_type,
                        UNIT_MAP[g.units[0]][2] if g.units else "D",
                        staffed_count - slots,
                    )
                )
            filled = min(staffed_count, slots)
            result.planned[(g.scope, g.service_type)] += slots
            result.staffed[(g.scope, g.service_type)] += filled
            result.staffed_all[(g.scope, g.service_type)] += staffed_count
            need = slots - filled
            # Blame the vehicles closest to being staffed: they are the ones a
            # single missing seat kept from being staffed.
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
    start = date.fromisoformat(week_start)
    return [start + timedelta(days=i) for i in range(7)]


def load_shortfalls(db_path: str | None, start: date, end: date) -> ShortfallResult:
    """Shortfalls for [start, end], from stored OPS View assignments.

    Only days in weeks that have name-level OPS View rows are analyzed. A week
    imported without them is listed in ``weeks_without_detail`` and left out
    rather than counted as fully short.
    """
    with session_scope(db_path) as session:
        return analyze_range(session, start, end)


def analyze_range(
    session, start: date, end: date, *, tracked_only: bool = False
) -> ShortfallResult:
    """``load_shortfalls`` for a caller that already has a session open.

    ``tracked_only`` limits the window to weeks the current definitions apply to
    (FY2027 onward); reports pass it so last FY is never given reasons it was
    not tracked under. The shortfall script leaves it off so any window can be
    inspected.
    """
    if tracked_only:
        cutoff = date.fromisoformat(CURRENT_DEFINITIONS_FROM_WEEK)
        if end < cutoff:
            return ShortfallResult()
        start = max(start, cutoff)
    start_s, end_s = start.isoformat(), end.isoformat()
    # Weeks overlapping the window (a week can start before ``start``).
    week_floor = (start - timedelta(days=6)).isoformat()
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
        roles[(date.fromisoformat(day_s), unit)].add(role)
    outages = {
        (date.fromisoformat(day_s), unit): note or ""
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


def _analyze_weeks(session, week_starts: Iterable[str]) -> dict[str, ShortfallResult]:
    """Shortfalls for each stored week that has name-level rows, read in bulk.

    Two queries for any number of weeks, so a caller working through a fiscal
    year does not pay a round trip (and a re-parse of the same rows) per week.
    Weeks without name-level rows are absent from the result.
    """
    weeks = sorted(set(week_starts))
    if not weeks:
        return {}
    with_rows: set[str] = set()
    roles: dict[str, dict[tuple[date, str], set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for week, day_s, unit, role, is_staffed in session.query(
        WeeklyOpsViewAssignment.week_start,
        WeeklyOpsViewAssignment.day_date,
        WeeklyOpsViewAssignment.unit_code,
        WeeklyOpsViewAssignment.role,
        WeeklyOpsViewAssignment.is_staffed,
    ).filter(WeeklyOpsViewAssignment.week_start.in_(weeks)):
        with_rows.add(week)
        if is_staffed == 1:
            roles[week][(date.fromisoformat(day_s), unit)].add(role)
    outages: dict[str, dict[tuple[date, str], str]] = defaultdict(dict)
    if with_rows:
        for week, day_s, unit, note in session.query(
            WeeklyVehicleOutage.week_start,
            WeeklyVehicleOutage.day_date,
            WeeklyVehicleOutage.unit_code,
            WeeklyVehicleOutage.note,
        ).filter(WeeklyVehicleOutage.week_start.in_(sorted(with_rows))):
            outages[week][(date.fromisoformat(day_s), unit)] = note or ""
    return {
        week: find_shortfalls(roles[week], _week_days(week), outages[week])
        for week in sorted(with_rows)
    }


def analyze_week(
    session, week_start: str, *, tracked_only: bool = False
) -> ShortfallResult | None:
    """Shortfalls for one stored week, or None if it has no name-level rows.

    ``tracked_only`` also returns None for a week before the current
    definitions apply (last FY).
    """
    if tracked_only and not uses_current_definitions(week_start):
        return None
    return _analyze_weeks(session, [week_start]).get(week_start)


REASON_TABLE_HEADERS: list[str] = ["", "Rotor wing", "Ground"]


def reason_table_rows(result: ShortfallResult | None) -> list[list[str]]:
    """Report rows: why planned vehicles were short, by vehicle type.

    Pilot applies to rotor wing only and EMT to ground only; a seat that does
    not exist on a vehicle type shows an em dash. Returns [] when there is no
    schedule detail to analyze, so a report leaves the block off rather than
    printing zeros it can't back up.
    """
    if result is None or not result.days_analyzed:
        return []
    em = "\u2014"
    by_type = {
        svc: reason_counts(s for s in result.shortfalls if s.service_type == svc)
        for svc in ("RW", "GR")
    }
    shorts = {
        svc: sum(1 for s in result.shortfalls if s.service_type == svc)
        for svc in ("RW", "GR")
    }
    rows = [["Short vehicle-days", str(shorts["RW"]), str(shorts["GR"])]]
    for role, label in (
        ("RN", "Short a nurse"),
        ("Medic", "Short a paramedic"),
        ("Pilot", "Short a pilot"),
        ("EMT", "Short an EMT"),
    ):
        rw = str(by_type["RW"][role]) if role != "EMT" else em
        gr = str(by_type["GR"][role]) if role != "Pilot" else em
        rows.append([label, rw, gr])
    if result.no_vehicle:
        rows.append(
            [
                "No vehicle (not counted)",
                str(sum(1 for n in result.no_vehicle if n.service_type == "RW")),
                str(sum(1 for n in result.no_vehicle if n.service_type == "GR")),
            ]
        )
    return rows


@dataclass(frozen=True)
class ReconcileRow:
    """Stored staffed count vs what the saved schedule detail implies."""

    week_start: str
    scope: str  # base name for rotor wing, "Ground (system)" for ground
    service_type: str  # "RW" or "GR"
    stored: int  # what the dashboard and reports use
    from_schedule: int  # what the stored schedule detail implies

    @property
    def diff(self) -> int:
        return self.stored - self.from_schedule


def _stored_staffed(cov: WeeklyBaseCoverage | None, kind: str) -> int:
    """Stored staffed unit-days for one vehicle type, as ``compute_week_metrics`` reads it."""
    if cov is None:
        return 0
    day = int(getattr(cov, f"{kind}_staffed_day", 0) or 0)
    night = int(getattr(cov, f"{kind}_staffed_night", 0) or 0)
    total = int(getattr(cov, f"{kind}_staffed_unit_days", 0) or 0)
    return day + night if day + night else total


def reconcile_weeks(
    session, week_starts: Iterable[str]
) -> dict[str, list[ReconcileRow]]:
    """``reconcile_week`` for many weeks in three queries, keyed by week_start.

    Weeks with no name-level detail are absent from the result.
    """
    results = _analyze_weeks(session, week_starts)
    if not results:
        return {}
    covs: dict[str, dict[str, WeeklyBaseCoverage]] = defaultdict(dict)
    for c in session.query(WeeklyBaseCoverage).filter(
        WeeklyBaseCoverage.week_start.in_(sorted(results))
    ):
        covs[str(c.week_start)][str(c.base_name)] = c
    rw_groups = [g for g in planned_groups() if g.service_type == "RW"]
    out: dict[str, list[ReconcileRow]] = {}
    for week_start, result in results.items():
        week_covs = covs[week_start]
        rows = [
            ReconcileRow(
                week_start,
                g.scope,
                "RW",
                _stored_staffed(week_covs.get(g.scope), "rw"),
                result.staffed[(g.scope, "RW")],
            )
            for g in rw_groups
        ]
        planned = sum(n for (_scope, svc), n in result.planned.items() if svc == "GR")
        stored = sum(_stored_staffed(c, "gr") for c in week_covs.values())
        derived = sum(
            n for (_scope, svc), n in result.staffed_all.items() if svc == "GR"
        )
        rows.append(
            ReconcileRow(
                week_start,
                "Ground (system)",
                "GR",
                min(stored, planned),
                min(derived, planned),
            )
        )
        out[week_start] = rows
    return out


def reconcile_week(session, week_start: str) -> list[ReconcileRow]:
    """Compare one week's stored coverage with its schedule detail.

    The reasons and shortfall lists are built from the schedule as imported. The
    dashboard percentages come from the stored base coverage, which can be
    corrected by hand on the week edit page. A difference means a hand
    correction (or detail that is out of date); this lists exactly where.
    Returns [] for a week with no name-level detail.
    """
    return reconcile_weeks(session, [week_start]).get(week_start, [])


def net_of_hand_corrections(
    session, result: ShortfallResult | None, week_starts: Iterable[str]
) -> ShortfallResult | None:
    """``result`` without the short days that were fixed by hand.

    A stored count above what the schedule supports means a vehicle-day the
    schedule showed short was later fixed on the week edit page (a late fill, an
    aircraft out of service, a pilot on leave). A fixed day is no longer a
    shortfall, so it is dropped and the reasons tie to the stored percentage.

    The schedule cannot say which day a fix covered. Where a base-week has more
    short days than fixes, the earliest short days are the ones dropped, so the
    seat counts for that base-week are a best estimate; where every short day
    was fixed they are exact. Rotor wing is netted per base, ground as one
    system total. A stored count below the schedule is not a fix and is ignored.
    """
    if result is None:
        return None
    # Only a week that has short days in ``result`` can have one dropped, so the
    # rest (every week before the current definitions, on a long window) are
    # never read.
    week_of_day = {day: week for week in week_starts for day in _week_days(week)}
    by_week: dict[str, list[UnitDayShortfall]] = defaultdict(list)
    for s in result.shortfalls:
        week = week_of_day.get(s.day)
        if week is not None:
            by_week[week].append(s)
    drop: set[int] = set()
    for week, rows in reconcile_weeks(session, by_week).items():
        for row in rows:
            if row.diff <= 0:
                continue
            candidates = sorted(
                (
                    s
                    for s in by_week[week]
                    if s.service_type == row.service_type
                    and (row.service_type == "GR" or s.base == row.scope)
                ),
                key=lambda s: (s.day, s.unit_code),
            )
            drop.update(id(s) for s in candidates[: row.diff])
    if not drop:
        return result
    return replace(
        result, shortfalls=[s for s in result.shortfalls if id(s) not in drop]
    )
