"""Edit one week as a grid: every vehicle, every day, every seat.

The schedule import stores the OPS View as one ``WeeklyOpsViewAssignment`` row per
vehicle, seat and day. This module shows those rows as a grid, applies hand
corrections to them (a crew moved to another vehicle, a name the parser misread,
a seat left open) together with the "vehicle out of service" marks, and then
rebuilds everything that depends on them from the grid alone: base coverage, the
per-day OPS counts, and the plan figures (``recompute_week_targets``). The
coverage percentages and the "why coverage fell short" reasons therefore come
from the same data and can't disagree.

What it does not touch: the per-person detail (``WeeklyPersonShift``: role fill,
shift mix, daily export), which is built from the Medic/RN/EMT sheets rather than
the OPS View. A re-import replaces the grid with what the workbook says.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from .metrics import EXTRA_UNIT_CODES
from .models import (
    BaseConfig,
    WeeklyBaseCoverage,
    WeeklyOpsViewAssignment,
    WeeklyOpsViewDay,
    WeeklyStaffing,
    WeeklyVehicleOutage,
)
from .schedule_import import UNIT_MAP, _cap_base_coverage_split
from .shortfall import REASON_LABELS, _missing
from .timeutil import utc_now_iso
from .week_targets import recompute_week_targets

NOTE_MAX = 200
NAME_MAX = 256
# Seats shown for each vehicle type, in the order they appear on the grid.
SEATS_BY_SERVICE: dict[str, tuple[str, ...]] = {
    "RW": ("RN", "Medic", "PIC"),
    "GR": ("RN", "Medic", "EMT"),
}
SEAT_LABELS: dict[str, str] = {
    "RN": "RN",
    "Medic": "Medic",
    "PIC": "Pilot",
    "EMT": "EMT",
}
# Joins several names sharing one seat (two RN rows, a partnered EMT pair).
NAME_JOINER = " / "


@dataclass
class GridRow:
    """One vehicle on one day."""

    day: date
    unit: str
    base: str
    service: str  # "RW" or "GR"
    day_night: str  # "D" or "N"
    seats: dict[str, str]  # seat -> names, "" when nobody is on it
    out_of_service: bool = False
    note: str = ""
    extra: bool = False  # opportunistic unit that is never planned

    @property
    def missing(self) -> list[str]:
        """Seat labels with nobody on them (a vehicle is staffed when none)."""
        crew, operator_missing = _missing(
            self.service, {s for s, v in self.seats.items() if v}
        )
        labels = [REASON_LABELS[r] for r in crew]
        if operator_missing:
            labels.append("Pilot" if self.service == "RW" else "EMT")
        return labels

    @property
    def staffed(self) -> bool:
        return not self.missing


@dataclass
class WeekGrid:
    week_start: str
    days: list[date]
    rows: list[GridRow] = field(default_factory=list)

    def rows_for(self, day: date) -> list[GridRow]:
        return [r for r in self.rows if r.day == day]


def _week_days(week_start: str) -> list[date]:
    start = date.fromisoformat(week_start)
    return [start + timedelta(days=i) for i in range(7)]


def has_grid(session: Session, week_start: str) -> bool:
    """True when the week has name-level OPS View rows to show and edit."""
    return (
        session.query(WeeklyOpsViewAssignment.id)
        .filter(WeeklyOpsViewAssignment.week_start == week_start)
        .first()
        is not None
    )


def _unit_order(units: set[str]) -> list[str]:
    """Rotor wing first, then ground; by base, day before night."""
    return sorted(
        units,
        key=lambda u: (
            UNIT_MAP[u][1] != "RW",
            UNIT_MAP[u][0],
            UNIT_MAP[u][2] != "D",
            u,
        ),
    )


def _cell_names(rows: list[WeeklyOpsViewAssignment]) -> str:
    """Names staffed on one seat (an "OPEN" or blank cell reads as empty)."""
    ordered = sorted(rows, key=lambda r: (r.excel_row, r.id))
    return NAME_JOINER.join(
        r.person_display.strip()
        for r in ordered
        if r.is_staffed and r.person_display.strip()
    )


def load_week_grid(session: Session, week_start: str) -> WeekGrid:
    """Every planned vehicle on every day, with who is on each seat."""
    by_cell: dict[tuple[str, str, str], list[WeeklyOpsViewAssignment]] = {}
    units_present: set[str] = set()
    for a in session.query(WeeklyOpsViewAssignment).filter(
        WeeklyOpsViewAssignment.week_start == week_start
    ):
        by_cell.setdefault((a.day_date, a.unit_code, a.role), []).append(a)
        units_present.add(a.unit_code)
    outages = {
        (o.day_date, o.unit_code): o.note or ""
        for o in session.query(WeeklyVehicleOutage).filter(
            WeeklyVehicleOutage.week_start == week_start
        )
    }
    units = {u for u in UNIT_MAP if u not in EXTRA_UNIT_CODES}
    # An unplanned extra truck is shown only on a week where someone staffed it.
    units |= {u for u in units_present if u in UNIT_MAP}
    days = _week_days(week_start)
    grid = WeekGrid(week_start=week_start, days=days)
    for day in days:
        iso = day.isoformat()
        for unit in _unit_order(units):
            base, service, dn = UNIT_MAP[unit]
            grid.rows.append(
                GridRow(
                    day=day,
                    unit=unit,
                    base=base,
                    service=service,
                    day_night=dn,
                    seats={
                        seat: _cell_names(by_cell.get((iso, unit, seat), []))
                        for seat in SEATS_BY_SERVICE[service]
                    },
                    out_of_service=(iso, unit) in outages,
                    note=outages.get((iso, unit), ""),
                    extra=unit in EXTRA_UNIT_CODES,
                )
            )
    return grid


# ---- form field names -------------------------------------------------------


def seat_field(day: date, unit: str, seat: str) -> str:
    return f"seat|{day.isoformat()}|{unit}|{seat}"


def outage_field(day: date, unit: str) -> str:
    return f"oos|{day.isoformat()}|{unit}"


def note_field(day: date, unit: str) -> str:
    return f"note|{day.isoformat()}|{unit}"


@dataclass
class GridSaveResult:
    cells_changed: int = 0
    outages_added: int = 0
    outages_removed: int = 0
    outage_notes_changed: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.cells_changed
            or self.outages_added
            or self.outages_removed
            or self.outage_notes_changed
        )


def _clean_name(value: str | None) -> str:
    return " ".join((value or "").split())[:NAME_MAX]


def apply_grid_post(
    session: Session, week_start: str, post: dict[str, str] | object
) -> GridSaveResult:
    """Write the submitted grid, then rebuild everything that depends on it.

    ``post`` is the submitted form (``.get`` per field name). A checkbox is "on"
    when present. Only cells whose text differs from what is stored are
    rewritten, so untouched cells keep the parser's row provenance.
    """
    get = post.get  # type: ignore[attr-defined]
    current = load_week_grid(session, week_start)
    result = GridSaveResult()

    for row in current.rows:
        iso = row.day.isoformat()
        for seat in SEATS_BY_SERVICE[row.service]:
            key = seat_field(row.day, row.unit, seat)
            posted = get(key)
            if posted is None:
                continue  # field not on the form: leave the cell alone
            new = _clean_name(posted)
            if new == row.seats[seat]:
                continue
            session.query(WeeklyOpsViewAssignment).filter(
                WeeklyOpsViewAssignment.week_start == week_start,
                WeeklyOpsViewAssignment.day_date == iso,
                WeeklyOpsViewAssignment.unit_code == row.unit,
                WeeklyOpsViewAssignment.role == seat,
            ).delete(synchronize_session=False)
            if new:
                session.add(
                    WeeklyOpsViewAssignment(
                        week_start=week_start,
                        day_date=iso,
                        unit_code=row.unit,
                        role=seat,
                        excel_row=0,  # entered by hand: no workbook row
                        person_display=new,
                        raw_value=new,
                        is_staffed=1,
                    )
                )
            result.cells_changed += 1

        want_out = get(outage_field(row.day, row.unit)) is not None
        note = (get(note_field(row.day, row.unit)) or "").strip()[:NOTE_MAX]
        existing = (
            session.query(WeeklyVehicleOutage)
            .filter(
                WeeklyVehicleOutage.day_date == iso,
                WeeklyVehicleOutage.unit_code == row.unit,
            )
            .first()
        )
        if want_out and existing is None:
            session.add(
                WeeklyVehicleOutage(
                    week_start=week_start,
                    day_date=iso,
                    unit_code=row.unit,
                    note=note or None,
                    created_at=utc_now_iso(),
                )
            )
            result.outages_added += 1
        elif want_out and existing is not None:
            if (existing.note or "") != note:
                existing.note = note or None
                result.outage_notes_changed += 1
        elif not want_out and existing is not None:
            session.delete(existing)
            result.outages_removed += 1

    session.flush()
    rebuild_from_grid(session, week_start)
    return result


def rebuild_from_grid(session: Session, week_start: str) -> None:
    """Recompute coverage, per-day OPS counts and plan figures from the grid."""
    week = (
        session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start == week_start)
        .first()
    )
    if week is None:
        return
    roles: dict[tuple[str, str], set[str]] = {}
    for day_s, unit, role in session.query(
        WeeklyOpsViewAssignment.day_date,
        WeeklyOpsViewAssignment.unit_code,
        WeeklyOpsViewAssignment.role,
    ).filter(
        WeeklyOpsViewAssignment.week_start == week_start,
        WeeklyOpsViewAssignment.is_staffed == 1,
    ):
        roles.setdefault((day_s, unit), set()).add(role)

    rw_day: dict[str, int] = {}
    rw_night: dict[str, int] = {}
    gr_day: dict[str, int] = {}
    gr_night: dict[str, int] = {}
    per_day: dict[tuple[str, str], list[int]] = {}
    for (day_s, unit), present in roles.items():
        info = UNIT_MAP.get(unit)
        if info is None:
            continue
        base, service, dn = info
        crew, operator_missing = _missing(service, present)
        if crew or operator_missing:
            continue
        counts = (
            (rw_night if dn == "N" else rw_day)
            if service == "RW"
            else (gr_night if dn == "N" else gr_day)
        )
        counts[base] = counts.get(base, 0) + 1
        slot = per_day.setdefault((day_s, base), [0, 0])
        slot[0 if service == "RW" else 1] += 1
    # Same weekly caps the import applies to its coverage.
    _cap_base_coverage_split(rw_day, rw_night, gr_day, gr_night)

    configured = [b for (b,) in session.query(BaseConfig.base_name).all()]
    for base in configured:
        rd, rn = rw_day.get(base, 0), rw_night.get(base, 0)
        gd, gn = gr_day.get(base, 0), gr_night.get(base, 0)
        rec = (
            session.query(WeeklyBaseCoverage)
            .filter(
                WeeklyBaseCoverage.week_start == week_start,
                WeeklyBaseCoverage.base_name == base,
            )
            .first()
        )
        if rec is None:
            rec = WeeklyBaseCoverage(week_start=week_start, base_name=base)
            session.add(rec)
        rec.rw_staffed_day, rec.rw_staffed_night = rd, rn
        rec.gr_staffed_day, rec.gr_staffed_night = gd, gn
        rec.rw_staffed_unit_days = rd + rn
        rec.gr_staffed_unit_days = gd + gn

    session.query(WeeklyOpsViewDay).filter(
        WeeklyOpsViewDay.week_start == week_start
    ).delete(synchronize_session=False)
    for (day_s, base), (rw, gr) in sorted(per_day.items()):
        session.add(
            WeeklyOpsViewDay(
                week_start=week_start,
                day_date=day_s,
                base_name=base,
                rw_count=rw,
                gr_count=gr,
            )
        )
    session.flush()
    recompute_week_targets(session, week_start)
