"""The four-cell shift table: required, out of service, staffed, extra, gap.

Day rotor, day ground, night rotor and night ground each get one row:

    Required   what the plan asks for (35 / 21 / 21 / 7 a week, 84 in all)
    OOS        planned shifts taken off because the vehicle was out of service
               for that shift and its crew was released ("no vehicle")
    Net plan   Required less OOS
    Staffed    vehicle-days fully staffed, as stored (hand corrections included)
    Extra      Staffed above the net plan
    Gap        net plan not staffed

The total row nets extras against gaps inside rotor wing and inside ground (the
rule the staffing rate has always used: a spare truck one day makes up a short
day), so its Staffed / Extra / Gap tie to the headline rate rather than to the
sum of the rows above it.

A shift fully crewed on a vehicle marked out of service counts as staffed (the
crew was there), but it could not take a call, so ``available`` leaves it out:
available = counted shifts less crewed-but-down shifts, over the full 84.

``flags`` is the "review before publishing" list for the week edit page: spare
crews, rotor gaps on days ground had a spare crew (a weather swap), hand-edited
counts and out-of-service marks. It never changes a number. Pure calculation,
no database and no Django.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .metrics import REQUIRED_DAY, REQUIRED_NIGHT, REQUIRED_TOTAL, WeekMetrics
from .shortfall import GROUND_DAY_PER_DAY, GROUND_NIGHT_PER_DAY, ShortfallResult

RW_DAY_PER_DAY = 5
RW_NIGHT_PER_DAY = 3

assert 7 * (RW_DAY_PER_DAY + GROUND_DAY_PER_DAY) == REQUIRED_DAY
assert 7 * (RW_NIGHT_PER_DAY + GROUND_NIGHT_PER_DAY) == REQUIRED_NIGHT

SHIFT_TABLE_HEADERS: list[str] = [
    "Shift",
    "Required",
    "OOS",
    "Net plan",
    "Staffed",
    "Extra",
    "Gap",
    "Fill %",
]

# (key, label, service type, "D"/"N", required per week)
_CELLS = (
    ("rw_day", "Day rotor", "RW", "D", RW_DAY_PER_DAY * 7),
    ("gr_day", "Day ground", "GR", "D", GROUND_DAY_PER_DAY * 7),
    ("rw_night", "Night rotor", "RW", "N", RW_NIGHT_PER_DAY * 7),
    ("gr_night", "Night ground", "GR", "N", GROUND_NIGHT_PER_DAY * 7),
)


@dataclass(frozen=True)
class ShiftCell:
    key: str
    label: str
    service_type: str  # "RW" or "GR"
    day_night: str  # "D" or "N"
    required: int
    removed: int
    staffed: int

    @property
    def net_plan(self) -> int:
        return max(0, self.required - self.removed)

    @property
    def extra(self) -> int:
        return max(0, self.staffed - self.net_plan)

    @property
    def gap(self) -> int:
        return max(0, self.net_plan - self.staffed)

    @property
    def fill(self) -> float | None:
        """Staffed over net plan, capped at 100%; None when nothing was planned."""
        if not self.net_plan:
            return None
        return min(self.staffed, self.net_plan) / self.net_plan


@dataclass(frozen=True)
class ShiftTable:
    cells: tuple[ShiftCell, ...]
    counted: int  # vehicles counted toward the staffing rate (extras netted)
    net_plan: int  # required_total after out-of-service removals
    extra_unused: int  # staffed above each type's whole plan, not needed
    gap: int  # net plan still unstaffed after extras are netted
    staffing_rate: float
    available: int  # counted, less crewed-but-out-of-service shifts
    crewed_out_of_service: int
    flags: list[str] = field(default_factory=list)

    @property
    def availability_rate(self) -> float:
        return self.available / REQUIRED_TOTAL if REQUIRED_TOTAL else 0.0

    @property
    def removed(self) -> int:
        return sum(c.removed for c in self.cells)

    @property
    def staffed(self) -> int:
        return sum(c.staffed for c in self.cells)

    @property
    def availability_line(self) -> str:
        return (
            f"Available for calls: {self.available} of {REQUIRED_TOTAL} "
            f"({self.availability_rate * 100:.1f}%). Fully staffed shifts on a "
            "vehicle that was in service."
        )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _day_label(d: date) -> str:
    return d.strftime("%a %b %d")


def _flags(
    cells: tuple[ShiftCell, ...],
    result: ShortfallResult,
    reconcile_notes: list[str],
    crewed_oos: int,
) -> list[str]:
    flags: list[str] = []
    for service, name in (("RW", "Rotor"), ("GR", "Ground")):
        group = [c for c in cells if c.service_type == service]
        extra = sum(c.extra for c in group)
        if not extra:
            continue
        gap = sum(c.gap for c in group)
        where = ", ".join(
            f"{'day' if c.day_night == 'D' else 'night'} +{c.extra}"
            for c in group
            if c.extra
        )
        flags.append(
            f"{name}: {extra} staffed above plan ({where}). "
            f"They offset {min(extra, gap)} of {gap} short {name.lower()} shifts."
        )
    spare_days = {d for d, svc, _dn, _n in result.extra_days if svc == "GR"}
    rotor_short = {s.day for s in result.shortfalls if s.service_type == "RW"}
    for d in sorted(spare_days & rotor_short):
        flags.append(
            f"{_day_label(d)}: rotor was short and ground had a spare crew. If "
            "the crew moved for weather, the total is right but the rotor gap "
            "stays; confirm it."
        )
    flags.extend(reconcile_notes)
    marked = len(result.no_vehicle)
    if marked:
        released = marked - crewed_oos
        flags.append(
            f"{marked} vehicle-day{'s' if marked != 1 else ''} marked out of "
            f"service: {released} released the crew (off the plan), "
            f"{crewed_oos} kept the crew (counted staffed, not available for calls)."
        )
    return flags


def build_shift_table(
    metrics: WeekMetrics,
    result: ShortfallResult,
    reconcile_notes: list[str] | None = None,
) -> ShiftTable:
    """The shift table for one week from its metrics and shortfall analysis."""
    staffed = {
        "rw_day": metrics.rw_day_staffed,
        "rw_night": metrics.rw_night_staffed,
        "gr_day": metrics.gr_day_staffed,
        "gr_night": metrics.gr_night_staffed,
    }
    cells = tuple(
        ShiftCell(
            key=key,
            label=label,
            service_type=svc,
            day_night=dn,
            required=required,
            removed=int(result.planned_removed_cell[(svc, dn)]),
            staffed=int(staffed[key]),
        )
        for key, label, svc, dn, required in _CELLS
    )
    counted = metrics.filled_total
    crewed_oos = sum(1 for n in result.no_vehicle if n.crew_present)
    return ShiftTable(
        cells=cells,
        counted=counted,
        net_plan=metrics.required_total,
        extra_unused=max(0, sum(c.staffed for c in cells) - counted),
        gap=max(0, metrics.required_total - counted),
        staffing_rate=metrics.staffing_rate,
        available=max(0, counted - crewed_oos),
        crewed_out_of_service=crewed_oos,
        flags=_flags(cells, result, list(reconcile_notes or []), crewed_oos),
    )


def shift_table_rows(table: ShiftTable) -> list[list[str]]:
    """Report rows under ``SHIFT_TABLE_HEADERS``, total row last."""
    rows = [
        [
            c.label,
            str(c.required),
            str(c.removed),
            str(c.net_plan),
            str(c.staffed),
            str(c.extra),
            str(c.gap),
            _pct(c.fill),
        ]
        for c in table.cells
    ]
    rows.append(
        [
            "Total",
            str(REQUIRED_TOTAL),
            str(table.removed),
            str(table.net_plan),
            str(table.staffed),
            str(table.extra_unused),
            str(table.gap),
            _pct(table.staffing_rate),
        ]
    )
    return rows


SHIFT_TABLE_NOTE = (
    "Extra staffing offsets gaps within rotor and within ground, so the total "
    "row can differ from the sum of the rows above it."
)
