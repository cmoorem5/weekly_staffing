"""
Schedule importer for Boston MedFlight weekly staffing.

Goal:
- Parse the RN, Medic, and EMT schedule tabs from the Excel workbook
  into a normalized shift record schema that we can aggregate into
  WeeklyStaffing + WeeklyBaseCoverage.

This module focuses on parsing/normalization only. UI (Django upload +
preview) and DB writes live in the caller; they pass the workbook path
in and consume the parsed data + issues.

NOTE: This is intentionally conservative. Unknown or malformed codes
are not treated as staffed shifts; instead they are surfaced as
ParseIssue instances so the UI can present them for correction.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from .manager_roster import default_manager_last_names_upper
from .person_names import person_displays_for_role
from .staff_roster import (
    StaffRosterMatchIndex,
    canonical_display,
    match_parsed_person_to_roster,
)

PARSER_VERSION = "2"

Role = str  # "RN", "MEDIC", "EMT", "PILOT"
ServiceType = str  # "RW" or "GR"
DayNight = str  # "D" or "N"
SkipReason = (
    str  # training, open, admin, ignored_unit, schedule_row, manager_row, retired_unit
)

# Max non-empty cells archived per week (SQLite-friendly).
RAW_CELL_ARCHIVE_LIMIT = 5000


@contextmanager
def _open_workbook(path: str, wb: Workbook | None = None) -> Iterator[Workbook]:
    """Yield a workbook, reusing ``wb`` when provided instead of re-reading.

    A single schedule import parses the same file several times (records,
    OPS View daily/detail, raw-cell archive). Passing one already-loaded
    workbook through those passes avoids 3-4 redundant full-file reads.
    When ``wb`` is given the caller owns its lifecycle, so it is left open;
    otherwise the workbook opened here is closed on exit.
    """
    if wb is not None:
        yield wb
        return
    opened = load_workbook(path, data_only=True)
    try:
        yield opened
    finally:
        opened.close()


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


# --- Unit mapping -------------------------------------------------------

# Map from canonical unit code (no suffixes like 'c' / 'p') to
# (base_name, service_type, day_night).
#
# Base assignment: Bedford = D7B, N7B, GR, NG; Mansfield = D11M, MG;
# Lawrence = D9L, N9L, LG; Manchester = D11H; Plymouth = D7P, N7P, PG, NP.
UNIT_MAP: dict[str, tuple[str, ServiceType, DayNight]] = {
    # Bedford: D7B, N7B, GR, NG
    "D7B": ("Bedford", "RW", "D"),
    "N7B": ("Bedford", "RW", "N"),
    "GR": ("Bedford", "GR", "D"),
    "NG": ("Bedford", "GR", "N"),
    # Lawrence: D9L, N9L, LG
    "D9L": ("Lawrence", "RW", "D"),
    "N9L": ("Lawrence", "RW", "N"),
    "LG": ("Lawrence", "GR", "D"),
    # Mansfield: D11M, MG
    "D11M": ("Mansfield", "RW", "D"),
    "MG": ("Mansfield", "GR", "D"),
    # Plymouth: D7P, N7P, PG, NP
    "D7P": ("Plymouth", "RW", "D"),
    "N7P": ("Plymouth", "RW", "N"),
    "PG": ("Plymouth", "GR", "D"),
    "NP": ("Plymouth", "GR", "N"),
    # Manchester: D11H
    "D11H": ("Manchester", "RW", "D"),
    # EMT GR shorthand (Bedford ground, aligns with D7B EMT staffing)
    "GR2": ("Bedford", "GR", "D"),
    "NG2": ("Bedford", "GR", "N"),
}

# Historical Manchester codes → canonical D11H (same base, RW day as today).
# raw_value keeps the original cell text; unit_code is canonical for aggregates.
LEGACY_UNIT_ALIASES: dict[str, str] = {
    "D9P": "D11H",
    "D9B": "D11H",
    "D11B": "D11H",
}

# Retired units: skip staffed parse; excluded from CEO aggregates.
RETIRED_UNIT_CODES: frozenset[str] = frozenset({"FW"})

# Max RW/GR staffed unit-days per base per week (cap OPS View counts to these).
# Bedford, Plymouth, Lawrence: 14 RW; Bedford: 14 GR;
# Mansfield, Lawrence, Plymouth: 7 GR.
MAX_RW_UNIT_DAYS_PER_WEEK: dict[str, int] = {
    "Bedford": 14,
    "Plymouth": 14,
    "Lawrence": 14,
    "Mansfield": 7,
    "Manchester": 7,
}
MAX_GR_UNIT_DAYS_PER_WEEK: dict[str, int] = {
    "Bedford": 14,
    "Mansfield": 7,
    "Lawrence": 7,
    "Plymouth": 7,
}

# Absence/exception cell values only — not roles (RN/Medic/EMT are row types, not leave codes).
LEAVE_CODES = {"AT", "LT", "SICK", "LOA", "PFML", "JURY", "BREV"}

# Raw values that count as AT for leave/exception totals.
AT_ALIASES: set[str] = {"SM/AT", "AT/SIM"}

# Unit-like codes to skip when parsing: no shift record, no unknown-unit issue.
IGNORE_UNIT_CODES: set[str] = {
    "ULTRASOUND",
    "RAL D7B",
    "RTW ADMIN",
    "RTW D7B",
}

# Training / admin / manager markers: skip cell entirely
# (no staffed, no leave, no issue).
# RN & Medic / EMT grids end with an "OPEN" row (unfilled units per day) and
# an "EXTRA" row (float staff names per day), followed by footer/notes rows.
# None of that is a person's schedule, so once we hit OPEN or EXTRA in a
# row's name column, that row and everything below it (within the block) is
# skipped. Detected by label text rather than a fixed row number: adding
# staff above these rows pushes them down the sheet every time a roster
# grows, so a hardcoded row number silently goes stale and lets the OPEN/
# EXTRA row's contents (unit-code lists, staff names) leak into the
# "unknown unit codes" review as if they were shift codes.
NON_PERSON_ROW_LABELS = frozenset({"OPEN", "EXTRA"})
_SCHEDULE_COL_B = 2
_SCHEDULE_COL_P = 16


def _find_non_person_skip_row(
    ws: Worksheet, first_row_idx: int, last_row_idx: int
) -> int | None:
    """First row in range labeled OPEN or EXTRA in its name column (A or B).

    Everything from that row through the end of the block is summary/
    footer content, not a person's schedule.
    """
    for row_idx in range(first_row_idx, last_row_idx + 1):
        raw_a, raw_b = _grid_name_cells(ws, row_idx)
        label = (raw_a or raw_b or "").strip().upper()
        if label in NON_PERSON_ROW_LABELS:
            return row_idx
    return None


SKIP_CELL_VALUES: set[str] = {
    "AOC",
    "SM",
    "SIM",
    "CLINICAL",
    "FLOAT",
    "AIRWAY SIM",
    "LTM",
    "MIL",
    "SM (LIVE)",
    "SM (VIRTUAL)",
    "SM(LIVE)",  # Excel sometimes drops space before (
    "SM(VIRTUAL)",
    "EDU",
    "CCT",
    "NEO SIM",
    "CLINICAL/PER",
    "CLINICAL/ PER",  # Excel sometimes has a space after the slash
}

# Training/education markers: not staffing, not leave -- counted separately
# in WeeklyStaffing.training_shifts (weekly total across all these codes).
SKIP_TRAINING_VALUES: set[str] = {
    "SM",
    "SIM",
    "AIRWAY SIM",
    "SM (LIVE)",
    "SM (VIRTUAL)",
    "SM(LIVE)",
    "SM(VIRTUAL)",
    "EDU",
    "CCT",
    "NEO SIM",
    "CLINICAL/PER",
    "CLINICAL/ PER",
}

SKIP_ADMIN_VALUES: set[str] = {
    "AOC",
    "CLINICAL",
    "FLOAT",
    "LTM",
    "MIL",
}

# Merge rules: apply to ALL unit codes (D7B, N7B, D9L, D11M, D7P, N7P, etc.).
# - OT: trailing "C" or " C" on any known unit → overtime.
#   E.g. D7PC, N7BC, D9LC, D7P C.
# - Leave: "UNIT/LEAVETYPE" → that leave type.
#   E.g. D7B/LT, N7P/SICK, D9L/LOA, D11M/JURY.
OT_SUFFIXES: set[str] = {"C", " C"}
# UNIT/LEAVETYPE patterns: suffix after / maps to leave_type for the grid.
UNIT_LEAVE_MERGE: dict[str, str] = {
    "AT": "AT",
    "SM/AT": "AT",
    "LT": "LT",
    "4LT": "LT",  # e.g. D11M/4LT → LT
    "LT-D": "LT-D",
    "LT-N": "LT-N",
    "SICK": "SICK",
    "LOA": "LOA",
    "PFML": "LOA",
    "JURY": "JURY",
    "BREV": "BREV",
}

# "Little c" variants that mean OT → normalize to C before parsing
# (D7Pᶜ, D7Pç → D7PC)
_OT_C_VARIANTS = ("\u1d9c", "\u0368", "\u00e7", "\u00c7")  # ᶜ, ͨ, ç, Ç


def _normalize_cell_value(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        s = raw.strip().upper()
    else:
        s = str(raw).strip().upper()
    for variant in _OT_C_VARIANTS:
        s = s.replace(variant, "C")
    return s


def _classify_skip_reason(
    text: str, training_values: frozenset[str] | set[str] = SKIP_TRAINING_VALUES
) -> SkipReason:
    """Map a skipped cell value to a persistence skip_reason.

    training_values: SKIP_TRAINING_VALUES plus any admin-added training
    codes (Settings > Training codes) for this parse call.
    """
    if text == "OPEN":
        return "open"
    if text in training_values:
        return "training"
    if text in SKIP_ADMIN_VALUES or text in IGNORE_UNIT_CODES:
        return "admin"
    return "admin"


def _append_skipped_shift(
    records: list[ShiftRecord],
    *,
    ws: Worksheet,
    row_idx: int,
    col_idx: int,
    d: date,
    role: Role,
    sheet_label: str,
    text: str,
    person_displays: tuple[str, ...],
    person_display: str,
    is_manager_row: bool,
    skip_reason: SkipReason,
) -> None:
    cell_ref = f"{get_column_letter(col_idx)}{row_idx}"
    # Training is the one skip category that still counts toward a weekly
    # total (WeeklyStaffing.training_shifts) -- everything else here is
    # truly dropped. Manager-row cells are excluded, matching how manager
    # leave/OT are tracked separately (weekly_manager_shifts) rather than
    # folded into the staff weekly totals.
    included_in_aggregates = skip_reason == "training" and not is_manager_row
    records.append(
        ShiftRecord(
            date=d,
            base="",
            service_type="",
            day_night="D",
            role=role,
            filled=False,
            overtime=False,
            leave_type=None,
            source_tab=sheet_label,
            source_cell=cell_ref,
            raw_value=text,
            person_display=person_display,
            person_displays=person_displays,
            is_manager_row=is_manager_row,
            skip_reason=skip_reason,
            included_in_aggregates=included_in_aggregates,
            excel_row=row_idx,
            excel_col=col_idx,
        )
    )


def schedule_file_sha256(path: str) -> str:
    """SHA-256 hex digest of a schedule workbook file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _name_tokens_for_grid_row(ws: Worksheet, row_idx: int) -> set[str]:
    """Tokens from columns A–B (last/first names) for manager filtering."""
    tokens: set[str] = set()
    for col in (1, 2):
        name_cell = ws.cell(row=row_idx, column=col).value
        name_key = (_normalize_cell_value(name_cell) or "").strip()
        if not name_key:
            continue
        tokens.update(t.strip(".,").upper() for t in name_key.split() if t.strip(".,"))
    return tokens


def _grid_name_cells(ws: Worksheet, row_idx: int) -> tuple[str, str]:
    def _cell_str(v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    return (
        _cell_str(ws.cell(row=row_idx, column=1).value),
        _cell_str(ws.cell(row=row_idx, column=2).value),
    )


def _row_person_displays_and_manager_flag(
    ws: Worksheet,
    row_idx: int,
    role: Role,
    manager_last_names_upper: frozenset[str],
) -> tuple[tuple[str, ...], bool]:
    """Clean person label(s) from columns A–B and manager-roster flag."""
    tokens = _name_tokens_for_grid_row(ws, row_idx)
    roster_hit = (
        tokens & manager_last_names_upper if manager_last_names_upper else set()
    )
    if roster_hit:
        return (min(roster_hit).title(),), True

    raw_a, raw_b = _grid_name_cells(ws, row_idx)
    persons = person_displays_for_role(role, raw_a, raw_b)
    return persons, False


def _shift_record_persons(rec: ShiftRecord) -> tuple[str, ...]:
    if rec.person_displays:
        return rec.person_displays
    person = (rec.person_display or "").strip()
    return (person,) if person else ()


def _header_cell_to_date(value: object, default_year: int | None = None) -> date | None:
    """Parse a single header cell (Excel date or string) to a calendar date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m/%d"):
        try:
            if fmt == "%m/%d":
                if default_year is None:
                    continue
                return datetime.strptime(s, fmt).replace(year=default_year).date()
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _canonical_unit_code(code: str) -> str | None:
    """Map legacy aliases to canonical UNIT_MAP keys; None if retired."""
    if code in RETIRED_UNIT_CODES:
        return None
    return LEGACY_UNIT_ALIASES.get(code, code)


def _is_resolvable_unit(code: str) -> bool:
    """True when code (or its legacy alias) maps to a known staffed unit."""
    canonical = _canonical_unit_code(code)
    return canonical is not None and canonical in UNIT_MAP


def _split_unit_suffix(code: str) -> tuple[str, bool, bool]:
    """
    Split a unit-like code into (base_unit, is_ot, is_dual_role).

    Strip trailing 'C' (OT) and 'P' (dual-role) only when the remainder is in
    UNIT_MAP (or LEGACY_UNIT_ALIASES), so D7P/N7P (Plymouth) are preserved.
    If core is already resolvable, do not strip. E.g. N7PC -> N7P (strip C);
    D9PC -> D9P (strip C); D7PP -> D7P (strip P); D7P -> D7P (no strip).

    Also supports " C" (space + C) for OT merge: D7P C, D7B C etc. → overtime.
    """
    is_ot = False
    is_dual = False
    core = code

    # Merge: "UNIT C" or "UNIT C" (space + C) → OT when base unit is known
    for suffix in OT_SUFFIXES:
        if core.endswith(suffix):
            candidate = core[: -len(suffix)].strip()
            if _is_resolvable_unit(candidate):
                core = candidate
                is_ot = True
                break

    while len(core) > 1 and core[-1] in {"C", "P"}:
        if _is_resolvable_unit(core):
            break
        last = core[-1]
        candidate = core[:-1]
        if _is_resolvable_unit(candidate):
            core = candidate
            if last == "C":
                is_ot = True
            else:
                is_dual = True
            break
        core = candidate
        if last == "C":
            is_ot = True
        else:
            is_dual = True
    return core, is_ot, is_dual


def _classify_unit(code: str) -> tuple[str, ServiceType, DayNight] | None:
    """Return (base, service_type, day_night) for a cleaned unit, or None."""
    canonical = _canonical_unit_code(code)
    if canonical is None:
        return None
    return UNIT_MAP.get(canonical)


def _iter_date_headers(
    row,
    week_start_date: date | None = None,
    week_end_date: date | None = None,
) -> list[tuple[int, date]]:
    """
    Given a header row (RN/Medic grid), return list of (column_index, date).
    Dates are taken from C1:P1 only (columns 3–16); row 2 is day labels
    (C2:P2).

    We expect each date header cell to be either a real Excel date or
    a string parseable as YYYY-MM-DD or similar. Cells with no valid
    date are skipped.
    If week_start_date and week_end_date are set, only columns whose
    date falls in [week_start_date, week_end_date] are included.
    """
    default_y = (
        week_start_date.year if week_start_date is not None else date.today().year
    )
    result: list[tuple[int, date]] = []
    for cell in row:
        if cell.column < 3 or cell.column > 16:  # only C–P (C1:P1)
            continue
        d = _header_cell_to_date(cell.value, default_y)
        if d is None:
            continue
        if week_start_date is not None and week_end_date is not None:
            if not week_start_date <= d <= week_end_date:
                continue
        result.append((cell.column, d))
    return result


def _normalize_sheet_title(name: str) -> str:
    s = " ".join(str(name).split())
    return s.lower().replace(" and ", " & ")


def resolve_workbook_sheet(wb: Workbook, *labels: str) -> str | None:
    """Return the workbook's actual sheet name matching any label (spacing/case/& vs and)."""
    for actual in wb.sheetnames:
        an = _normalize_sheet_title(actual)
        for lb in labels:
            if _normalize_sheet_title(lb) == an:
                return actual
    return None


def _best_header_row_in_ws(
    ws: Worksheet,
    week_start_date: date | None,
    week_end_date: date | None,
    prefer_row: int = 1,
) -> tuple[int, list[tuple[int, date]]]:
    """Use row 1–5; pick the row with the most date columns in the selected week."""
    order = [prefer_row] + [r for r in range(1, 6) if r != prefer_row]
    best_row = prefer_row
    best_cols: list[tuple[int, date]] = []
    for row_idx in order:
        col_dates = _iter_date_headers(
            ws[row_idx],
            week_start_date=week_start_date,
            week_end_date=week_end_date,
        )
        if len(col_dates) > len(best_cols):
            best_row = row_idx
            best_cols = col_dates
    return best_row, best_cols


def detect_schedule_week_starts(path: str) -> list[str]:
    """Sunday ``YYYY-MM-DD`` for each week present in schedule headers (cols C–P, rows 1–5).

    Scans **RN & Medic**, **EMT**, and **OPS View** (name variants) so two-week workbooks work.
    """
    wb = load_workbook(path, data_only=True)
    try:
        all_d: set[date] = set()
        default_y = date.today().year
        sheet_keys = [
            resolve_workbook_sheet(wb, "RN & Medic", "RN AND MEDIC", "RN/MEDIC"),
            resolve_workbook_sheet(wb, "EMT"),
            resolve_workbook_sheet(wb, "OPS View", "Ops View"),
        ]
        for sn in sheet_keys:
            if not sn:
                continue
            ws = wb[sn]
            for row_idx in range(1, 6):
                for cell in ws[row_idx]:
                    if cell.column < 3 or cell.column > 16:
                        continue
                    d = _header_cell_to_date(cell.value, default_y)
                    if d is not None:
                        all_d.add(d)
        if not all_d:
            return []
        sundays: set[date] = set()
        for d in all_d:
            sun = d - timedelta(days=(d.weekday() + 1) % 7)
            sundays.add(sun)
        return sorted(s_dt.isoformat() for s_dt in sorted(sundays))
    finally:
        wb.close()


def _parse_grid(
    ws: Worksheet,
    header_row_idx: int,
    first_row_idx: int,
    last_row_idx: int,
    role: Role,
    sheet_label: str,
    week_start_date: date | None = None,
    week_end_date: date | None = None,
    unit_overrides: dict[str, str] | None = None,
    manager_last_names_upper: frozenset[str] | None = None,
    extra_training_codes: frozenset[str] | None = None,
) -> tuple[list[ShiftRecord], list[ParseIssue]]:
    """Parse a rectangular RN/Medic/EMT grid into shift records + issues.

    unit_overrides: map raw_value -> replacement
    (e.g. {"D7BCP": "D7BC", "TYPO": "D7B/LT"}).
    Use at import to fix typos or merge unknown codes.

    manager_last_names_upper: roster from staffing.db (or caller default); when
    omitted, uses the built-in default list.

    extra_training_codes: admin-added codes (Settings > Training codes) on
    top of the built-in SKIP_TRAINING_VALUES -- e.g. a new class name that
    would otherwise show up as an unknown unit code.
    """
    mgr_upper = (
        manager_last_names_upper
        if manager_last_names_upper is not None
        else default_manager_last_names_upper()
    )
    records: list[ShiftRecord] = []
    issues: list[ParseIssue] = []
    overrides = unit_overrides or {}
    training_values = SKIP_TRAINING_VALUES | (extra_training_codes or frozenset())
    skip_cell_values = SKIP_CELL_VALUES | (extra_training_codes or frozenset())

    header_row_idx, col_dates = _best_header_row_in_ws(
        ws,
        week_start_date=week_start_date,
        week_end_date=week_end_date,
        prefer_row=header_row_idx,
    )
    if not col_dates:
        default_y = (
            week_start_date.year if week_start_date is not None else date.today().year
        )
        raw_dates: list[date] = []
        for scan_r in range(1, 6):
            for cell in ws[scan_r]:
                if cell.column < 3 or cell.column > 16:
                    continue
                d = _header_cell_to_date(cell.value, default_y)
                if d is not None:
                    raw_dates.append(d)
        if week_start_date is not None and week_end_date is not None and raw_dates:
            lo, hi = min(raw_dates), max(raw_dates)
            sun = lo - timedelta(days=(lo.weekday() + 1) % 7)
            suggested = sun.isoformat()
            issues.append(
                ParseIssue(
                    sheet=ws.title,
                    cell=f"row {header_row_idx} C:P",
                    raw_value="",
                    issue_type="week_mismatch",
                    message=(
                        f"No columns fall in the selected week "
                        f"{week_start_date.isoformat()} through "
                        f"{week_end_date.isoformat()}. "
                        f"This sheet shows dates {lo.isoformat()} through "
                        f"{hi.isoformat()}. "
                        f"Use Sunday week_start={suggested} for this file, "
                        f"or choose a schedule export whose header dates "
                        f"include your week."
                    ),
                )
            )
        else:
            issues.append(
                ParseIssue(
                    sheet=ws.title,
                    cell=f"A{header_row_idx}",
                    raw_value="",
                    issue_type="no_dates",
                    message="No date headers found in RN/Medic grid header row.",
                )
            )
        return records, issues

    skip_from_row_idx = _find_non_person_skip_row(ws, first_row_idx, last_row_idx)

    for row_idx in range(first_row_idx, last_row_idx + 1):
        person_displays, is_manager_row = _row_person_displays_and_manager_flag(
            ws, row_idx, role, mgr_upper
        )
        person_display = person_displays[0] if person_displays else ""
        for col_idx, d in col_dates:
            cell = ws.cell(row=row_idx, column=col_idx)
            raw = cell.value
            text = _normalize_cell_value(raw)
            if not text:
                continue
            if (
                skip_from_row_idx is not None
                and row_idx >= skip_from_row_idx
                and _SCHEDULE_COL_B <= col_idx <= _SCHEDULE_COL_P
            ):
                _append_skipped_shift(
                    records,
                    ws=ws,
                    row_idx=row_idx,
                    col_idx=col_idx,
                    d=d,
                    role=role,
                    sheet_label=sheet_label,
                    text=text,
                    person_displays=person_displays,
                    person_display=person_display,
                    is_manager_row=is_manager_row,
                    skip_reason="schedule_row",
                )
                continue
            # Apply import mapping: unknown code -> treat as this value
            # (e.g. D7BCP -> D7BC)
            if text in overrides:
                text = _normalize_cell_value(overrides[text])
                if not text:
                    continue
            cell_ref = f"{get_column_letter(col_idx)}{row_idx}"

            # EMT: NL = Lawrence RW night (N9L) when EMT is operator on line.
            if role == "EMT" and text == "NL":
                text = "N9L"

            # Non-staffing / training / admin markers — persist as skipped.
            if text in skip_cell_values:
                if is_manager_row and text == "AOC":
                    records.append(
                        ShiftRecord(
                            date=d,
                            base="",
                            service_type="",
                            day_night="",
                            role=role,
                            filled=False,
                            overtime=False,
                            leave_type=None,
                            source_tab=sheet_label,
                            source_cell=cell_ref,
                            raw_value=text,
                            person_display=person_display,
                            person_displays=person_displays,
                            is_manager_row=True,
                            included_in_aggregates=False,
                            manager_event_type="aoc",
                            excel_row=row_idx,
                            excel_col=col_idx,
                        )
                    )
                    continue
                _append_skipped_shift(
                    records,
                    ws=ws,
                    row_idx=row_idx,
                    col_idx=col_idx,
                    d=d,
                    role=role,
                    sheet_label=sheet_label,
                    text=text,
                    person_displays=person_displays,
                    person_display=person_display,
                    is_manager_row=is_manager_row,
                    skip_reason=_classify_skip_reason(text, training_values),
                )
                continue

            if text == "OPEN":
                _append_skipped_shift(
                    records,
                    ws=ws,
                    row_idx=row_idx,
                    col_idx=col_idx,
                    d=d,
                    role=role,
                    sheet_label=sheet_label,
                    text=text,
                    person_displays=person_displays,
                    person_display=person_display,
                    is_manager_row=is_manager_row,
                    skip_reason="open",
                )
                continue

            # Weekday labels (e.g. row 2: SUN, MON, TUE, …) — ignore.
            if text in {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"}:
                continue

            # Ignored unit-like codes — persist as skipped admin.
            if text in IGNORE_UNIT_CODES:
                _append_skipped_shift(
                    records,
                    ws=ws,
                    row_idx=row_idx,
                    col_idx=col_idx,
                    d=d,
                    role=role,
                    sheet_label=sheet_label,
                    text=text,
                    person_displays=person_displays,
                    person_display=person_display,
                    is_manager_row=is_manager_row,
                    skip_reason="admin",
                )
                continue

            # EMT: PER → LT (same bucket as leave time for exception grid).
            if role == "EMT" and text == "PER":
                if is_manager_row:
                    _append_skipped_shift(
                        records,
                        ws=ws,
                        row_idx=row_idx,
                        col_idx=col_idx,
                        d=d,
                        role=role,
                        sheet_label=sheet_label,
                        text=text,
                        person_displays=person_displays,
                        person_display=person_display,
                        is_manager_row=True,
                        skip_reason="manager_row",
                    )
                    continue
                records.append(
                    ShiftRecord(
                        date=d,
                        base="",
                        service_type="",
                        day_night="D",
                        role=role,
                        filled=False,
                        overtime=False,
                        leave_type="LT",
                        source_tab=sheet_label,
                        source_cell=cell_ref,
                        raw_value=text,
                        person_display=person_display,
                        person_displays=person_displays,
                        is_manager_row=is_manager_row,
                        excel_row=row_idx,
                        excel_col=col_idx,
                    )
                )
                continue

            # Merge: UNIT/LEAVETYPE → leave (any unit + any leave type)
            if "/" in text:
                parts = text.split("/", 1)
                suffix = (parts[1] or "").strip()
                leave_display = UNIT_LEAVE_MERGE.get(suffix)
                if leave_display is not None:
                    if is_manager_row:
                        _append_skipped_shift(
                            records,
                            ws=ws,
                            row_idx=row_idx,
                            col_idx=col_idx,
                            d=d,
                            role=role,
                            sheet_label=sheet_label,
                            text=text,
                            person_displays=person_displays,
                            person_display=person_display,
                            is_manager_row=True,
                            skip_reason="manager_row",
                        )
                        continue
                    records.append(
                        ShiftRecord(
                            date=d,
                            base="",
                            service_type="",
                            day_night="D",
                            role=role,
                            filled=False,
                            overtime=False,
                            leave_type=leave_display,
                            source_tab=sheet_label,
                            source_cell=cell_ref,
                            raw_value=text,
                            person_display=person_display,
                            person_displays=person_displays,
                            is_manager_row=is_manager_row,
                            excel_row=row_idx,
                            excel_col=col_idx,
                        )
                    )
                    continue

            # Leave/absence codes (LT-D, LT-N kept; SM/AT counts as AT.)
            if text in AT_ALIASES:
                leave_code = "AT"
                leave_display = "AT"
            elif text.startswith("LT-"):
                leave_code = "LT"
                leave_display = text  # LT-D or LT-N
            else:
                leave_code = text
                leave_display = text
            if leave_code in LEAVE_CODES:
                if is_manager_row:
                    _append_skipped_shift(
                        records,
                        ws=ws,
                        row_idx=row_idx,
                        col_idx=col_idx,
                        d=d,
                        role=role,
                        sheet_label=sheet_label,
                        text=text,
                        person_displays=person_displays,
                        person_display=person_display,
                        is_manager_row=True,
                        skip_reason="manager_row",
                    )
                    continue
                records.append(
                    ShiftRecord(
                        date=d,
                        base="",
                        service_type="",
                        day_night="D",
                        role=role,
                        filled=False,
                        overtime=False,
                        leave_type=leave_display,
                        source_tab=sheet_label,
                        source_cell=cell_ref,
                        raw_value=text,
                        person_display=person_display,
                        person_displays=person_displays,
                        is_manager_row=is_manager_row,
                        excel_row=row_idx,
                        excel_col=col_idx,
                    )
                )
                continue

            base_code, is_ot, is_dual = _split_unit_suffix(text)
            if base_code in RETIRED_UNIT_CODES:
                _append_skipped_shift(
                    records,
                    ws=ws,
                    row_idx=row_idx,
                    col_idx=col_idx,
                    d=d,
                    role=role,
                    sheet_label=sheet_label,
                    text=text,
                    person_displays=person_displays,
                    person_display=person_display,
                    is_manager_row=is_manager_row,
                    skip_reason="retired_unit",
                )
                continue

            unit_info = _classify_unit(base_code)
            if not unit_info:
                issues.append(
                    ParseIssue(
                        sheet=ws.title,
                        cell=cell_ref,
                        raw_value=text,
                        issue_type="unknown_unit",
                        message=(
                            f"Unknown unit code '{text}' (base '{base_code}') "
                            f"for role {role}."
                        ),
                    )
                )
                continue

            base, service_type, dn = unit_info
            canonical_unit = _canonical_unit_code(base_code) or base_code
            effective_role: Role = role
            if role == "RN" and is_dual:
                effective_role = "MEDIC"

            records.append(
                ShiftRecord(
                    date=d,
                    base=base,
                    service_type=service_type,
                    day_night=dn,
                    role=effective_role,
                    filled=True,
                    overtime=is_ot,
                    leave_type=None,
                    source_tab=sheet_label,
                    source_cell=cell_ref,
                    raw_value=text,
                    unit_code=canonical_unit,
                    person_display=person_display,
                    person_displays=person_displays,
                    is_manager_row=is_manager_row,
                    excel_row=row_idx,
                    excel_col=col_idx,
                )
            )

    return records, issues


def _person_shift_event_type(rec: ShiftRecord) -> str | None:
    """Derive staffed / leave / ot / training / skipped from a parsed shift record."""
    if rec.skip_reason == "training":
        return "training"
    if rec.skip_reason:
        return "skipped"
    if rec.leave_type:
        return "leave"
    if rec.filled:
        return "ot" if rec.overtime else "staffed"
    return None


def weekly_person_shift_mappings(
    week_start: str,
    records: Iterable[ShiftRecord],
    staff_roster_index: StaffRosterMatchIndex | None = None,
    *,
    schedule_import_id: int | None = None,
) -> list[dict[str, object]]:
    """
    Rows for ``WeeklyPersonShift`` bulk insert: staffed, leave, OT, and skipped
    cells for clinical roles (RN, MEDIC, EMT).

    When ``staff_roster_index`` is provided, ``staff_member_id`` and canonical
    ``person_display`` are set when a roster match exists; unmatched names are
    still persisted.
    """
    rows: list[dict[str, object]] = []
    for r in records:
        event_type = _person_shift_event_type(r)
        if event_type is None:
            continue
        if r.role not in {"RN", "MEDIC", "EMT"}:
            continue
        persons = _shift_record_persons(r)
        if not persons:
            if event_type != "skipped":
                continue
            persons = ("",)
        base_row = {
            "week_start": week_start,
            "schedule_import_id": schedule_import_id,
            "shift_date": r.date.isoformat(),
            "role": r.role,
            "event_type": event_type,
            "base_name": (r.base or "").strip(),
            "service_type": (r.service_type or "").strip(),
            "day_night": (r.day_night or "").strip() or "D",
            "unit_code": (r.unit_code or "").strip(),
            "leave_type": (r.leave_type or "").strip() or None,
            "overtime": 1 if r.overtime else 0,
            "raw_value": (r.raw_value or "")[:64],
            "source_tab": (r.source_tab or "")[:128],
            "source_cell": (r.source_cell or "")[:16],
            "excel_row": r.excel_row or 0,
            "excel_col": r.excel_col or 0,
            "is_manager_row": 1 if r.is_manager_row else 0,
            "included_in_aggregates": 1 if r.included_in_aggregates else 0,
            "skip_reason": (r.skip_reason or "")[:32] or None,
        }
        for person in persons:
            staff_member_id = None
            display = person[:256]
            if staff_roster_index is not None and person:
                entry = match_parsed_person_to_roster(
                    person, r.role, staff_roster_index
                )
                if entry is not None:
                    staff_member_id = entry.id
                    display = canonical_display(entry)[:256]
            rows.append(
                {
                    **base_row,
                    "staff_member_id": staff_member_id,
                    "person_display": display,
                }
            )
    return rows


def weekly_manager_shift_mappings(
    week_start: str,
    records: Iterable[ShiftRecord],
) -> list[dict[str, object]]:
    """
    Rows for ``WeeklyManagerShift`` bulk insert: staffed unit cells and AOC
    admin days on manager roster rows (same last-name set as leave exclusion).
    """
    rows: list[dict[str, object]] = []
    for r in records:
        if not r.is_manager_row:
            continue
        if r.manager_event_type == "aoc":
            if r.role not in {"RN", "MEDIC", "EMT"}:
                continue
            rows.append(
                {
                    "week_start": week_start,
                    "person_display": (r.person_display or "").strip() or "(unknown)",
                    "role": r.role,
                    "shift_date": r.date.isoformat(),
                    "event_type": "aoc",
                    "base_name": "",
                    "service_type": "",
                    "day_night": "",
                    "unit_code": "",
                    "overtime": 0,
                    "raw_value": (r.raw_value or "")[:64],
                    "source_tab": (r.source_tab or "")[:128],
                    "source_cell": (r.source_cell or "")[:16],
                }
            )
            continue
        if not r.filled:
            continue
        if r.role not in {"RN", "MEDIC", "EMT"}:
            continue
        if not (r.base or "").strip():
            continue
        rows.append(
            {
                "week_start": week_start,
                "person_display": (r.person_display or "").strip() or "(unknown)",
                "role": r.role,
                "shift_date": r.date.isoformat(),
                "event_type": "line_shift",
                "base_name": r.base,
                "service_type": r.service_type,
                "day_night": r.day_night,
                "unit_code": (r.unit_code or "").strip(),
                "overtime": 1 if r.overtime else 0,
                "raw_value": (r.raw_value or "")[:64],
                "source_tab": (r.source_tab or "")[:128],
                "source_cell": (r.source_cell or "")[:16],
            }
        )
    return rows


# --- OPS View: vehicle-level RW/GR staffed unit-days ----------------------

# OPS View: unit codes in column A, role labels in column B (rows 4-66).
# GR2 EMT, NG2 EMT, PG EMT, NP EMT, NL EMT count as EMT; Orientee/RAL ignored.
_OPS_ROLE_RN = {"RN"}
_OPS_ROLE_MEDIC = {"MEDIC"}
_OPS_ROLE_PIC = {"PIC"}
_OPS_ROLE_EMT = {"EMT", "GR2 EMT", "NG2 EMT", "PG EMT", "NP EMT", "NL EMT"}


def _ops_parse_dates_row(
    ws: Worksheet,
    week_start_date: date,
    week_end_date: date,
) -> list[tuple[int, date]]:
    """Columns C–P; try rows 1–5 for the date header (templates vary)."""
    _, best = _best_header_row_in_ws(
        ws,
        week_start_date=week_start_date,
        week_end_date=week_end_date,
        prefer_row=1,
    )
    return best


def _ops_vehicle_blocks(
    ws: Worksheet,
) -> list[tuple[str, int, dict[str, list[int]]]]:
    """
    Column A = unit code, column B = role label (rows 4-66). When A matches
    UNIT_MAP we start a new vehicle block; subsequent rows with A empty use B
    for role.
    Return (unit_code, first_row, role_rows) with role_rows mapping role to
    row indices.
    """
    blocks: list[tuple[str, int, dict[str, list[int]]]] = []
    current_unit: str | None = None
    current_start = 0
    role_rows: dict[str, list[int]] = {}

    # Row 3 is often weekday labels; unit blocks start row 4 (BMF 29 Mar 2026 layout).
    for row_idx in range(4, 67):
        cell_a = ws.cell(row=row_idx, column=1)
        cell_b = ws.cell(row=row_idx, column=2)
        raw_a = cell_a.value
        raw_b = cell_b.value
        text_a = (_normalize_cell_value(raw_a) or "").strip()
        text_b = (_normalize_cell_value(raw_b) or "").strip()
        # Unit code in A starts a new block (ignore FLOAT, "(Badged)", etc.)
        if text_a and text_a not in RETIRED_UNIT_CODES:
            canonical_a = LEGACY_UNIT_ALIASES.get(text_a, text_a)
            if canonical_a in UNIT_MAP:
                if current_unit is not None:
                    blocks.append((current_unit, current_start, role_rows))
                current_unit = canonical_a
            current_start = row_idx
            role_rows = {}
        if current_unit is None:
            continue
        if not text_b:
            continue
        if text_b in _OPS_ROLE_RN:
            role_rows.setdefault("RN", []).append(row_idx)
        elif text_b in _OPS_ROLE_MEDIC:
            role_rows.setdefault("Medic", []).append(row_idx)
        elif text_b in _OPS_ROLE_PIC:
            role_rows.setdefault("PIC", []).append(row_idx)
        elif text_b in _OPS_ROLE_EMT:
            role_rows.setdefault("EMT", []).append(row_idx)
    if current_unit is not None:
        blocks.append((current_unit, current_start, role_rows))
    return blocks


def _ops_cell_staffed(cell_value: object) -> bool:
    """True if cell has a non-empty value that looks like a name."""
    s = _normalize_cell_value(cell_value)
    if not s or s == "OPEN":
        return False
    return True


def _cap_base_coverage_split(
    base_rw_day: dict[str, int],
    base_rw_night: dict[str, int],
    base_gr_day: dict[str, int],
    base_gr_night: dict[str, int],
) -> None:
    """Enforce MAX_RW / MAX_GR per base on (day+night) totals.

    Reduce night counts first.
    """
    for base in set(base_rw_day) | set(base_rw_night):
        cap = MAX_RW_UNIT_DAYS_PER_WEEK.get(base)
        if cap is None:
            continue
        d, n = base_rw_day.get(base, 0), base_rw_night.get(base, 0)
        total = d + n
        if total <= cap:
            continue
        over = total - cap
        nn = min(over, n)
        base_rw_night[base] = n - nn
        over -= nn
        if over > 0:
            base_rw_day[base] = max(0, d - over)
    for base in set(base_gr_day) | set(base_gr_night):
        cap = MAX_GR_UNIT_DAYS_PER_WEEK.get(base)
        if cap is None:
            continue
        d, n = base_gr_day.get(base, 0), base_gr_night.get(base, 0)
        total = d + n
        if total <= cap:
            continue
        over = total - cap
        nn = min(over, n)
        base_gr_night[base] = n - nn
        over -= nn
        if over > 0:
            base_gr_day[base] = max(0, d - over)


def _parse_ops_view_worksheet(
    ws: Worksheet,
    week_start: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    """
    Parse an already-loaded OPS View worksheet (same rules as parse_ops_view).
    Returns (rw_day, rw_night, gr_day, gr_night) per base_name.
    """
    try:
        week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
    except ValueError:
        return {}, {}, {}, {}
    week_end_date = week_start_date + timedelta(days=6)

    col_dates = _ops_parse_dates_row(ws, week_start_date, week_end_date)
    blocks = _ops_vehicle_blocks(ws)

    base_rw_day: dict[str, int] = {}
    base_rw_night: dict[str, int] = {}
    base_gr_day: dict[str, int] = {}
    base_gr_night: dict[str, int] = {}

    for unit_code, _first_row, role_rows in blocks:
        unit_info = UNIT_MAP.get(unit_code)
        if not unit_info:
            continue
        base, service_type, dn = unit_info
        rn_rows = role_rows.get("RN") or []
        medic_rows = role_rows.get("Medic") or []
        pic_rows = role_rows.get("PIC") or []
        emt_rows = role_rows.get("EMT") or []

        for col_idx, _d in col_dates:
            staffed = _ops_staffed_for_column(
                ws,
                col_idx,
                service_type,
                rn_rows,
                medic_rows,
                pic_rows,
                emt_rows,
            )
            if service_type == "RW":
                if staffed:
                    if dn == "N":
                        base_rw_night[base] = base_rw_night.get(base, 0) + 1
                    else:
                        base_rw_day[base] = base_rw_day.get(base, 0) + 1
            elif service_type == "GR":
                if staffed:
                    if dn == "N":
                        base_gr_night[base] = base_gr_night.get(base, 0) + 1
                    else:
                        base_gr_day[base] = base_gr_day.get(base, 0) + 1

    _cap_base_coverage_split(
        base_rw_day,
        base_rw_night,
        base_gr_day,
        base_gr_night,
    )

    return base_rw_day, base_rw_night, base_gr_day, base_gr_night


def parse_ops_view(
    path: str,
    week_start: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    """
    Parse the OPS View sheet from a workbook path (loads the file once).

    Day vs night follows the unit code in UNIT_MAP
    (e.g. D7B=day RW, N7B=night RW).

    - Dates: row 1, columns C–P; only dates in
      [week_start, week_start+6] are used.
    - Vehicles: column A rows 4–66; unit codes from UNIT_MAP; role rows
      RN, Medic, PIC, EMT (GR2/NG2 EMT = EMT).
    - RW staffed: RN + Medic + (PIC or EMT); GR staffed: RN + Medic + EMT.

    Returns (rw_day, rw_night, gr_day, gr_night) per base_name.
    """
    wb = load_workbook(path, data_only=True)
    try:
        sn = resolve_workbook_sheet(wb, "OPS View", "Ops View")
        if not sn:
            return {}, {}, {}, {}
        return _parse_ops_view_worksheet(wb[sn], week_start)
    finally:
        wb.close()


def parse_schedule_workbook(
    path: str,
    week_start: str | None = None,
    unit_overrides: dict[str, str] | None = None,
    manager_last_names_upper: frozenset[str] | None = None,
    extra_training_codes: frozenset[str] | None = None,
    wb: Workbook | None = None,
) -> tuple[
    list[ShiftRecord],
    list[ParseIssue],
    tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]] | None,
]:
    """
    Parse the current BMF schedule workbook into shift records.

    If week_start is provided (YYYY-MM-DD, Sunday), only dates in that 7-day
    window are included. When "OPS View" sheet exists and week_start is set,
    returns (records, issues, (rw_day, rw_night, gr_day, gr_night)) from OPS
    View; otherwise the third value is None.

    manager_last_names_upper: roster from staffing.db for manager rows; when
    omitted, uses the built-in default (see ``manager_roster``).

    Assumptions (based on your 15 Feb 2026 layout):
    - Sheet 'RN & Medic': RN lines ~4–50, Medic ~52–100 (dates often row 2).
    - Date row may be 1–5 (not always row 1). EMT tab mirrors RN grid.
    """
    week_start_date: date | None = None
    week_end_date: date | None = None
    if week_start:
        try:
            week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
            week_end_date = week_start_date + timedelta(days=6)
        except ValueError:
            pass

    mgr_upper = (
        manager_last_names_upper
        if manager_last_names_upper is not None
        else default_manager_last_names_upper()
    )

    records: list[ShiftRecord] = []
    issues: list[ParseIssue] = []

    with _open_workbook(path, wb) as wb:
        # RN & Medic
        sn_rn = resolve_workbook_sheet(wb, "RN & Medic", "RN AND MEDIC", "RN/MEDIC")
        if sn_rn:
            ws = wb[sn_rn]
            # Dates often row 2 (row 3 = Sun–Sat labels); RN lines start row 4.
            # End row extended for current templates (was 43; med block starts ~52).
            rn_records, rn_issues = _parse_grid(
                ws=ws,
                header_row_idx=1,
                first_row_idx=4,
                last_row_idx=50,
                role="RN",
                sheet_label="RN & Medic (RN)",
                week_start_date=week_start_date,
                week_end_date=week_end_date,
                unit_overrides=unit_overrides,
                manager_last_names_upper=mgr_upper,
                extra_training_codes=extra_training_codes,
            )
            # Medic block: first line row 52 (row 51 blank in current template).
            med_records, med_issues = _parse_grid(
                ws=ws,
                header_row_idx=1,
                first_row_idx=52,
                last_row_idx=100,
                role="MEDIC",
                sheet_label="RN & Medic (Medic)",
                week_start_date=week_start_date,
                week_end_date=week_end_date,
                unit_overrides=unit_overrides,
                manager_last_names_upper=mgr_upper,
                extra_training_codes=extra_training_codes,
            )
            records.extend(rn_records)
            records.extend(med_records)
            issues.extend(rn_issues)
            issues.extend(med_issues)
        else:
            issues.append(
                ParseIssue(
                    sheet="(workbook)",
                    cell="",
                    raw_value="",
                    issue_type="missing_sheet",
                    message=(
                        "RN/Medic sheet not found (looked for 'RN & Medic', "
                        "'RN AND MEDIC', 'RN/MEDIC'). Actual sheets: "
                        f"{wb.sheetnames!r}."
                    ),
                )
            )

        # EMT tab: layout mirrors RN & Medic template.
        sn_emt = resolve_workbook_sheet(wb, "EMT")
        if sn_emt:
            emt_ws = wb[sn_emt]
            # EMT: dates row 1, day names row 2, staff rows 3+; counters section lower sheet.
            emt_records, emt_issues = _parse_grid(
                ws=emt_ws,
                header_row_idx=1,
                first_row_idx=3,
                last_row_idx=29,
                role="EMT",
                sheet_label="EMT",
                week_start_date=week_start_date,
                week_end_date=week_end_date,
                unit_overrides=unit_overrides,
                manager_last_names_upper=mgr_upper,
                extra_training_codes=extra_training_codes,
            )
            records.extend(emt_records)
            issues.extend(emt_issues)

        ops_coverage: (
            tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]] | None
        ) = None
        if week_start:
            sn_ops = resolve_workbook_sheet(wb, "OPS View", "Ops View")
            if sn_ops:
                ops_rw_d, ops_rw_n, ops_gr_d, ops_gr_n = _parse_ops_view_worksheet(
                    wb[sn_ops], week_start
                )
                ops_coverage = (ops_rw_d, ops_rw_n, ops_gr_d, ops_gr_n)

        return records, issues, ops_coverage


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


def _ops_staffed_for_column(
    ws: Worksheet,
    col_idx: int,
    service_type: ServiceType,
    rn_rows: list[int],
    medic_rows: list[int],
    pic_rows: list[int],
    emt_rows: list[int],
) -> bool:
    rn_ok = any(
        _ops_cell_staffed(ws.cell(row=r, column=col_idx).value) for r in rn_rows
    )
    medic_ok = any(
        _ops_cell_staffed(ws.cell(row=r, column=col_idx).value) for r in medic_rows
    )
    pic_ok = any(
        _ops_cell_staffed(ws.cell(row=r, column=col_idx).value) for r in pic_rows
    )
    emt_ok = any(
        _ops_cell_staffed(ws.cell(row=r, column=col_idx).value) for r in emt_rows
    )
    if service_type == "RW":
        return rn_ok and medic_ok and (pic_ok or emt_ok)
    return rn_ok and medic_ok and emt_ok


def _parse_ops_view_daily_worksheet(
    ws: Worksheet,
    week_start: str,
) -> dict[date, tuple[int, int]]:
    """Per calendar day: (RW staffed unit-days, GR staffed unit-days) from OPS View."""
    try:
        week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
    except ValueError:
        return {}
    week_end_date = week_start_date + timedelta(days=6)

    col_dates = _ops_parse_dates_row(ws, week_start_date, week_end_date)
    if not col_dates:
        return {}

    daily: dict[date, list[int]] = {d: [0, 0] for _, d in col_dates}
    for unit_code, _first_row, role_rows in _ops_vehicle_blocks(ws):
        unit_info = UNIT_MAP.get(unit_code)
        if not unit_info:
            continue
        base, service_type, _dn = unit_info
        rn_rows = role_rows.get("RN") or []
        medic_rows = role_rows.get("Medic") or []
        pic_rows = role_rows.get("PIC") or []
        emt_rows = role_rows.get("EMT") or []

        for col_idx, day_date in col_dates:
            if not _ops_staffed_for_column(
                ws,
                col_idx,
                service_type,
                rn_rows,
                medic_rows,
                pic_rows,
                emt_rows,
            ):
                continue
            if service_type == "RW":
                daily[day_date][0] += 1
            else:
                daily[day_date][1] += 1

    return {d: (vals[0], vals[1]) for d, vals in daily.items()}


def parse_ops_view_daily(
    path: str, week_start: str, wb: Workbook | None = None
) -> dict[date, tuple[int, int]]:
    """Load workbook and return per-day RW/GR staffed counts from OPS View."""
    with _open_workbook(path, wb) as wb:
        sn = resolve_workbook_sheet(wb, "OPS View", "Ops View")
        if not sn:
            return {}
        return _parse_ops_view_daily_worksheet(wb[sn], week_start)


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


def _ops_role_label(text_b: str) -> str | None:
    if text_b in _OPS_ROLE_RN:
        return "RN"
    if text_b in _OPS_ROLE_MEDIC:
        return "Medic"
    if text_b in _OPS_ROLE_PIC:
        return "PIC"
    if text_b in _OPS_ROLE_EMT:
        return "EMT"
    return None


def _parse_ops_view_detail_worksheet(
    ws: Worksheet,
    week_start: str,
) -> tuple[list[OpsViewDayBase], list[OpsViewAssignment]]:
    """Per-day per-base counts and name-level OPS View assignments."""
    try:
        week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
    except ValueError:
        return [], []
    week_end_date = week_start_date + timedelta(days=6)

    col_dates = _ops_parse_dates_row(ws, week_start_date, week_end_date)
    if not col_dates:
        return [], []

    day_base: dict[tuple[date, str], list[int]] = {
        (d, base): [0, 0]
        for _, d in col_dates
        for base in {info[0] for info in UNIT_MAP.values()}
    }
    assignments: list[OpsViewAssignment] = []

    for unit_code, _first_row, role_rows in _ops_vehicle_blocks(ws):
        unit_info = UNIT_MAP.get(unit_code)
        if not unit_info:
            continue
        base, service_type, _dn = unit_info
        for role_key, row_list in role_rows.items():
            role_label = role_key
            for row_idx in row_list:
                for col_idx, day_date in col_dates:
                    cell_val = ws.cell(row=row_idx, column=col_idx).value
                    staffed = _ops_cell_staffed(cell_val)
                    raw_s = _normalize_cell_value(cell_val)
                    if raw_s:
                        assignments.append(
                            OpsViewAssignment(
                                day_date=day_date,
                                unit_code=unit_code,
                                role=role_label,
                                excel_row=row_idx,
                                person_display=raw_s,
                                raw_value=raw_s,
                                is_staffed=staffed,
                            )
                        )
        for col_idx, day_date in col_dates:
            staffed_slot = _ops_staffed_for_column(
                ws,
                col_idx,
                service_type,
                role_rows.get("RN") or [],
                role_rows.get("Medic") or [],
                role_rows.get("PIC") or [],
                role_rows.get("EMT") or [],
            )
            if not staffed_slot:
                continue
            key = (day_date, base)
            if key not in day_base:
                day_base[key] = [0, 0]
            if service_type == "RW":
                day_base[key][0] += 1
            else:
                day_base[key][1] += 1

    day_rows = [
        OpsViewDayBase(
            day_date=day_date,
            base_name=base_name,
            rw_count=vals[0],
            gr_count=vals[1],
        )
        for (day_date, base_name), vals in sorted(day_base.items())
        if vals[0] or vals[1]
    ]
    return day_rows, assignments


def parse_ops_view_detail(
    path: str,
    week_start: str,
    wb: Workbook | None = None,
) -> tuple[list[OpsViewDayBase], list[OpsViewAssignment]]:
    """Load workbook and return OPS View day/base counts and assignments."""
    with _open_workbook(path, wb) as wb:
        sn = resolve_workbook_sheet(wb, "OPS View", "Ops View")
        if not sn:
            return [], []
        return _parse_ops_view_detail_worksheet(wb[sn], week_start)


# Sheet scan ranges for optional raw-cell archive (row_min, row_max, col_max).
_RAW_ARCHIVE_SHEETS: tuple[tuple[str, tuple[str, ...], int, int, int], ...] = (
    ("RN & Medic", ("RN & Medic", "RN AND MEDIC", "RN/MEDIC"), 1, 113, 16),
    ("EMT", ("EMT",), 1, 29, 16),
    ("OPS View", ("OPS View", "Ops View"), 1, 66, 16),
)


def collect_schedule_raw_cells(
    path: str,
    week_start: str,
    wb: Workbook | None = None,
) -> list[dict[str, object]]:
    """
    Non-empty cells from schedule grid areas for optional DB archive.

    Returns empty list when the workbook would exceed ``RAW_CELL_ARCHIVE_LIMIT``.
    """
    try:
        week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
    except ValueError:
        return []
    week_end_date = week_start_date + timedelta(days=6)

    cells: list[dict[str, object]] = []
    with _open_workbook(path, wb) as wb:
        for _label, name_variants, row_min, row_max, col_max in _RAW_ARCHIVE_SHEETS:
            sn = resolve_workbook_sheet(wb, *name_variants)
            if not sn:
                continue
            ws = wb[sn]
            # Header row (and therefore the set of columns belonging to this week)
            # is constant per sheet, so resolve it once instead of per cell.
            _hdr_row, header_dates = _best_header_row_in_ws(
                ws,
                week_start_date=week_start_date,
                week_end_date=week_end_date,
            )
            week_cols = {c for c, _ in header_dates}
            for row_idx in range(row_min, row_max + 1):
                for col_idx in range(1, col_max + 1):
                    val = ws.cell(row=row_idx, column=col_idx).value
                    if val is None:
                        continue
                    text = str(val).strip()
                    if not text:
                        continue
                    if col_idx >= 3 and week_cols and col_idx not in week_cols:
                        continue
                    cells.append(
                        {
                            "week_start": week_start,
                            "sheet_name": sn[:128],
                            "row_idx": row_idx,
                            "col_idx": col_idx,
                            "value_text": text[:512],
                        }
                    )
                    if len(cells) > RAW_CELL_ARCHIVE_LIMIT:
                        return []
        return cells


def find_schedule_workbook_for_week(
    week_start: str,
    search_dirs: Iterable[str],
) -> str | None:
    """Return the newest .xlsx in search_dirs whose dates include week_start."""
    candidates: list[tuple[float, str]] = []
    for directory in search_dirs:
        if not directory or not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.lower().endswith((".xlsx", ".xlsm")):
                continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            try:
                weeks = detect_schedule_week_starts(path)
            except Exception:
                continue
            if week_start in weeks:
                candidates.append((os.path.getmtime(path), path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def aggregate_week_from_records(
    week_start: str,
    records: Iterable[ShiftRecord],
    ops_coverage: tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]
    | None = None,
    ops_daily: dict[date, tuple[int, int]] | None = None,
) -> AggregatedWeek:
    """
    Aggregate parsed shift records into the fields needed for WeeklyStaffing
    and WeeklyBaseCoverage.

    - Filled Day/Night (crew shifts): for each (date, base, service_type, D/N,
      unit_code) where both RN and MEDIC are staffed, count one filled crew shift.
      If OPS View is present but the grid pairs nothing, filled day/night fall back to
      the sum of staffed RW/GR unit-days from OPS (matches base coverage).
    - OT by role + Day/Night: count overtime shifts (person-shifts).
    - Leave totals: AT, LT, SICK, LOA (PFML folded into LOA), JURY, BREV.
    - Base coverage: when ops_coverage is provided, use
      (rw_day, rw_night, gr_day, gr_night) from OPS View; legacy 2-tuple
      (rw_tot, gr_tot) is treated as all day / zero night.
      Otherwise derive from shift records: one RW unit-day per slot with RN+Medic;
      one GR unit-day per slot with RN+Medic+EMT (same rules as OPS View).
    """
    filled_day = filled_night = 0
    ot_rn_day = ot_rn_night = 0
    ot_medic_day = ot_medic_night = 0
    ot_emt_day = ot_emt_night = 0
    leave_at = leave_lt = leave_sick = leave_loa = leave_jury = leave_brev = 0
    training_total = 0
    leave_breakdown: dict[tuple[str, str], int] = {}
    base_rw_day: dict[str, int] = {}
    base_rw_night: dict[str, int] = {}
    base_gr_day: dict[str, int] = {}
    base_gr_night: dict[str, int] = {}
    # (date, base, service_type, day_night, slot) -> role flags for pairing.
    # slot = unit_code when set, else source_cell so distinct lines do not collapse.
    # EMT is included so GR unit-days match OPS View (RN + Medic + EMT).
    crew_roles: dict[tuple[date, str, ServiceType, DayNight, str], dict[str, bool]] = {}
    try:
        week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
    except ValueError:
        week_start_date = None
    week_days: list[date] = []
    daily_filled: dict[date, int] = {}
    daily_rw: dict[date, int] = {}
    daily_gr: dict[date, int] = {}
    daily_exc: dict[date, int] = {}
    if week_start_date is not None:
        week_days = [week_start_date + timedelta(days=i) for i in range(7)]
        daily_filled = {d: 0 for d in week_days}
        daily_rw = {d: 0 for d in week_days}
        daily_gr = {d: 0 for d in week_days}
        daily_exc = {d: 0 for d in week_days}

    for rec in records:
        if not rec.included_in_aggregates:
            continue
        if rec.skip_reason == "training":
            training_total += 1
            continue
        # Filled staffing: crew slots (RN + MEDIC paired); when not using OPS
        # View, derive base-level staffed unit-days from the same keys (below).
        if rec.filled and rec.day_night in {"D", "N"}:
            if rec.role in {"RN", "MEDIC", "EMT"} and rec.base and rec.service_type:
                slot = (rec.unit_code or "").strip() or rec.source_cell
                key = (rec.date, rec.base, rec.service_type, rec.day_night, slot)
                info = crew_roles.setdefault(
                    key, {"RN": False, "MEDIC": False, "EMT": False}
                )
                info[rec.role] = True

            # OT by role and day/night (person-shifts)
            if rec.overtime:
                if rec.role == "RN":
                    if rec.day_night == "D":
                        ot_rn_day += 1
                    else:
                        ot_rn_night += 1
                elif rec.role == "MEDIC":
                    if rec.day_night == "D":
                        ot_medic_day += 1
                    else:
                        ot_medic_night += 1
                elif rec.role == "EMT":
                    if rec.day_night == "D":
                        ot_emt_day += 1
                    else:
                        ot_emt_night += 1

        # Leave/absence totals and per-role breakdown (leave_type display).
        lt = rec.leave_type
        if lt and rec.date in daily_exc:
            daily_exc[rec.date] += 1
        if lt == "AT":
            leave_at += 1
        elif lt in ("LT-D", "LT-N", "LT"):
            leave_lt += 1
        elif lt == "SICK":
            leave_sick += 1
        elif lt in ("LOA", "PFML"):
            leave_loa += 1
        elif lt == "JURY":
            leave_jury += 1
        elif lt in ("BREV", "BERV", "BEREAVEMENT"):
            leave_brev += 1
        if lt:
            if rec.role == "MEDIC":
                role_display = "Medic"
            elif rec.role == "PILOT":
                role_display = "Pilot"
            else:
                role_display = rec.role
            display_type = "BREV" if lt in ("BREV", "BERV", "BEREAVEMENT") else lt
            key = (role_display, display_type)
            leave_breakdown[key] = leave_breakdown.get(key, 0) + 1

    # Convert crew_roles into filled crew shifts by day/night (RN + Medic only).
    for (day_date, _base_name, service_type, dn, _slot), roles in crew_roles.items():
        if roles.get("RN") and roles.get("MEDIC"):
            if dn == "D":
                filled_day += 1
            else:
                filled_night += 1
            if day_date in daily_filled:
                daily_filled[day_date] += 1
        if day_date in daily_rw:
            if service_type == "RW" and roles.get("RN") and roles.get("MEDIC"):
                daily_rw[day_date] += 1
            elif (
                service_type == "GR"
                and roles.get("RN")
                and roles.get("MEDIC")
                and roles.get("EMT")
            ):
                daily_gr[day_date] += 1

    # Base coverage without OPS View: one unit-day per staffed slot (not per person-row).
    # Aligns with OPS View — RW: RN+Medic; GR: RN+Medic+EMT.
    if ops_coverage is None:
        for (_d, base_name, service_type, dn, _slot), roles in crew_roles.items():
            if service_type == "RW":
                if not (roles.get("RN") and roles.get("MEDIC")):
                    continue
                if dn == "N":
                    base_rw_night[base_name] = base_rw_night.get(base_name, 0) + 1
                else:
                    base_rw_day[base_name] = base_rw_day.get(base_name, 0) + 1
            elif service_type == "GR":
                if not (roles.get("RN") and roles.get("MEDIC") and roles.get("EMT")):
                    continue
                if dn == "N":
                    base_gr_night[base_name] = base_gr_night.get(base_name, 0) + 1
                else:
                    base_gr_day[base_name] = base_gr_day.get(base_name, 0) + 1

    # Grid often has names or layout drift; OPS View still counts staffed vehicles.
    if (
        ops_coverage is not None
        and len(ops_coverage) >= 4
        and filled_day == 0
        and filled_night == 0
    ):
        ops_day = sum(ops_coverage[0].values()) + sum(ops_coverage[2].values())
        ops_night = sum(ops_coverage[1].values()) + sum(ops_coverage[3].values())
        if ops_day > 0 or ops_night > 0:
            filled_day = ops_day
            filled_night = ops_night

    if ops_coverage is not None:
        if len(ops_coverage) >= 4:
            base_rw_day = dict(ops_coverage[0])
            base_rw_night = dict(ops_coverage[1])
            base_gr_day = dict(ops_coverage[2])
            base_gr_night = dict(ops_coverage[3])
        else:
            base_rw_day = dict(ops_coverage[0])
            base_rw_night = {}
            base_gr_day = dict(ops_coverage[1])
            base_gr_night = {}
    else:
        _cap_base_coverage_split(
            base_rw_day,
            base_rw_night,
            base_gr_day,
            base_gr_night,
        )

    all_rw = set(base_rw_day) | set(base_rw_night)
    all_gr = set(base_gr_day) | set(base_gr_night)
    base_rw = {b: base_rw_day.get(b, 0) + base_rw_night.get(b, 0) for b in all_rw}
    base_gr = {b: base_gr_day.get(b, 0) + base_gr_night.get(b, 0) for b in all_gr}

    if ops_daily:
        for day_date, (rw, gr) in ops_daily.items():
            if day_date in daily_rw:
                daily_rw[day_date] = rw
                daily_gr[day_date] = gr

    if week_days and sum(daily_filled.values()) == 0 and ops_daily:
        for day_date, (rw, gr) in ops_daily.items():
            if day_date in daily_filled:
                daily_filled[day_date] = rw + gr

    daily_detail = [
        DailyDetailDay(
            day_date=day_date,
            filled=daily_filled.get(day_date, 0),
            rw=daily_rw.get(day_date, 0),
            gr=daily_gr.get(day_date, 0),
            exceptions=daily_exc.get(day_date, 0),
        )
        for day_date in week_days
    ]

    return AggregatedWeek(
        week_start=week_start,
        filled_day=filled_day,
        filled_night=filled_night,
        ot_rn_day=ot_rn_day,
        ot_rn_night=ot_rn_night,
        ot_medic_day=ot_medic_day,
        ot_medic_night=ot_medic_night,
        ot_emt_day=ot_emt_day,
        ot_emt_night=ot_emt_night,
        leave_at=leave_at,
        leave_lt=leave_lt,
        leave_sick=leave_sick,
        leave_loa=leave_loa,
        leave_jury=leave_jury,
        leave_brev=leave_brev,
        training_total=training_total,
        leave_breakdown=leave_breakdown,
        base_rw_staffed=base_rw,
        base_gr_staffed=base_gr,
        base_rw_staffed_day=base_rw_day,
        base_rw_staffed_night=base_rw_night,
        base_gr_staffed_day=base_gr_day,
        base_gr_staffed_night=base_gr_night,
        daily_detail=daily_detail,
    )
