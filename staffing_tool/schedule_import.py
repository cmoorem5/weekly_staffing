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

This module is the public façade: it keeps ``parse_schedule_workbook`` (the
orchestrator) and the raw-cell archive, and re-exports every name from the
modules the pipeline was split into — import from here, not the submodules:

- ``schedule_types``    dataclasses + type aliases (ShiftRecord, ParseIssue, …)
- ``schedule_cells``    unit/leave/training/skip code tables + cell classification
- ``schedule_workbook`` workbook opening, sheet + header-row/date detection
- ``schedule_grid``     the RN/Medic/EMT grid walker (_parse_grid)
- ``schedule_ops_view`` OPS View weekly/daily/detail parsing + coverage caps
- ``schedule_aggregate`` weekly rollups + per-person/manager shift mappings
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from openpyxl.workbook import Workbook

from .manager_roster import default_manager_last_names_upper
from .schedule_aggregate import (  # noqa: F401
    _person_shift_event_type,
    aggregate_week_from_records,
    weekly_manager_shift_mappings,
    weekly_person_shift_mappings,
)
from .schedule_cells import (  # noqa: F401
    _OT_C_VARIANTS,
    AT_ALIASES,
    IGNORE_UNIT_CODES,
    LEAVE_CODES,
    LEGACY_UNIT_ALIASES,
    MAX_GR_UNIT_DAYS_PER_WEEK,
    MAX_RW_UNIT_DAYS_PER_WEEK,
    OT_SUFFIXES,
    RETIRED_UNIT_CODES,
    SKIP_ADMIN_VALUES,
    SKIP_CELL_VALUES,
    SKIP_TRAINING_VALUES,
    UNIT_LEAVE_MERGE,
    UNIT_MAP,
    _canonical_unit_code,
    _classify_skip_reason,
    _classify_unit,
    _is_resolvable_unit,
    _normalize_cell_value,
    _split_unit_suffix,
)
from .schedule_grid import (  # noqa: F401
    _SCHEDULE_COL_B,
    _SCHEDULE_COL_P,
    NON_PERSON_ROW_LABELS,
    _append_skipped_shift,
    _find_non_person_skip_row,
    _grid_name_cells,
    _name_tokens_for_grid_row,
    _parse_grid,
    _row_person_displays_and_manager_flag,
)
from .schedule_ops_view import (  # noqa: F401
    _OPS_ROLE_EMT,
    _OPS_ROLE_MEDIC,
    _OPS_ROLE_PIC,
    _OPS_ROLE_RN,
    _cap_base_coverage_split,
    _ops_cell_staffed,
    _ops_parse_dates_row,
    _ops_role_label,
    _ops_staffed_for_column,
    _ops_vehicle_blocks,
    _parse_ops_view_daily_worksheet,
    _parse_ops_view_detail_worksheet,
    _parse_ops_view_worksheet,
    parse_ops_view,
    parse_ops_view_daily,
    parse_ops_view_detail,
)
from .schedule_types import (  # noqa: F401
    AggregatedWeek,
    DailyDetailDay,
    DayNight,
    OpsViewAssignment,
    OpsViewDayBase,
    ParseIssue,
    Role,
    ServiceType,
    ShiftRecord,
    SkipReason,
    _shift_record_persons,
)
from .schedule_workbook import (  # noqa: F401
    _best_header_row_in_ws,
    _header_cell_to_date,
    _iter_date_headers,
    _normalize_sheet_title,
    _open_workbook,
    detect_schedule_week_starts,
    find_schedule_workbook_for_week,
    resolve_workbook_sheet,
    schedule_file_sha256,
)

PARSER_VERSION = "2"

# Max non-empty cells archived per week (SQLite-friendly).
RAW_CELL_ARCHIVE_LIMIT = 5000


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
