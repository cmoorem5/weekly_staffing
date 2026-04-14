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

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

Role = str  # "RN", "MEDIC", "EMT", "PILOT"
ServiceType = str  # "RW" or "GR"
DayNight = str  # "D" or "N"


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
    "D11B": ("Bedford", "RW", "D"),
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
}

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

LEAVE_CODES = {"AT", "LT", "SICK", "LOA", "RN", "EMT", "PFML", "JURY", "BREV"}

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
# RN & Medic: OPEN/EXTRA rows + footer block — skip schedule grid B–P (cols 2–16).
RN_MEDIC_SKIP_SCHEDULE_ROWS = frozenset({45, 46, 91, 92, *range(95, 114)})
_SCHEDULE_COL_B = 2
_SCHEDULE_COL_P = 16

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

# Names (column A) to exclude from leave/exception totals (managers).
# Comparison is case-insensitive, stripped.
IGNORE_LEAVE_NAMES: set[str] = {
    # Medic managers
    "Ahlstedt",
    "Denison",
    "Doherty",
    "Ender",
    "Estanislao",
    "Holst",
    "Kadow",
    "Moore",
    "Powers",
    # RN managers
    "Bowman",
    "Farkas",
    "Frakes",
    "Muszalski",
    "Steckevicz",
    "Wallace",
}

_IGNORE_LEAVE_LAST_NAMES_UPPER: frozenset[str] = frozenset(
    name.strip().upper() for name in IGNORE_LEAVE_NAMES if name
)


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


def _skip_leave_row_for_manager(ws: Worksheet, row_idx: int) -> bool:
    """True if this row should not count toward leave (manager row)."""
    if not _IGNORE_LEAVE_LAST_NAMES_UPPER:
        return False
    tok = _name_tokens_for_grid_row(ws, row_idx)
    return bool(tok & _IGNORE_LEAVE_LAST_NAMES_UPPER)


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


def _split_unit_suffix(code: str) -> tuple[str, bool, bool]:
    """
    Split a unit-like code into (base_unit, is_ot, is_dual_role).

    Strip trailing 'C' (OT) and 'P' (dual-role) only when the remainder is in
    UNIT_MAP, so D7P/N7P (Plymouth) are preserved. If core is already in
    UNIT_MAP, do not strip. E.g. N7PC -> N7P (strip C); D7PP -> D7P (strip P);
    D7P -> D7P (no strip).

    Also supports " C" (space + C) for OT merge: D7P C, D7B C etc. → overtime.
    """
    is_ot = False
    is_dual = False
    core = code

    # Merge: "UNIT C" or "UNIT C" (space + C) → OT when base unit is known
    for suffix in OT_SUFFIXES:
        if core.endswith(suffix):
            candidate = core[: -len(suffix)].strip()
            if candidate in UNIT_MAP:
                core = candidate
                is_ot = True
                break

    while len(core) > 1 and core[-1] in {"C", "P"}:
        if core in UNIT_MAP:
            break
        last = core[-1]
        candidate = core[:-1]
        if candidate in UNIT_MAP:
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
    # Exact match first
    if code in UNIT_MAP:
        return UNIT_MAP[code]
    return None


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
) -> tuple[list[ShiftRecord], list[ParseIssue]]:
    """Parse a rectangular RN/Medic/EMT grid into shift records + issues.

    unit_overrides: map raw_value -> replacement
    (e.g. {"D7BCP": "D7BC", "TYPO": "D7B/LT"}).
    Use at import to fix typos or merge unknown codes.
    """
    records: list[ShiftRecord] = []
    issues: list[ParseIssue] = []
    overrides = unit_overrides or {}

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

    for row_idx in range(first_row_idx, last_row_idx + 1):
        for col_idx, d in col_dates:
            if (
                sheet_label.startswith("RN & Medic")
                and row_idx in RN_MEDIC_SKIP_SCHEDULE_ROWS
                and _SCHEDULE_COL_B <= col_idx <= _SCHEDULE_COL_P
            ):
                continue
            cell = ws.cell(row=row_idx, column=col_idx)
            raw = cell.value
            text = _normalize_cell_value(raw)
            if not text:
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

            # Non-staffing / training / manager markers
            # (no staffed, no leave, no issue).
            if text in SKIP_CELL_VALUES:
                continue

            # Weekday labels (e.g. row 2: SUN, MON, TUE, …) — ignore.
            if text in {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"}:
                continue

            # Skip ignored unit-like codes (no shift, no issue).
            if text in IGNORE_UNIT_CODES:
                continue

            # EMT: PER → LT (same bucket as leave time for exception grid).
            if role == "EMT" and text == "PER":
                if _skip_leave_row_for_manager(ws, row_idx):
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
                    )
                )
                continue

            # Merge: UNIT/LEAVETYPE → leave (any unit + any leave type)
            if "/" in text:
                parts = text.split("/", 1)
                suffix = (parts[1] or "").strip()
                leave_display = UNIT_LEAVE_MERGE.get(suffix)
                if leave_display is not None:
                    if _skip_leave_row_for_manager(ws, row_idx):
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
                # Skip manager rows (last-name match in A/B).
                if _skip_leave_row_for_manager(ws, row_idx):
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
                    )
                )
                continue

            base_code, is_ot, is_dual = _split_unit_suffix(text)
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
                    unit_code=base_code,
                )
            )

    return records, issues


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
        if text_a and text_a in UNIT_MAP:
            if current_unit is not None:
                blocks.append((current_unit, current_start, role_rows))
            current_unit = text_a
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
            rn_ok = any(
                _ops_cell_staffed(ws.cell(row=r, column=col_idx).value) for r in rn_rows
            )
            medic_ok = any(
                _ops_cell_staffed(ws.cell(row=r, column=col_idx).value)
                for r in medic_rows
            )
            pic_ok = any(
                _ops_cell_staffed(ws.cell(row=r, column=col_idx).value)
                for r in pic_rows
            )
            emt_ok = any(
                _ops_cell_staffed(ws.cell(row=r, column=col_idx).value)
                for r in emt_rows
            )

            if service_type == "RW":
                staffed = rn_ok and medic_ok and (pic_ok or emt_ok)
                if staffed:
                    if dn == "N":
                        base_rw_night[base] = base_rw_night.get(base, 0) + 1
                    else:
                        base_rw_day[base] = base_rw_day.get(base, 0) + 1
            elif service_type == "GR":
                staffed = rn_ok and medic_ok and emt_ok
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

    Assumptions (based on your 15 Feb 2026 layout):
    - Sheet 'RN & Medic': RN lines ~4–50, Medic ~52–100 (dates often row 2).
    - Date row may be 1–5 (not always row 1). EMT tab mirrors RN grid.
    """
    wb = load_workbook(path, data_only=True)

    week_start_date: date | None = None
    week_end_date: date | None = None
    if week_start:
        try:
            week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
            week_end_date = week_start_date + timedelta(days=6)
        except ValueError:
            pass

    records: list[ShiftRecord] = []
    issues: list[ParseIssue] = []

    try:
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
    finally:
        wb.close()


# --- Aggregation into weekly metrics ---------------------------------------


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
    # (role, leave_type) -> count for grid (RN/Medic/EMT/Pilot × leave types).
    leave_breakdown: dict[tuple[str, str], int]
    base_rw_staffed: dict[str, int]
    base_gr_staffed: dict[str, int]
    base_rw_staffed_day: dict[str, int]
    base_rw_staffed_night: dict[str, int]
    base_gr_staffed_day: dict[str, int]
    base_gr_staffed_night: dict[str, int]


def aggregate_week_from_records(
    week_start: str,
    records: Iterable[ShiftRecord],
    ops_coverage: tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]
    | None = None,
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
    leave_breakdown: dict[tuple[str, str], int] = {}
    base_rw_day: dict[str, int] = {}
    base_rw_night: dict[str, int] = {}
    base_gr_day: dict[str, int] = {}
    base_gr_night: dict[str, int] = {}
    # (date, base, service_type, day_night, slot) -> role flags for pairing.
    # slot = unit_code when set, else source_cell so distinct lines do not collapse.
    # EMT is included so GR unit-days match OPS View (RN + Medic + EMT).
    crew_roles: dict[tuple[date, str, ServiceType, DayNight, str], dict[str, bool]] = {}

    for rec in records:
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
    for (_d, _base_name, _service_type, dn, _slot), roles in crew_roles.items():
        if roles.get("RN") and roles.get("MEDIC"):
            if dn == "D":
                filled_day += 1
            else:
                filled_night += 1

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
        leave_breakdown=leave_breakdown,
        base_rw_staffed=base_rw,
        base_gr_staffed=base_gr,
        base_rw_staffed_day=base_rw_day,
        base_rw_staffed_night=base_rw_night,
        base_gr_staffed_day=base_gr_day,
        base_gr_staffed_night=base_gr_night,
    )
