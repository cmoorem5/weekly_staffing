"""Day-level research export built from schedule-import detail.

Three tables, all keyed by calendar day:

* ``daily``   one row per day: seats worked and OT by role and day/night,
              leave, training, plus the CEO-report daily figures when present.
* ``by_base`` one row per day and base: OPS View RW/GR staffed unit-days.
* ``shifts``  one row per person-shift, de-identified by default (a stable
              pseudonymous staff ID instead of a name).

Seat counting matches ``metrics.compute_role_fill``: worked shifts dedupe on
(role, date, source tab, source cell) so a pair-staffed EMT seat counts once,
and the opportunistic extra units in ``metrics.EXTRA_UNIT_CODES`` stay out of
the seat totals (they are still listed in ``shifts``, flagged).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .db import session_scope
from .metrics import EXTRA_UNIT_CODES
from .models import (
    WeeklyDailyDetail,
    WeeklyOpsViewDay,
    WeeklyPersonShift,
)

EXPORT_ROLES = ("RN", "MEDIC", "EMT")
# Start/end by base and day/night (12-hour shifts; Manchester is day only).
# Matches the crew_hub shift skeleton (bmf_staffing/crew_hub/shifts.py).
BASE_SHIFT_TIMES = {
    "Bedford": {"D": ("07:00", "19:00"), "N": ("19:00", "07:00")},
    "Plymouth": {"D": ("07:00", "19:00"), "N": ("19:00", "07:00")},
    "Lawrence": {"D": ("09:00", "21:00"), "N": ("21:00", "09:00")},
    "Mansfield": {"D": ("11:00", "23:00")},
    "Manchester": {"D": ("11:00", "23:00")},
}
# Units whose real hours are not in the skeleton: left blank, not guessed.
UNKNOWN_TIME_UNITS = frozenset({"G9B"})


def shift_window(base: str, day_night: str, unit_code: str = "") -> tuple[str, str]:
    """(start, end) for a shift, or blanks when the hours are not known."""
    if unit_code in UNKNOWN_TIME_UNITS:
        return "", ""
    return BASE_SHIFT_TIMES.get(base, {}).get(day_night, ("", ""))


LEAVE_COLUMNS = ("AT", "LT", "SICK", "LOA", "JURY", "BREV", "OTHER")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

DAILY_HEADER = (
    ["date", "weekday", "week_start", "has_person_detail"]
    + [
        f"{role.lower()}_{kind}_{dn}"
        for role in EXPORT_ROLES
        for kind in ("worked", "ot")
        for dn in ("day", "night")
    ]
    + ["rw_seats_worked", "gr_seats_worked"]
    + [f"leave_{lt.lower()}" for lt in LEAVE_COLUMNS]
    + [
        "leave_total",
        "training",
        "reported_filled",
        "reported_rw",
        "reported_gr",
        "reported_exceptions",
    ]
)
BY_BASE_HEADER = ["date", "weekday", "week_start", "base", "rw_units", "gr_units"]
SHIFT_HEADER = [
    "staff_id",
    "shift_date",
    "weekday",
    "week_start",
    "role",
    "event",
    "base",
    "service_type",
    "day_night",
    "shift_start",
    "shift_end",
    "ends_next_day",
    "unit_code",
    "leave_type",
    "overtime",
    "extra_unit",
]


@dataclass
class DailyExport:
    date_start: str
    date_end: str
    include_names: bool
    daily: list[list] = field(default_factory=list)
    by_base: list[list] = field(default_factory=list)
    shifts: list[list] = field(default_factory=list)
    weeks_with_detail: int = 0
    weeks_in_range: int = 0

    @property
    def shift_header(self) -> list[str]:
        return (["person"] if self.include_names else []) + SHIFT_HEADER


def staff_pseudonym(staff_member_id: int | None, person_display: str) -> str:
    """Stable de-identified label: roster ID when linked, else a name hash."""
    if staff_member_id is not None:
        return f"S{staff_member_id:04d}"
    digest = hashlib.sha1((person_display or "").strip().lower().encode()).hexdigest()
    return f"X{digest[:8]}"


def _leave_bucket(leave_type: str | None, raw_value: str) -> str:
    text = (leave_type or raw_value or "").strip().upper()
    if text.startswith("LT"):
        return "LT"
    if text == "PFML":
        return "LOA"
    for code in LEAVE_COLUMNS[:-1]:
        if text.startswith(code):
            return code
    return "OTHER"


def _week_start_for(day: date) -> str:
    """Sunday on or before ``day`` (schedule weeks run Sunday to Saturday)."""
    return (day - timedelta(days=(day.weekday() + 1) % 7)).isoformat()


def load_daily_export(
    db_path: str | None,
    date_start: date,
    date_end: date,
    *,
    roles: tuple[str, ...] = EXPORT_ROLES,
    include_names: bool = False,
) -> DailyExport:
    start_s, end_s = date_start.isoformat(), date_end.isoformat()
    export = DailyExport(start_s, end_s, include_names)
    with session_scope(db_path) as session:
        shift_rows = (
            session.query(WeeklyPersonShift)
            .filter(
                WeeklyPersonShift.shift_date >= start_s,
                WeeklyPersonShift.shift_date <= end_s,
                WeeklyPersonShift.event_type != "skipped",
                WeeklyPersonShift.role.in_(roles),
            )
            .order_by(
                WeeklyPersonShift.shift_date,
                WeeklyPersonShift.role,
                WeeklyPersonShift.id,
            )
            .all()
        )
        ops_rows = (
            session.query(WeeklyOpsViewDay)
            .filter(
                WeeklyOpsViewDay.day_date >= start_s,
                WeeklyOpsViewDay.day_date <= end_s,
            )
            .order_by(WeeklyOpsViewDay.day_date, WeeklyOpsViewDay.base_name)
            .all()
        )
        reported = {
            r.day_date: r
            for r in session.query(WeeklyDailyDetail).filter(
                WeeklyDailyDetail.day_date >= start_s,
                WeeklyDailyDetail.day_date <= end_s,
            )
        }
        # Detach plain values before the session closes.
        shifts = [
            (
                m.staff_member_id,
                m.person_display,
                m.shift_date,
                m.week_start,
                m.role,
                m.event_type,
                m.base_name or "",
                m.service_type or "",
                m.day_night or "",
                m.unit_code or "",
                m.leave_type,
                m.raw_value or "",
                bool(m.overtime),
                m.included_in_aggregates,
                m.source_tab or "",
                m.source_cell or "",
                m.id,
            )
            for m in shift_rows
        ]
        ops = [(o.day_date, o.base_name, o.rw_count, o.gr_count) for o in ops_rows]
        reported_vals = {
            k: (v.filled, v.rw, v.gr, v.exceptions) for k, v in reported.items()
        }

    detail_weeks = {s[3] for s in shifts}
    seen: set[tuple] = set()
    per_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for (
        staff_id,
        person,
        shift_date,
        week_start,
        role,
        event,
        base,
        service,
        dn,
        unit,
        leave_type,
        raw,
        overtime,
        in_agg,
        tab,
        cell,
        row_id,
    ) in shifts:
        day = date.fromisoformat(shift_date)
        extra = unit in EXTRA_UNIT_CODES
        d = per_day[shift_date]
        if event in ("staffed", "ot") and in_agg == 1 and not extra:
            key = (role, shift_date, tab, cell) if cell else (role, "row", row_id)
            if key not in seen:
                seen.add(key)
                is_ot = event == "ot" or overtime
                kind = "ot" if is_ot else "worked"
                slot = "night" if dn == "N" else "day"
                d[f"{role}_{kind}_{slot}"] += 1
                if not is_ot and service in ("RW", "GR"):
                    d[f"{service.lower()}_seats_worked"] += 1
        elif event == "leave":
            d[f"leave_{_leave_bucket(leave_type, raw).lower()}"] += 1
            d["leave_total"] += 1
        elif event == "training":
            d["training"] += 1

        start_t, end_t = shift_window(base, dn, unit)
        label = event
        if event == "leave":
            label = "leave"
        row = [
            staff_pseudonym(staff_id, person),
            shift_date,
            WEEKDAYS[day.weekday()],
            week_start,
            role,
            label,
            base,
            service,
            dn,
            start_t,
            end_t,
            "Y" if end_t and end_t < start_t else "",
            unit,
            leave_type or "",
            "Y" if overtime or event == "ot" else "",
            "Y" if extra else "",
        ]
        if include_names:
            row.insert(0, person)
        export.shifts.append(row)

    ops_weeks = {_week_start_for(date.fromisoformat(o[0])) for o in ops}
    for day_s, base, rw, gr in ops:
        day = date.fromisoformat(day_s)
        export.by_base.append(
            [day_s, WEEKDAYS[day.weekday()], _week_start_for(day), base, rw, gr]
        )

    day = date_start
    weeks: set[str] = set()
    while day <= date_end:
        day_s = day.isoformat()
        ws = _week_start_for(day)
        weeks.add(ws)
        d = per_day.get(day_s, {})
        rep = reported_vals.get(day_s)
        row: list = [
            day_s,
            WEEKDAYS[day.weekday()],
            ws,
            "Y" if ws in detail_weeks else "",
        ]
        for role in EXPORT_ROLES:
            for kind in ("worked", "ot"):
                for slot in ("day", "night"):
                    row.append(
                        d.get(f"{role}_{kind}_{slot}", 0) if ws in detail_weeks else ""
                    )
        row += [
            d.get("rw_seats_worked", 0) if ws in detail_weeks else "",
            d.get("gr_seats_worked", 0) if ws in detail_weeks else "",
        ]
        for lt in LEAVE_COLUMNS:
            row.append(d.get(f"leave_{lt.lower()}", 0) if ws in detail_weeks else "")
        row.append(d.get("leave_total", 0) if ws in detail_weeks else "")
        row.append(d.get("training", 0) if ws in detail_weeks else "")
        row += list(rep) if rep else ["", "", "", ""]
        export.daily.append(row)
        day += timedelta(days=1)

    export.weeks_in_range = len(weeks)
    export.weeks_with_detail = len(weeks & (detail_weeks | ops_weeks))
    return export


NOTES = (
    "One row per calendar day in the selected range; weeks run Sunday to Saturday.",
    "shift_start/shift_end follow the base: Bedford and Plymouth 07:00-19:00 and "
    "19:00-07:00, Lawrence 09:00-21:00 and 21:00-09:00, Mansfield and Manchester "
    "11:00-23:00 (day only). Hours come from the scheduled base and day/night code, "
    "not from an individual's actual clock-in. Blank = hours not known (unit G9B).",
    "worked = scheduled line seats that are not overtime; ot = overtime seats. "
    "Pair-staffed EMT seats count once. Opportunistic extra units (GR2, NG2, G9B) "
    "are excluded from the seat counts and flagged in the shift detail.",
    "Blank cells mean the week was imported without per-person detail, "
    "not zero. Check has_person_detail before analysing.",
    "reported_* columns are the figures entered in the CEO weekly report for that "
    "day, where available.",
    "staff_id is a stable pseudonym from the staff roster (S####); X######## means "
    "the person was not matched to the roster. IDs are only comparable within this database.",
    "Leave codes: AT, LT, SICK, LOA (PFML included), JURY, BREV, OTHER.",
)
