"""Dataclasses and type aliases shared by the schedule-import pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

Role = str  # "RN", "MEDIC", "EMT", "PILOT"
ServiceType = str  # "RW" or "GR"
DayNight = str  # "D" or "N"
SkipReason = (
    str  # training, open, admin, ignored_unit, schedule_row, manager_row, retired_unit
)


@dataclass
class ShiftRecord:
    """Single shift cell after parsing."""

    date: date
    base: str
    service_type: ServiceType
    day_night: DayNight
    role: Role
    filled: bool
    overtime: bool
    leave_type: str | None
    source_tab: str
    source_cell: str
    raw_value: str
    # Canonical unit (e.g. D7B) for staffed cells; separates multiple aircraft same base/day.
    unit_code: str = ""
    # Row identity from schedule grid (cols A–B); used for manager line-shift tracking.
    person_display: str = ""
    # All people on this grid row (EMT pairs); person_display is the first entry.
    person_displays: tuple[str, ...] = ()
    is_manager_row: bool = False
    skip_reason: SkipReason | None = None
    included_in_aggregates: bool = True
    # Manager-only events persisted to weekly_manager_shifts (e.g. aoc).
    manager_event_type: str | None = None
    excel_row: int = 0
    excel_col: int = 0


@dataclass
class ParseIssue:
    """Something the parser could not confidently map."""

    sheet: str
    cell: str
    raw_value: str
    issue_type: str  # e.g. "unknown_unit", "unknown_leave"
    message: str


def _shift_record_persons(rec: ShiftRecord) -> tuple[str, ...]:
    if rec.person_displays:
        return rec.person_displays
    person = (rec.person_display or "").strip()
    return (person,) if person else ()


# --- Aggregation into weekly metrics ---------------------------------------


@dataclass
class DailyDetailDay:
    """One row of the weekly report daily detail table."""

    day_date: date
    filled: int
    rw: int
    gr: int
    exceptions: int


@dataclass
class AggregatedWeek:
    """Aggregated metrics for one week from shift records."""

    week_start: str
    filled_day: int
    filled_night: int
    ot_rn_day: int
    ot_rn_night: int
    ot_medic_day: int
    ot_medic_night: int
    ot_emt_day: int
    ot_emt_night: int
    leave_at: int
    leave_lt: int
    leave_sick: int
    leave_loa: int
    leave_jury: int
    leave_brev: int
    # Training/education shift count (EDU, CCT, Neo Sim, Clinical/PER, SM/SIM, ...).
    training_total: int
    # (role, leave_type) -> count for grid (RN/Medic/EMT/Pilot × leave types).
    leave_breakdown: dict[tuple[str, str], int]
    base_rw_staffed: dict[str, int]
    base_gr_staffed: dict[str, int]
    base_rw_staffed_day: dict[str, int]
    base_rw_staffed_night: dict[str, int]
    base_gr_staffed_day: dict[str, int]
    base_gr_staffed_night: dict[str, int]
    daily_detail: list[DailyDetailDay]


@dataclass
class OpsViewDayBase:
    """OPS View staffed unit-days for one base on one calendar day."""

    day_date: date
    base_name: str
    rw_count: int
    gr_count: int


@dataclass
class OpsViewAssignment:
    """OPS View name in one unit/role cell for a calendar day."""

    day_date: date
    unit_code: str
    role: str
    excel_row: int
    person_display: str
    raw_value: str
    is_staffed: bool
