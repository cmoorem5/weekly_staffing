"""Day-level research export built from schedule-import detail.

Three tables, all keyed by calendar day:

* ``daily``   one row per day: required, worked, OT and open seats by role and
              day/night, plus leave and training.
* ``by_base`` one row per day and base: OPS View RW/GR staffed unit-days.
* ``shifts``  one row per person-shift with start/end time and rest-gap
              columns, de-identified with a random per-download staff ID.

Seat counting matches ``metrics.compute_role_fill``: worked shifts dedupe on
(role, date, source tab, source cell) so a pair-staffed EMT seat counts once,
and the opportunistic extra units in ``metrics.EXTRA_UNIT_CODES`` stay out of
the seat totals (they are still listed in ``shifts``, flagged). Manager rows
are excluded from every table.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .db import session_scope
from .metrics import EXTRA_UNIT_CODES, ROLE_CAPACITY_PER_WEEK
from .models import WeeklyOpsViewDay, WeeklyPersonShift

EXPORT_ROLES = ("RN", "MEDIC", "EMT")
SHIFT_HOURS = 12
# Days of history read before the range so rest gaps and consecutive-day
# counts are right on the first days of the export.
FATIGUE_LOOKBACK_DAYS = 7

# Start/end by base and day/night (12-hour shifts; Manchester is day only).
# Matches the crew_hub shift skeleton (bmf_staffing/crew_hub/shifts.py).
BASE_SHIFT_TIMES = {
    "Bedford": {"D": ("07:00", "19:00"), "N": ("19:00", "07:00")},
    "Plymouth": {"D": ("07:00", "19:00"), "N": ("19:00", "07:00")},
    "Lawrence": {"D": ("09:00", "21:00"), "N": ("21:00", "09:00")},
    "Mansfield": {"D": ("11:00", "23:00")},
    "Manchester": {"D": ("11:00", "23:00")},
}
# Units that keep their own hours instead of the base default. GR2/NG2 follow
# Bedford (0700/1900); G9B is a 0900-2100 day ground unit.
UNIT_SHIFT_TIMES = {"G9B": ("09:00", "21:00")}

# Required seats per day from the crew skeleton: RN and Medic ride the 8 day
# and 4 night crews; EMTs sit on GR/PG/LG/MG by day and NG/NP/NL by night.
# Times 7 these equal metrics.ROLE_CAPACITY_PER_WEEK (84 / 84 / 49).
REQUIRED_SEATS_PER_DAY = {
    "RN": {"day": 8, "night": 4},
    "MEDIC": {"day": 8, "night": 4},
    "EMT": {"day": 4, "night": 3},
}
assert all(
    7 * sum(REQUIRED_SEATS_PER_DAY[r].values()) == ROLE_CAPACITY_PER_WEEK[r]
    for r in EXPORT_ROLES
)

LEAVE_COLUMNS = ("AT", "LT", "SICK", "LOA", "JURY", "BREV", "OTHER")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SLOTS = ("day", "night")
ROLE_MEASURES = ("required", "worked", "ot", "open")


def shift_window(base: str, day_night: str, unit_code: str = "") -> tuple[str, str]:
    """(start, end) for a shift, or blanks when the hours are not known."""
    if unit_code in UNIT_SHIFT_TIMES:
        return UNIT_SHIFT_TIMES[unit_code]
    return BASE_SHIFT_TIMES.get(base, {}).get(day_night, ("", ""))


def daily_header(roles: tuple[str, ...] = EXPORT_ROLES) -> list[str]:
    cols = ["date", "weekday", "week_start", "has_person_detail"]
    for role in roles:
        r = role.lower()
        cols += [f"{r}_{m}_{s}" for m in ROLE_MEASURES for s in SLOTS]
        cols.append(f"{r}_leave")
    cols += [f"leave_{lt.lower()}" for lt in LEAVE_COLUMNS]
    cols += ["leave_total", "training"]
    return cols


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
    "hours_since_prev_shift",
    "consecutive_days",
]
KEY_HEADER = ["staff_id", "name", "roles"]


class Pseudonymizer:
    """Random-per-download staff IDs keyed by a secret salt.

    The same person gets the same ID throughout one export (so fatigue and
    absence can be followed per person), but IDs from two downloads never
    match and cannot be rebuilt from a staff list without the salt. The
    salt is never stored; the key file shipped with the export is the only
    way back to names.
    """

    def __init__(self, salt: bytes):
        self._salt = salt
        self.key: dict[str, tuple[str, set[str]]] = {}

    def __call__(self, staff_member_id: int | None, name: str, role: str) -> str:
        ident = (
            f"id:{staff_member_id}"
            if staff_member_id is not None
            else f"name:{(name or '').strip().lower()}"
        )
        digest = hmac.new(self._salt, ident.encode(), hashlib.sha256).hexdigest()
        pid = f"P{digest[:8].upper()}"
        entry = self.key.setdefault(pid, ((name or "").strip(), set()))
        entry[1].add(role)
        return pid

    def key_rows(self) -> list[list[str]]:
        return [
            [pid, name, "/".join(sorted(roles))]
            for pid, (name, roles) in sorted(self.key.items(), key=lambda kv: kv[1][0])
        ]


@dataclass
class DailyExport:
    date_start: str
    date_end: str
    roles: tuple[str, ...]
    daily: list[list] = field(default_factory=list)
    by_base: list[list] = field(default_factory=list)
    shifts: list[list] = field(default_factory=list)
    key: list[list[str]] = field(default_factory=list)
    weeks_with_detail: int = 0
    weeks_in_range: int = 0

    @property
    def daily_header(self) -> list[str]:
        return daily_header(self.roles)


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


def _start_dt(day: date, start: str) -> datetime | None:
    if not start:
        return None
    hh, mm = start.split(":")
    return datetime(day.year, day.month, day.day, int(hh), int(mm))


def _person_key(staff_id: int | None, person: str) -> tuple:
    if staff_id is not None:
        return ("i", f"{staff_id:010d}")
    return ("n", person.strip().lower())


def _fatigue_by_row(shifts: list[tuple]) -> dict[int, tuple]:
    """row id -> (hours since previous worked shift, consecutive worked days).

    Walks each person's worked shifts in start-time order, so a night shift
    is measured against the day shift before it regardless of row order.
    """
    work = []
    for s in shifts:
        staff_id, person, shift_date, _ws, _role, event, base, _svc, dn, unit = s[:10]
        if event not in ("staffed", "ot"):
            continue
        day = date.fromisoformat(shift_date)
        begin = _start_dt(day, shift_window(base, dn, unit)[0])
        work.append((_person_key(staff_id, person), day, begin, s[16]))
    work.sort(key=lambda w: (w[0], w[1], w[2] or datetime.max))

    out: dict[int, tuple] = {}
    prev_key = prev_day = prev_end = None
    run = 0
    for pkey, day, begin, row_id in work:
        if pkey != prev_key:
            prev_day = prev_end = None
            run = 0
        if day != prev_day:
            run = run + 1 if prev_day == day - timedelta(days=1) else 1
        gap = ""
        if begin is not None:
            if prev_end is not None:
                gap = round((begin - prev_end).total_seconds() / 3600, 1)
            prev_end = begin + timedelta(hours=SHIFT_HOURS)
        out[row_id] = (gap, run)
        prev_key, prev_day = pkey, day
    return out


def load_daily_export(
    db_path: str | None,
    date_start: date,
    date_end: date,
    *,
    salt: bytes,
    roles: tuple[str, ...] = EXPORT_ROLES,
) -> DailyExport:
    start_s, end_s = date_start.isoformat(), date_end.isoformat()
    lookback_s = (date_start - timedelta(days=FATIGUE_LOOKBACK_DAYS)).isoformat()
    roles = tuple(r for r in EXPORT_ROLES if r in roles)
    export = DailyExport(start_s, end_s, roles)
    pseudo = Pseudonymizer(salt)

    with session_scope(db_path) as session:
        shifts = [
            (
                m.staff_member_id,
                m.person_display or "",
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
            for m in session.query(WeeklyPersonShift)
            .filter(
                WeeklyPersonShift.shift_date >= lookback_s,
                WeeklyPersonShift.shift_date <= end_s,
                WeeklyPersonShift.event_type != "skipped",
                WeeklyPersonShift.is_manager_row == 0,
                WeeklyPersonShift.role.in_(roles),
            )
            .order_by(
                WeeklyPersonShift.shift_date,
                WeeklyPersonShift.role,
                WeeklyPersonShift.id,
            )
        ]
        ops = [
            (o.day_date, o.base_name, o.rw_count, o.gr_count)
            for o in session.query(WeeklyOpsViewDay)
            .filter(
                WeeklyOpsViewDay.day_date >= start_s,
                WeeklyOpsViewDay.day_date <= end_s,
            )
            .order_by(WeeklyOpsViewDay.day_date, WeeklyOpsViewDay.base_name)
        ]

    detail_weeks = {s[3] for s in shifts if s[2] >= start_s}
    fatigue = _fatigue_by_row(shifts)
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
        in_range = shift_date >= start_s
        extra = unit in EXTRA_UNIT_CODES
        is_work = event in ("staffed", "ot")
        start_t, end_t = shift_window(base, dn, unit)

        gap, consec = fatigue.get(row_id, ("", ""))
        if not in_range:
            continue

        d = per_day[shift_date]
        if is_work and in_agg == 1 and not extra:
            key = (role, shift_date, tab, cell) if cell else (role, "row", row_id)
            if key not in seen:
                seen.add(key)
                kind = "ot" if event == "ot" or overtime else "worked"
                slot = "night" if dn == "N" else "day"
                d[f"{role}_{kind}_{slot}"] += 1
        elif event == "leave":
            d[f"leave_{_leave_bucket(leave_type, raw).lower()}"] += 1
            d[f"{role}_leave"] += 1
            d["leave_total"] += 1
        elif event == "training":
            d["training"] += 1

        export.shifts.append(
            [
                pseudo(staff_id, person, role),
                shift_date,
                WEEKDAYS[day.weekday()],
                week_start,
                role,
                event,
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
                gap,
                consec,
            ]
        )

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
        has = ws in detail_weeks
        d = per_day.get(day_s, {})
        row: list = [day_s, WEEKDAYS[day.weekday()], ws, "Y" if has else ""]
        for role in roles:
            for measure in ROLE_MEASURES:
                for slot in SLOTS:
                    if not has:
                        row.append("")
                        continue
                    required = REQUIRED_SEATS_PER_DAY[role][slot]
                    filled = d.get(f"{role}_worked_{slot}", 0) + d.get(
                        f"{role}_ot_{slot}", 0
                    )
                    row.append(
                        {
                            "required": required,
                            "worked": d.get(f"{role}_worked_{slot}", 0),
                            "ot": d.get(f"{role}_ot_{slot}", 0),
                            "open": max(required - filled, 0),
                        }[measure]
                    )
            row.append(d.get(f"{role}_leave", 0) if has else "")
        for lt in LEAVE_COLUMNS:
            row.append(d.get(f"leave_{lt.lower()}", 0) if has else "")
        row.append(d.get("leave_total", 0) if has else "")
        row.append(d.get("training", 0) if has else "")
        export.daily.append(row)
        day += timedelta(days=1)

    export.weeks_in_range = len(weeks)
    export.weeks_with_detail = len(weeks & detail_weeks)
    export.key = pseudo.key_rows()
    return export


NOTES = (
    "One row per calendar day in the selected range; weeks run Sunday to Saturday.",
    "Blank cells mean the week was imported without per-person detail, not zero. "
    "Filter on has_person_detail = Y before analysing.",
    "Seat columns (required, worked, ot, open) count seats: a seat staffed by two "
    "EMT partners counts once. Leave and training columns count people.",
    "required = scheduled seats per day: RN and Medic 8 day / 4 night, EMT 4 day / "
    "3 night. worked = seats filled on regular time; ot = seats filled on overtime; "
    "open = required minus (worked + ot), never below zero.",
    "Opportunistic extra units (GR2, NG2, G9B) are left out of seat counts and "
    "flagged extra_unit = Y in Shift detail. Managers working line shifts are "
    "excluded from every sheet. Pilots are not included.",
    "shift_start / shift_end are the scheduled hours for the base: Bedford and "
    "Plymouth 07:00-19:00 and 19:00-07:00, Lawrence 09:00-21:00 and 21:00-09:00, "
    "Mansfield and Manchester 11:00-23:00 (day only), unit G9B 09:00-21:00. They "
    "are not clock-in times. Leave and training rows have no base, so no times.",
    "hours_since_prev_shift = hours from the end of the person's previous worked "
    "shift to the start of this one; consecutive_days = worked days in a row "
    "ending on this date. Both look back 7 days before the range and are blank "
    "on leave and training rows.",
    "staff_id is a random code generated for this download only. The same person "
    "has the same code throughout this file, but codes from other downloads will "
    "not match. The key linking codes to names is a separate file.",
    "Leave codes: AT, LT, SICK, LOA (PFML included), JURY, BREV, OTHER.",
)
