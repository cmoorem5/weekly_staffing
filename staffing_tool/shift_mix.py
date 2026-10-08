"""
Per-person shift mix and CBA requirement tracking (RN and Medic).

Built from ``WeeklyPersonShift`` rows. For one person over a date range it reports:

* day / night, RW / GR, and per-base mix of *worked* shifts
* OT, training, and leave shown separately (none of them count as worked)
* progress against the weekly line requirement (3 shifts a week)
* fixed 6-week blocks, each checked against the night requirement (set by years
  of service at block start) and the weekend requirement (5 weekend shifts)

Worked means ``event_type == "staffed"``: a line shift that is not overtime.
Weekend shifts run from Friday night through Sunday night, so the five slots
are Fri N, Sat D, Sat N, Sun D, Sun N.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import distinct

from .db import session_scope
from .models import WeeklyPersonShift
from .person_ops import PersonOpsRow, load_person_ops_detail
from .staff_roster import roster_entry_for_display

WEEKLY_SHIFT_TARGET = 3
BLOCK_WEEKS = 6
BLOCK_DAYS = BLOCK_WEEKS * 7
WEEKEND_SHIFTS_PER_BLOCK = 5
BLOCK_SHIFT_TARGET = WEEKLY_SHIFT_TARGET * BLOCK_WEEKS

# (years of service below this value, nights required per 6-week block).
# A tier's lower bound is inclusive, so exactly 3.0 years falls in the 7 tier.
NIGHT_REQUIREMENT_TIERS: tuple[tuple[float, int], ...] = (
    (3, 8),
    (8, 7),
    (11, 6),
    (14, 5),
    (17, 2),
    (20, 1),
)
NIGHT_REQUIREMENT_20_PLUS = 0

# Display order for leave types; anything unrecognized lands in "Other".
LEAVE_ORDER: tuple[str, ...] = (
    "LT-D",
    "LT-N",
    "LT",
    "SICK",
    "LOA",
    "PFML",
    "AT",
)

ELIGIBLE_ROLES = ("RN", "MEDIC")


def night_requirement_table() -> list[tuple[str, int]]:
    """Human-readable tiers for display: [("0-3", 8), ..., ("20+", 0)]."""
    rows: list[tuple[str, int]] = []
    low = 0.0
    for upper, nights in NIGHT_REQUIREMENT_TIERS:
        rows.append((f"{low:g}-{upper:g}", nights))
        low = upper
    rows.append((f"{low:g}+", NIGHT_REQUIREMENT_20_PLUS))
    return rows


def years_of_service(hire_date: date, as_of: date) -> float:
    """Completed years plus the fraction of the current year."""
    if as_of <= hire_date:
        return 0.0
    whole = as_of.year - hire_date.year
    try:
        anniversary = hire_date.replace(year=hire_date.year + whole)
    except ValueError:  # Feb 29 hire date in a non-leap year
        anniversary = hire_date.replace(year=hire_date.year + whole, day=28)
    if anniversary > as_of:
        whole -= 1
        try:
            anniversary = hire_date.replace(year=hire_date.year + whole)
        except ValueError:
            anniversary = hire_date.replace(year=hire_date.year + whole, day=28)
    try:
        next_anniv = hire_date.replace(year=hire_date.year + whole + 1)
    except ValueError:
        next_anniv = hire_date.replace(year=hire_date.year + whole + 1, day=28)
    span = (next_anniv - anniversary).days or 365
    return whole + (as_of - anniversary).days / span


def required_nights(years: float) -> int:
    """Nights required per 6-week block for the given years of service."""
    for upper, nights in NIGHT_REQUIREMENT_TIERS:
        if years < upper:
            return nights
    return NIGHT_REQUIREMENT_20_PLUS


def week_start_sunday(d: date) -> date:
    """Sunday on or before ``d`` (schedule weeks run Sunday to Saturday)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def is_weekend_shift(shift_date: date, day_night: str) -> bool:
    """Friday night through Sunday night."""
    wd = shift_date.weekday()
    if wd in (5, 6):
        return True
    return wd == 4 and (day_night or "").upper() == "N"


def leave_label(row: PersonOpsRow) -> str:
    raw = (row.leave_type or row.raw_value or "").strip().upper()
    return raw if raw in LEAVE_ORDER else "Other"


def _pct(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round(100.0 * part / whole, 1)


def parse_iso_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@dataclass
class MixCounts:
    day: int = 0
    night: int = 0
    rw: int = 0
    gr: int = 0

    @property
    def total(self) -> int:
        return self.day + self.night


@dataclass
class BaseMix:
    base: str
    service_type: str
    day: int = 0
    night: int = 0

    @property
    def total(self) -> int:
        return self.day + self.night


@dataclass
class BlockResult:
    index: int
    start: date
    end: date
    worked: int
    nights: int
    weekend: int
    required_nights: int | None
    complete: bool
    in_progress: bool
    missing_weeks: int
    years: float | None
    ot: int = 0

    @property
    def target_shifts(self) -> int:
        return BLOCK_SHIFT_TARGET

    @property
    def nights_delta(self) -> int | None:
        if self.required_nights is None:
            return None
        return self.nights - self.required_nights

    @property
    def weekend_delta(self) -> int:
        return self.weekend - WEEKEND_SHIFTS_PER_BLOCK

    @property
    def nights_met(self) -> bool | None:
        if self.required_nights is None:
            return None
        return self.nights >= self.required_nights

    @property
    def weekend_met(self) -> bool:
        return self.weekend >= WEEKEND_SHIFTS_PER_BLOCK

    @property
    def status(self) -> str:
        if not self.complete:
            return "In progress" if self.in_progress else "Incomplete data"
        checks = [self.weekend_met]
        if self.nights_met is not None:
            checks.append(self.nights_met)
        return "Met" if all(checks) else "Short"


@dataclass
class ShiftMixReport:
    person: str
    role: str
    hire_date: date | None
    range_start: date
    range_end: date
    anchor: date
    data_weeks: int
    weeks_equivalent: float
    missing_weeks: list[date]
    worked: MixCounts
    expected_shifts: float
    by_base: list[BaseMix]
    ot: MixCounts
    training: int
    leave_counts: dict[str, int]
    leave_total: int
    blocks: list[BlockResult] = field(default_factory=list)

    @property
    def day_pct(self) -> float | None:
        return _pct(self.worked.day, self.worked.total)

    @property
    def night_pct(self) -> float | None:
        return _pct(self.worked.night, self.worked.total)

    @property
    def rw_pct(self) -> float | None:
        return _pct(self.worked.rw, self.worked.rw + self.worked.gr)

    @property
    def gr_pct(self) -> float | None:
        return _pct(self.worked.gr, self.worked.rw + self.worked.gr)

    @property
    def target_pct(self) -> float | None:
        if self.expected_shifts <= 0:
            return None
        return round(100.0 * self.worked.total / self.expected_shifts, 1)

    @property
    def scored_blocks(self) -> list[BlockResult]:
        return [b for b in self.blocks if b.complete]

    @property
    def nights_required_total(self) -> int:
        return sum(b.required_nights or 0 for b in self.scored_blocks)

    @property
    def nights_worked_total(self) -> int:
        return sum(
            b.nights for b in self.scored_blocks if b.required_nights is not None
        )

    @property
    def nights_pct(self) -> float | None:
        return _pct(self.nights_worked_total, self.nights_required_total)

    @property
    def weekend_required_total(self) -> int:
        return WEEKEND_SHIFTS_PER_BLOCK * len(self.scored_blocks)

    @property
    def weekend_worked_total(self) -> int:
        return sum(b.weekend for b in self.scored_blocks)

    @property
    def weekend_pct(self) -> float | None:
        return _pct(self.weekend_worked_total, self.weekend_required_total)

    @property
    def blocks_met(self) -> int:
        return sum(1 for b in self.scored_blocks if b.status == "Met")


def default_block_anchor(range_start: date) -> date:
    """Week 1 Sunday of the fiscal year containing ``range_start``."""
    from .fiscal_year import fy_week1_sunday_containing

    return fy_week1_sunday_containing(range_start)


def block_windows(
    anchor: date, range_start: date, range_end: date
) -> list[tuple[int, date, date]]:
    """Fixed 6-week blocks (anchored on ``anchor``) overlapping the range."""
    anchor = week_start_sunday(anchor)
    first = (range_start - anchor).days // BLOCK_DAYS
    last = (range_end - anchor).days // BLOCK_DAYS
    windows = []
    for k in range(first, last + 1):
        start = anchor + timedelta(days=k * BLOCK_DAYS)
        windows.append((k, start, start + timedelta(days=BLOCK_DAYS - 1)))
    return windows


def analysis_window(
    anchor: date, range_start: date, range_end: date
) -> tuple[date, date]:
    """Date span the loader must cover: the range widened to whole blocks."""
    windows = block_windows(anchor, range_start, range_end)
    return windows[0][1], windows[-1][2]


def build_shift_mix(
    rows: Iterable[PersonOpsRow],
    *,
    person: str,
    role: str,
    hire_date: date | None,
    range_start: date,
    range_end: date,
    anchor: date,
    data_week_starts: set[date],
    today: date | None = None,
) -> ShiftMixReport:
    """Pure calculation; ``rows`` may extend beyond the range to cover blocks."""
    today = today or date.today()
    rows = list(rows)

    # Weeks (Sunday starts) overlapping the range, and how many have import data.
    weeks_equiv = 0.0
    data_weeks = 0
    missing: list[date] = []
    wk = week_start_sunday(range_start)
    while wk <= range_end:
        overlap_start = max(wk, range_start)
        overlap_end = min(wk + timedelta(days=6), range_end)
        overlap = (overlap_end - overlap_start).days + 1
        if wk in data_week_starts:
            data_weeks += 1
            weeks_equiv += overlap / 7
        else:
            missing.append(wk)
        wk += timedelta(days=7)

    worked = MixCounts()
    ot = MixCounts()
    training = 0
    leave: Counter[str] = Counter()
    base_mix: dict[tuple[str, str], BaseMix] = {}

    def tally(target: MixCounts, row: PersonOpsRow) -> None:
        if (row.day_night or "D").upper() == "N":
            target.night += 1
        else:
            target.day += 1
        if row.service_type == "RW":
            target.rw += 1
        elif row.service_type == "GR":
            target.gr += 1

    in_range: list[PersonOpsRow] = []
    for row in rows:
        d = parse_iso_date(row.shift_date)
        if d is None or d < range_start or d > range_end:
            continue
        in_range.append(row)
    for row in in_range:
        if row.event_type == "staffed":
            tally(worked, row)
            key = (row.base_name or "Unknown", row.service_type or "")
            mix = base_mix.setdefault(key, BaseMix(key[0], key[1]))
            if (row.day_night or "D").upper() == "N":
                mix.night += 1
            else:
                mix.day += 1
        elif row.event_type == "ot":
            tally(ot, row)
        elif row.event_type == "training":
            training += 1
        elif row.event_type == "leave":
            leave[leave_label(row)] += 1

    leave_counts: dict[str, int] = {}
    for label in (*LEAVE_ORDER, "Other"):
        if leave.get(label):
            leave_counts[label] = leave[label]

    # Blocks use every loaded row, not just those inside the selected range.
    by_block: dict[int, list[PersonOpsRow]] = defaultdict(list)
    windows = block_windows(anchor, range_start, range_end)
    anchor_sun = week_start_sunday(anchor)
    for row in rows:
        d = parse_iso_date(row.shift_date)
        if d is None:
            continue
        by_block[(d - anchor_sun).days // BLOCK_DAYS].append(row)

    blocks: list[BlockResult] = []
    for idx, start, end in windows:
        block_rows = by_block.get(idx, [])
        block_weeks = [start + timedelta(days=7 * i) for i in range(BLOCK_WEEKS)]
        missing_weeks = sum(1 for w in block_weeks if w not in data_week_starts)
        years = years_of_service(hire_date, start) if hire_date else None
        nights = weekend = n_worked = n_ot = 0
        for row in block_rows:
            if row.event_type == "ot":
                n_ot += 1
                continue
            if row.event_type != "staffed":
                continue
            n_worked += 1
            d = parse_iso_date(row.shift_date)
            is_night = (row.day_night or "D").upper() == "N"
            if is_night:
                nights += 1
            if d is not None and is_weekend_shift(d, row.day_night):
                weekend += 1
        blocks.append(
            BlockResult(
                index=idx,
                start=start,
                end=end,
                worked=n_worked,
                nights=nights,
                weekend=weekend,
                required_nights=required_nights(years) if years is not None else None,
                complete=missing_weeks == 0 and end <= today,
                in_progress=end > today,
                missing_weeks=missing_weeks,
                years=years,
                ot=n_ot,
            )
        )

    return ShiftMixReport(
        person=person,
        role=role,
        hire_date=hire_date,
        range_start=range_start,
        range_end=range_end,
        anchor=week_start_sunday(anchor),
        data_weeks=data_weeks,
        weeks_equivalent=round(weeks_equiv, 2),
        missing_weeks=missing,
        worked=worked,
        expected_shifts=round(WEEKLY_SHIFT_TARGET * weeks_equiv, 1),
        by_base=sorted(base_mix.values(), key=lambda b: (-b.total, b.base)),
        ot=ot,
        training=training,
        leave_counts=leave_counts,
        leave_total=sum(leave_counts.values()),
        blocks=blocks,
    )


def load_data_week_starts(db_path: str | None, start: date, end: date) -> set[date]:
    """Sunday week starts with any per-person import data in the window."""
    lo = (start - timedelta(days=7)).isoformat()
    hi = end.isoformat()
    with session_scope(db_path) as session:
        raw = (
            session.query(distinct(WeeklyPersonShift.week_start))
            .filter(
                WeeklyPersonShift.week_start >= lo,
                WeeklyPersonShift.week_start <= hi,
            )
            .all()
        )
    weeks: set[date] = set()
    for (value,) in raw:
        d = parse_iso_date(value)
        if d is not None:
            weeks.add(d)
    return weeks


def roster_hire_date(
    db_path: str | None, person: str, role: str | None
) -> tuple[date | None, str]:
    """(hire date, roster role) for a person, or (None, role or "")."""
    with session_scope(db_path) as session:
        entry = roster_entry_for_display(session, person, role=role or None)
        if entry is None:
            return None, role or ""
        return parse_iso_date(entry.hire_date), entry.role


def load_shift_mix(
    db_path: str | None,
    person: str,
    range_start: date,
    range_end: date,
    *,
    role: str | None = None,
    anchor: date | None = None,
    today: date | None = None,
) -> ShiftMixReport:
    """Load a person's rows (widened to whole blocks) and build the report."""
    anchor = anchor or default_block_anchor(range_start)
    win_start, win_end = analysis_window(anchor, range_start, range_end)
    hire, resolved_role = roster_hire_date(db_path, person, role)
    rows = load_person_ops_detail(
        db_path, person, win_start, win_end, role=role or None
    )
    weeks = load_data_week_starts(db_path, win_start, win_end)
    return build_shift_mix(
        rows,
        person=person,
        role=resolved_role,
        hire_date=hire,
        range_start=range_start,
        range_end=range_end,
        anchor=anchor,
        data_week_starts=weeks,
        today=today,
    )
